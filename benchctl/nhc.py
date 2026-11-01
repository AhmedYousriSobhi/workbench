"""Manage the node health checks (C-15, ADR 0019): the files in images/node-base/nhc.d, ordered and switched by checks.conf.

One module behind the three ways to change them: the CLI (`benchctl add|set|remove check`), a hand edit followed by
`benchctl reload checks`, and the Web UI's Health checks sheet. Every change is made on a copy, validated (grammar, names,
parameters, `bash -n`, the check contract, a smoke run on healthy metrics) and only then written, so a refused change leaves
the files as they were. `sync` copies the result into compose/generated/nhc, which the nodes mount read-only: the runner
(`images/node-base/nhc`) reads it at every run, so a change reaches a running node within one health-check interval.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

SRC = Path("images/node-base/nhc.d")
OUT = Path("compose/generated/nhc")
RUNNER = Path(__file__).resolve().parent.parent / "images" / "node-base" / "nhc"
CONF = "checks.conf"
NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
KEY = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
VALUE = re.compile(r"^[A-Za-z0-9_.:,/-]{1,64}$")  # nothing a shell or awk program could read as code
MAX_BYTES = 16384
CHECK_SECONDS = 10  # keep equal to the `timeout` in images/node-base/nhc
HEADER = (
    "# NHC checks, run top to bottom by images/node-base/nhc. One line per check: NAME on|off [key=value ...]\n"
    "# NAME is the file NAME.sh in this directory. Manage it with `benchctl add|set|remove check`, or edit it and run\n"
    "# `benchctl reload checks` (docs/guides/nhc-checks.md).\n"
)


class NhcError(ValueError):
    """A change or a file that the checks cannot accept; the message says what and where."""


@dataclass
class Check:
    name: str
    enabled: bool = True
    params: dict[str, str] = field(default_factory=dict)
    description: str = ""
    declared: list[str] = field(default_factory=list)
    template: str = ""  # the template it was made from; empty for the shipped checks and custom scripts

    def to_dict(self) -> dict:
        return {"name": self.name, "enabled": self.enabled, "params": dict(self.params), "description": self.description,
                "declared": list(self.declared), "template": self.template}


def _header(text: str) -> tuple[str, list[str], str]:
    """(description, declared parameter names, template) from the leading comment lines of a check file."""
    desc, declared, template = "", [], ""
    for line in text.splitlines():
        if not line.startswith("#"):
            if line.strip():
                break
            continue
        body = line[1:].strip()
        for tag in ("check:", "params:", "template:"):
            if body.startswith(tag):
                value = body[len(tag):].strip()
                if tag == "check:" and not desc:
                    desc = value
                elif tag == "params:":
                    declared = value.split()
                elif tag == "template:":
                    template = value
    return desc, declared, template


def _describe(name: str, path: Path, enabled: bool, params: dict[str, str]) -> Check:
    text = path.read_text() if path.is_file() else ""
    desc, declared, template = _header(text)
    return Check(name, enabled, params, desc, declared, template)


def parse_conf(text: str, src: Path = SRC) -> list[Check]:
    """The ordered checks of a checks.conf. Raises NhcError naming the line for anything the runner would misread."""
    checks: list[Check] = []
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        name, state, rest = parts[0], (parts[1] if len(parts) > 1 else ""), parts[2:]
        where = f"{CONF} line {no}"
        if not NAME.match(name):
            raise NhcError(f"{where}: '{name}' is not a check name (lower case letters, digits and '-', starting with a letter)")
        if state not in ("on", "off"):
            raise NhcError(f"{where}: {name} needs 'on' or 'off' after the name, not '{state}'")
        if any(c.name == name for c in checks):
            raise NhcError(f"{where}: {name} is listed twice")
        params: dict[str, str] = {}
        for token in rest:
            key, eq, value = token.partition("=")
            if not eq or not KEY.match(key):
                raise NhcError(f"{where}: '{token}' is not key=value (key: lower case letters, digits, '_')")
            if not VALUE.match(value):
                raise NhcError(f"{where}: the value of {key} must be 1 to 64 letters, digits or _ . : , / - (no spaces)")
            params[key] = value
        checks.append(_describe(name, src / f"{name}.sh", state == "on", params))
    return checks


def load(src: Path = SRC) -> list[Check]:
    conf = src / CONF
    if not conf.is_file():
        raise NhcError(f"{conf} is missing")
    return parse_conf(conf.read_text(), src)


def render(checks: list[Check]) -> str:
    lines = [f"{c.name} {'on' if c.enabled else 'off'}" + "".join(f" {k}={v}" for k, v in c.params.items()) for c in checks]
    return HEADER + "\n".join(lines) + "\n"


def templates(src: Path = SRC) -> list[dict]:
    out = []
    for path in sorted((src / "templates").glob("*.sh")):
        desc, declared, _ = _header(path.read_text())
        out.append({"name": path.stem, "description": desc, "declared": declared})
    return out


def _bash_n(path: Path) -> str:
    r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, timeout=10, check=False)
    return r.stderr.strip().replace(str(path), path.name) if r.returncode else ""


def _healthy_metrics(node: str, gpus: int, ib: int) -> str:
    lines = [f'DCGM_FI_DEV_GPU_TEMP{{node="{node}",gpu="{g}",Hostname="{node}"}} 40.0' for g in range(gpus)]
    lines += [f'DCGM_FI_DEV_XID_ERRORS{{node="{node}",gpu="{g}",Hostname="{node}"}} 0' for g in range(gpus)]
    lines += [f'node_infiniband_state_id{{node="{node}",Hostname="{node}",device="mlx5_{i}",port="1"}} 4' for i in range(ib)]
    lines.append(f'node_network_carrier_changes_total{{node="{node}",device="eth0"}} 1')
    return "\n".join(lines) + "\n"


def smoke(check: Check, src: Path = SRC, runner: Path = RUNNER) -> str:
    """Run the runner with only this check on, against healthy sample metrics. A check that reports a problem there (or
    fails, or hangs) would drain every healthy node, so it is refused. Returns '' when it passes, else the reason."""
    node = "smoke001"
    with tempfile.TemporaryDirectory(prefix="nhc-smoke-") as tmp:
        work = Path(tmp)
        (work / "checks").mkdir()
        shutil.copy(src / f"{check.name}.sh", work / "checks")
        (work / "checks" / CONF).write_text(render([Check(check.name, True, check.params)]))
        (work / "m.txt").write_text(_healthy_metrics(node, 2, 2))
        calls = work / "calls.txt"
        stub = work / "scontrol"
        stub.write_text(f'#!/bin/sh\nif [ "$1" != show ]; then echo "$@" >> {calls}; fi\n')
        stub.chmod(0o755)
        env = {**os.environ, "NHC_METRICS_FILE": str(work / "m.txt"), "NHC_SCONTROL": str(stub), "NHC_NODE": node,
               "NHC_EXPECTED_GPUS": "2", "NHC_EXPECTED_IB": "2", "NHC_STATE_DIR": str(work), "NHC_CHECKS_DIR": str(work / "checks"),
               "NHC_ENV_FILE": str(work / "none")}
        started = time.monotonic()
        try:
            r = subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True, timeout=30, check=False)
        except subprocess.TimeoutExpired:
            return f"did not finish within 30 s on sample metrics (a check may run for {CHECK_SECONDS} s)"
        if time.monotonic() - started >= CHECK_SECONDS:  # the runner kills it and carries on, so it would never report anything
            return f"takes {CHECK_SECONDS} s or longer on sample metrics; the runner stops a check after {CHECK_SECONDS} s"
        if r.returncode != 0 or calls.exists():
            said = (r.stdout.strip().splitlines() or ["unhealthy"])[-1]
            return f"reports a problem on healthy sample metrics ({said}); a check must print nothing for a healthy node"
    return ""


def check_problems(check: Check, src: Path = SRC, with_smoke: bool = True) -> list[str]:
    """Everything wrong with one check: its file, its parameters, its contract and (last) a smoke run."""
    path, out = src / f"{check.name}.sh", []
    if not path.is_file():
        return [f"{check.name}: {check.name}.sh does not exist in {src}"]
    text = path.read_text()
    if not text.strip():
        return [f"{check.name}: {path.name} is empty"]
    if len(text.encode()) > MAX_BYTES:
        return [f"{check.name}: {path.name} is larger than {MAX_BYTES} bytes"]
    if err := _bash_n(path):
        return [f"{check.name}: syntax error: {err}"]
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    if not code.strip():
        return [f"{check.name}: {path.name} has only comments: a check needs at least one command"]
    if re.search(r"(^|[;&|(]\s*|\s)(scontrol|sudo)\s", code, re.MULTILINE):
        out.append(f"{check.name}: a check reports a problem by printing a line (add \"reason\"); the runner drains the node, "
                   "so the script must not call scontrol or sudo")
    declared = set(check.declared)
    missing = sorted(declared - set(check.params))
    unknown = sorted(set(check.params) - declared)
    if missing:
        out.append(f"{check.name}: needs {', '.join(missing)} (benchctl set check {check.name} {' '.join(f'--set {k}=VALUE' for k in missing)})")
    if unknown:
        out.append(f"{check.name}: does not take {', '.join(unknown)} (the file declares: {' '.join(check.declared) or 'no parameters'}; "
                   "declare one with a '# params: a b' line)")
    if out or not with_smoke:
        return out
    if reason := smoke(check, src):
        out.append(f"{check.name}: {reason}")
    return out


def problems(src: Path = SRC, with_smoke: bool = True) -> list[str]:
    """All problems of the whole directory; empty when it is safe to deliver to the nodes."""
    try:
        checks = load(src)
    except NhcError as e:
        return [str(e)]
    listed = {c.name for c in checks}
    out = [f"{p.name} is not listed in {CONF}: add a line '{p.stem} on' or delete the file"
           for p in sorted(src.glob("*.sh")) if p.stem not in listed]
    for c in checks:
        out += check_problems(c, src, with_smoke)
    return out


# --- changes: each is made on a copy, validated, then written -----------------------------------------------------------

def _stage(src: Path) -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory(prefix="nhc-stage-")
    work = Path(tmp.name) / "nhc.d"
    shutil.copytree(src, work)
    return tmp, work


def _commit(work: Path, src: Path) -> None:
    """Write the validated copy back: only files that differ are replaced (each atomically), vanished ones removed."""
    for path in list(src.glob("*.sh")) + [src / CONF]:
        if not (work / path.name).exists():
            path.unlink(missing_ok=True)
    for path in list(work.glob("*.sh")) + [work / CONF]:
        dest = src / path.name
        if not dest.exists() or dest.read_bytes() != path.read_bytes():
            _write_atomic(dest, path.read_bytes())


def _write_atomic(dest: Path, data: bytes) -> None:
    tmp = dest.with_name(f".{dest.name}.tmp")
    tmp.write_bytes(data)
    tmp.chmod(0o644)
    os.replace(tmp, dest)


def _change(src: Path, mutate) -> list[Check]:
    tmp, work = _stage(src)
    try:
        checks = load(work)
        checks = mutate(checks, work) or checks
        (work / CONF).write_text(render(checks))
        if errs := problems(work):
            raise NhcError("\n".join(errs))
        _commit(work, src)
        return checks
    finally:
        tmp.cleanup()


def _find(checks: list[Check], name: str) -> Check:
    for c in checks:
        if c.name == name:
            return c
    raise NhcError(f"no check named '{name}' (benchctl get checks)")


def parse_sets(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        key, eq, value = pair.partition("=")
        if not eq:
            raise NhcError(f"--set wants key=value, not '{pair}'")
        out[key] = value
    return out


def _param_errors(params: dict[str, str] | None) -> None:
    for key, value in (params or {}).items():
        if not KEY.match(key):
            raise NhcError(f"'{key}' is not a parameter name (lower case letters, digits, '_')")
        if not VALUE.match(value):
            raise NhcError(f"the value of {key} must be 1 to 64 letters, digits or _ . : , / - (no spaces)")


def _place(checks: list[Check], check: Check, position: int | None) -> None:
    checks.remove(check)
    index = len(checks) if position is None else max(0, min(position - 1, len(checks)))
    checks.insert(index, check)


def add(name: str, *, template: str | None = None, script: str | None = None, params: dict[str, str] | None = None,
        position: int | None = None, src: Path = SRC) -> list[Check]:
    """Add a check from a template (with its parameters) or from the text of a custom script."""
    if (template is None) == (script is None):
        raise NhcError("give exactly one of a template or a script")
    if not NAME.match(name):
        raise NhcError(f"'{name}' is not a check name (lower case letters, digits and '-', starting with a letter)")
    _param_errors(params)

    def mutate(checks: list[Check], work: Path):
        if any(c.name == name for c in checks) or (work / f"{name}.sh").exists():
            raise NhcError(f"a check named '{name}' exists (benchctl set check {name} ..., or remove it first)")
        if template is not None:
            tpl = work / "templates" / f"{template}.sh"
            if not tpl.is_file():
                raise NhcError(f"no template '{template}' (have: {', '.join(t['name'] for t in templates(work)) or 'none'})")
            body = tpl.read_text()
            body = body.replace("# check:", f"# template: {template}\n# check:", 1) if "# check:" in body else f"# template: {template}\n{body}"
        else:
            body = script if script.endswith("\n") else script + "\n"
            if "# check:" not in body:
                body = f"# check: {name} (custom)\n{body}"
        (work / f"{name}.sh").write_text(body)
        check = _describe(name, work / f"{name}.sh", True, params or {})
        checks.append(check)
        if position is not None:
            _place(checks, check, position)

    return _change(src, mutate)


def set_check(name: str, *, enabled: bool | None = None, params: dict[str, str] | None = None, position: int | None = None,
              src: Path = SRC) -> list[Check]:
    _param_errors(params)

    def mutate(checks: list[Check], work: Path):
        check = _find(checks, name)
        if enabled is not None:
            check.enabled = enabled
        if params:
            check.params.update(params)
        if position is not None:
            _place(checks, check, position)

    return _change(src, mutate)


def remove(name: str, src: Path = SRC) -> list[Check]:
    """Remove a check and its file (git has the old one)."""
    def mutate(checks: list[Check], work: Path):
        checks.remove(_find(checks, name))
        (work / f"{name}.sh").unlink(missing_ok=True)

    return _change(src, mutate)


def reorder(names: list[str], src: Path = SRC) -> list[Check]:
    """Set the whole order at once (the page's drag and drop); `names` must be exactly the existing checks."""
    def mutate(checks: list[Check], work: Path):
        by = {c.name: c for c in checks}
        if sorted(names) != sorted(by):
            raise NhcError("the order must list every check exactly once")
        return [by[n] for n in names]

    return _change(src, mutate)


def sync(src: Path = SRC, out: Path = OUT) -> list[str]:
    """Validate, then copy checks.conf and the checks into `out` (what the nodes mount). Files are replaced in place: a
    bind-mounted directory must keep its identity, so the directory itself is never swapped. Returns the problems
    (nothing is copied when there are any)."""
    if errs := problems(src):
        return errs
    out.mkdir(parents=True, exist_ok=True)
    names = {CONF} | {p.name for p in src.glob("*.sh")}
    for name in names:
        data = (src / name).read_bytes()
        if not (out / name).exists() or (out / name).read_bytes() != data:
            _write_atomic(out / name, data)
    for stale in out.iterdir():
        if stale.name not in names and stale.is_file():
            stale.unlink()
    out.chmod(0o755)
    return []


def state(src: Path = SRC) -> dict:
    """What the page shows: the ordered checks, the templates and the problems of the files as they are."""
    try:
        checks = [c.to_dict() for c in load(src)]
    except NhcError as e:
        return {"checks": [], "templates": templates(src), "problems": [str(e)]}
    return {"checks": checks, "templates": templates(src), "problems": problems(src, with_smoke=False)}
