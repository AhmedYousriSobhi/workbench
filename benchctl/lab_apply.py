"""Config lab apply and remove (issue 553, ADR 0049, SPEC C-24).

`apply` puts one side of a lab (baseline or candidate) on the running cluster; `remove` returns to the plain `sim.yaml` cluster.
Both run only through the governor (`lab-apply`, `lab-restore`: L0-L1, one at a time, audited), which calls
`benchctl apply lab NAME --side S --execute` / `benchctl remove lab NAME --execute`; nothing here talks to production.

What is applied (owner decisions of 2026-11-15):
- slurm.conf keys of class policy, time-scaled and restart (`lab_slurm.KEY_CLASS`), times unscaled (`time_scale` 1.0 in the marker).
  Unknown keys are NOT applied (reported); lab-owned keys keep the base value; node and partition structure waits for the run (#554).
- QOS and accounts through `sacctmgr`, Prometheus rules (a lab rules directory, SIGHUP reload), Kubernetes policy objects
  (label `workbench.lab=NAME`), Teleport roles (`tctl create -f`).

State: `compose/generated/lab/active.json` (the marker) and `compose/generated/lab/NAME/render/` (pre-lab `scontrol show config`,
`sacctmgr dump` before and after, the rendered slurm.conf keys). The slurm.conf writer re-applies the marker on every `up` and `scale`;
`down` keeps it; only `remove lab` ends a lab. Every refusal happens before the first change.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import lab, lab_candidate, lab_import, lab_slurm, lab_validate
from .lab import LabError

MARKER = "active.json"
LABEL = "workbench.lab"
CLUSTER = "workbench"
RULES_REL = "prometheus/rules/lab"  # under compose/generated; mounted at /etc/prometheus/generated/rules/lab (prometheus.yml globs *.yml)
APPLIED_CLASSES = ("policy", "time-scaled", "restart")
QOS_FIELDS = ("Priority", "GraceTime", "PreemptMode", "Preempt", "PreemptExemptTime", "MaxTRESPerUser", "MaxTRESPerJob", "MaxTRESPerAccount",
              "MaxJobsPerUser", "MaxJobsPerAccount", "MaxSubmitJobsPerUser", "MaxSubmitJobsPerAccount", "MaxWall", "UsageFactor", "Flags",
              "GrpTRES", "GrpJobs", "GrpSubmitJobs", "MinTRES", "MaxTRESMins")
BEGIN, END = "# BEGIN lab {name} (benchctl apply lab; benchctl remove lab ends it)", "# END lab {name}"
_WAS = re.compile(r"^(\s*[A-Za-z]\w*=).*? # lab-was (.*)$")


def _run(cmd: list[str], input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=input, capture_output=True, text=True, check=False, timeout=600)


# ---------------------------------------------------------------- marker

def marker_path(root: Path | None = None) -> Path:
    return (root or lab.ROOT) / MARKER


def read_marker(root: Path | None = None) -> dict | None:
    p = marker_path(root)
    try:
        m = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) and isinstance(m.get("name"), str) else None


def write_marker(m: dict, root: Path | None = None) -> None:
    p = marker_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(MARKER + ".tmp")
    tmp.write_text(json.dumps(m, indent=2) + "\n")
    tmp.replace(p)


def status_block(root: Path | None = None) -> dict | None:
    """What governor `status` and the page show: which lab is on the cluster, so nobody tests the plain cluster by mistake."""
    m = read_marker(root)
    return None if m is None else {"name": m["name"], "side": m.get("side"), "time_scale": m.get("time_scale", 1.0), "state": "applied"}


# ---------------------------------------------------------------- slurm.conf overlay

def _quote(v: str) -> str:
    return f'"{v}"' if re.search(r"\s", v) and not v.startswith('"') else v


def strip_overlay(text: str) -> str:
    """The generated slurm.conf as it was before any overlay: lines replaced in place are restored, the block is dropped."""
    out, skip = [], False
    for line in text.splitlines():
        if line.startswith("# BEGIN lab "):
            skip = True
        elif line.startswith("# END lab "):
            skip = False
        elif not skip:
            m = _WAS.match(line)
            out.append(m.group(1) + m.group(2) if m else line)
    return "\n".join(out) + "\n"


def overlay_text(text: str, keys: dict[str, str], name: str) -> str:
    """Put `keys` on top of the generated text: a key on its own line is replaced in place (the old value kept in a trailing comment so
    `strip_overlay` can restore it); any other key goes in a block at the end."""
    lines = strip_overlay(text).splitlines()
    added: list[str] = []
    for k, v in keys.items():
        idx = [i for i, ln in enumerate(lines) if re.fullmatch(rf"\s*{re.escape(k)}=\S*", ln, re.IGNORECASE)]
        if len(idx) == 1:
            old = lines[idx[0]].split("=", 1)[1]
            if old != _quote(v):
                lines[idx[0]] = f"{k}={_quote(v)} # lab-was {old}"
        else:
            added.append(f"{k}={_quote(v)}")
    if added:
        lines += [BEGIN.format(name=name), *added, END.format(name=name)]
    return "\n".join(lines) + "\n"


def reapply(slurm_dir: Path, root: Path | None = None) -> str | None:
    """Called by the slurm.conf writer after it generated the files: the active lab's keys go back on top. Returns the lab's name."""
    m = read_marker(root if root is not None else slurm_dir.parent / "lab")
    conf = slurm_dir / "slurm.conf"
    if not m or not conf.is_file():
        return None
    conf.write_text(overlay_text(conf.read_text(), m.get("keys", {}), m["name"]))
    return m["name"]


def note_active(say: Callable[[str], None], root: Path | None = None) -> None:
    m = read_marker(root)
    if m:
        say(f"lab {m['name']} is active: its settings were re-applied")


def render_slurm(name: str, side: str, root: Path | None = None) -> dict:
    """{keys: applied, unknown: [...], lab_owned: [...]} for the side's slurm.conf; nothing for a lab without one."""
    text = lab_candidate.effective(name, side, root).get("slurm.conf")
    if not text:
        return {"keys": {}, "unknown": [], "lab_owned": []}
    try:
        keys = lab_import.parse_slurm_conf(text)["keys"]
    except lab_import.ImportError_ as e:
        raise LabError(f"{side} slurm.conf: {e}") from e
    applied = {k: v for k, v in keys.items() if lab_slurm.classify(k) in APPLIED_CLASSES}
    return {"keys": applied, "unknown": sorted(k for k in keys if lab_slurm.classify(k) == "unknown"),
            "lab_owned": sorted(k for k in keys if lab_slurm.classify(k) == "lab-owned")}


def conf_keys(text: str) -> dict[str, str]:
    try:
        return lab_import.parse_slurm_conf(text)["keys"]
    except lab_import.ImportError_:
        return {}


def changed_keys(new: dict[str, str], live: dict[str, str], previous: dict[str, str]) -> list[str]:
    """Keys whose live value differs from the new one, plus keys of the previous side that fall away (their base value returns)."""
    lower = {k.lower(): v for k, v in live.items()}
    out = [k for k, v in new.items() if lower.get(k.lower()) != v]
    return out + [k for k in previous if k not in new]


# ---------------------------------------------------------------- scontrol show config

VOLATILE_SAFE = {k.lower() for k in lab_slurm.KEY_CLASS if lab_slurm.KEY_CLASS[k] in APPLIED_CLASSES}


def parse_show_config(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in text.splitlines():
        k, eq, v = ln.partition("=")
        if eq and re.fullmatch(r"[A-Za-z]\w*", k.strip()):
            out[k.strip()] = v.strip()
    return out


def comparable(cfg: dict[str, str], extra: set[str] = frozenset()) -> dict[str, str]:
    """Only the keys a lab can change (policy, time-scaled, restart classes, and the marker's own): the rest of `scontrol show config`
    holds runtime values (boot time, counters) that change on their own."""
    keep = VOLATILE_SAFE | {e.lower() for e in extra}
    return {k.lower(): v for k, v in cfg.items() if k.lower() in keep}


def same_value(want: str, got: str) -> bool:
    a, b = want.strip().strip('"'), got.strip().strip('"')
    if a.casefold() == b.casefold():
        return True
    try:
        return float(a) == float(b)
    except ValueError:
        pass
    try:  # 7-0 and 7-00:00:00 are one time
        return (re.search(r"[:-]", a) is not None and lab_slurm.parse_duration(a) == lab_slurm.parse_duration(b))
    except LabError:
        return False


def verify(want: dict[str, str], cfg: dict[str, str]) -> tuple[list[tuple[str, str, str]], list[str]]:
    """([(key, wanted, live)] that differ, [keys scontrol does not show]): a mismatch fails the apply, an unshown key is only listed."""
    low = {k.lower(): v for k, v in cfg.items()}
    bad, unseen = [], []
    for k, v in want.items():
        if k.lower() not in low:
            unseen.append(k)
        elif not same_value(v, low[k.lower()]):
            bad.append((k, v, low[k.lower()]))
    return bad, unseen


# ---------------------------------------------------------------- sacctmgr records

def dump_records(text: str) -> dict[str, str]:
    """`sacctmgr dump` -> {"Account/parent/name": line, "User/parent/name": line}; the parent is the last Parent line."""
    out, parent = {}, ""
    for raw in text.splitlines():
        m = re.match(r"^(\w+) - '([^']*)'(.*)$", raw.strip())
        if not m:
            continue
        kind, nm, _ = m.groups()
        if kind == "Parent":
            parent = nm
        elif kind in ("Account", "User"):
            out[f"{kind}/{parent}/{nm}"] = raw.strip()
    return out


def record_delta(pre: str, post: str) -> tuple[list[str], list[str]]:
    """(created, changed) record keys: present after and not before, and present in both with other options. From the saved dumps, never by name."""
    a, b = dump_records(pre), dump_records(post)
    return sorted(k for k in b if k not in a), sorted(k for k in b if k in a and a[k] != b[k])


def qos_table(text: str) -> dict[str, dict[str, str]]:
    if not text.strip():
        return {}
    _, rows = lab_import.parse_parsable(text, ("Name",), "QOS table")
    return {r["Name"]: r for r in rows}


def qos_delta(pre: dict, post: dict) -> tuple[list[str], list[str]]:
    return (sorted(n for n in post if n not in pre),
            sorted(n for n in post if n in pre and any(pre[n].get(f, "") != post[n].get(f, "") for f in QOS_FIELDS)))


def qos_set(row: dict[str, str]) -> list[str]:
    return [f"{f}={row[f]}" for f in QOS_FIELDS if row.get(f)]


def with_cluster(dump: str, cluster: str = CLUSTER) -> str:
    """The imported dump names the production cluster; the lab's accounts go to the workbench cluster."""
    return re.sub(r"^(Cluster - ')[^']*(')", rf"\g<1>{cluster}\g<2>", dump, flags=re.MULTILINE)


# ---------------------------------------------------------------- environment (injectable for tests)

@dataclass
class Env:
    root: Path = field(default_factory=lambda: lab.ROOT)
    slurm_dir: Path = field(default_factory=lambda: Path("compose/generated/slurm"))
    prom_dir: Path = field(default_factory=lambda: Path("compose/generated"))
    sim: dict = field(default_factory=dict)
    runner: Callable = _run
    running: Callable[[str], bool] = lambda s: False
    kubectl: Callable | None = None  # (args, stdin) -> CompletedProcess
    http_get: Callable[[str], str] = lambda url: urllib.request.urlopen(url, timeout=5).read().decode()
    rules_check: Callable[[list[Path]], list[str]] | None = None  # None: promtool_check (issue 552's executor)
    restart_ctld: Callable[[], bool] | None = None
    sleep: Callable[[float], None] = time.sleep
    say: Callable[[str], None] = print

    def dc(self, *args: str) -> list[str]:
        from . import stack
        return stack.dc(*args)

    def exec(self, service: str, *cmd: str, input: str | None = None) -> subprocess.CompletedProcess:
        return self.runner(self.dc("exec", "-T", service, *cmd), input=input) if input is not None else self.runner(self.dc("exec", "-T", service, *cmd))

    def kube(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        if self.kubectl:
            return self.kubectl(args, stdin)
        from . import k8s
        return k8s.kubectl(self.sim, *args, runner=self.runner, stdin=stdin)

    def restart(self) -> bool:
        if self.restart_ctld:
            return self.restart_ctld()
        from . import stack
        r = self.runner(self.dc("up", "-d", "--no-deps", "--force-recreate", "slurmctld"))
        return r.returncode == 0 and not stack.wait_running(["slurmctld"], self.runner) and not stack._answering(["slurmctld"], self.runner, stack.COMPOSE_FILE, 60)


def render_dir(name: str, env: Env) -> Path:
    d = lab.path(name, env.root) / "render"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sacct_dump(env: Env) -> str:
    r = env.exec("slurmctld", "sh", "-c", f"f=$(mktemp -u) && sacctmgr -i dump {CLUSTER} file=$f >/dev/null && cat $f && rm -f $f")
    if r.returncode != 0:
        raise LabError(f"sacctmgr dump failed: {(r.stderr or r.stdout).strip()[-200:]}")
    return r.stdout


def _qos_dump(env: Env) -> str:
    r = env.exec("slurmctld", "sacctmgr", "-n", "-P", "show", "qos", "format=Name," + ",".join(QOS_FIELDS))
    if r.returncode != 0:
        raise LabError(f"sacctmgr show qos failed: {(r.stderr or r.stdout).strip()[-200:]}")
    return "Name|" + "|".join(QOS_FIELDS) + "\n" + r.stdout


def _scontrol_config(env: Env) -> str:
    r = env.exec("slurmctld", "scontrol", "show", "config")
    if r.returncode != 0:
        raise LabError(f"scontrol show config failed: {(r.stderr or r.stdout).strip()[-200:]}")
    return r.stdout


def _k8s_objects(files: dict[str, str], name: str) -> list[dict]:
    objs = []
    for rel, text in sorted(files.items()):
        if rel.startswith("k8s/"):
            for d in yaml.safe_load_all(text):
                if isinstance(d, dict) and d.get("kind") and (d.get("metadata") or {}).get("name"):
                    d.setdefault("metadata", {}).setdefault("labels", {})[LABEL] = name
                    objs.append(d)
    return objs


def _teleport_roles(files: dict[str, str], name: str) -> list[dict]:
    out = []
    for rel, text in sorted(files.items()):
        if rel.startswith("teleport/"):
            for d in yaml.safe_load_all(text):
                if isinstance(d, dict) and d.get("kind") == "role" and (d.get("metadata") or {}).get("name"):
                    d["metadata"].setdefault("labels", {})[LABEL] = name
                    out.append(d)
    return out


def _rule_files(files: dict[str, str]) -> dict[str, str]:
    return {rel.split("/", 1)[1]: t for rel, t in files.items() if rel.startswith("rules/") and not re.search(r"\.test\.ya?ml$", rel)}


def ref(o: dict) -> str:
    md = o["metadata"]
    return f"{o['kind']}/{md['name']}" + (f" in {md['namespace']}" if md.get("namespace") else "")


def partition_warnings(files: dict[str, str], live_conf: str) -> list[str]:
    """Imported QOS or account rows that name a partition the lab does not have."""
    try:
        have = {p.get("PartitionName") for p in lab_import.parse_slurm_conf(live_conf)["partitions"]}
    except lab_import.ImportError_:
        return []
    named: dict[str, str] = {}
    for line in files.get("sacctmgr.dump", "").splitlines():
        for p in re.findall(r":Partition=([^:']+)", line):
            named[p] = "sacctmgr.dump"
    if files.get("qos.txt"):
        try:
            for r in lab_import.parse_qos(files["qos.txt"]):
                for col in ("Partition", "PartitionQOS"):
                    if r.get(col):
                        named[r[col]] = "qos.txt"
        except lab_import.ImportError_:
            pass
    return [f"warning: {src} names partition {p}, which the lab does not have (partitions come with the run)" for p, src in sorted(named.items()) if p not in have]


def promtool_check(files: list[Path], ex: Callable[..., tuple[int, str]] | None = None) -> list[str]:
    """`promtool check rules` on rule files that sit in one folder, as a throwaway one-shot with the executor and image of the lab
    validation (issue 552, `lab_validate`); returns the error lines, empty when the rules are valid."""
    if not files:
        return []
    ex = ex or lab_validate.run_cmd
    d = files[0].parent
    # as the host user: the rules sit in a private temporary folder (mode 700) that the image's own user (nobody) cannot enter
    rc, out = ex(["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}", "--memory", "128m", "--entrypoint", "promtool", "-v", f"{d.resolve()}:/rules:ro",
                  lab_validate.PROM_IMAGE, "check", "rules", *[f"/rules/{p.name}" for p in files]], timeout=lab_validate.ONE_SHOT_TIMEOUT_S)
    if rc == 0:
        return []
    lines = [ln.strip().replace("/rules/", "") for ln in out.splitlines() if ln.strip()]
    return [ln for ln in lines if "FAILED" in ln or "error" in ln.lower()][:5] or lines[:1] or [f"promtool exit {rc}"]


# ---------------------------------------------------------------- apply

def apply(name: str, side: str, env: Env) -> dict:
    """Apply one side. Raises LabError (one message) before any change when a check fails; returns {"lines": [...], "marker": {...}}."""
    lab.valid_name(name)
    lab_candidate.check_side(side)
    files = lab_candidate.effective(name, side, env.root)
    old = read_marker(env.root)
    if old and old["name"] != name:
        raise LabError(f"lab {old['name']} is active: remove it first (benchctl remove lab {old['name']}), then apply {name}")
    if not env.running("slurmctld"):
        raise LabError("the cluster is not running: start it (./scripts/benchctl up) and apply again")
    left = lab_import.residual_secrets(files)
    if left:
        raise LabError("refused: a secret may be in the " + side + " files at " + ", ".join(left[:5]) + "; remove it and apply again")
    sl = render_slurm(name, side, env.root)
    live_text = (env.slurm_dir / "slurm.conf").read_text() if (env.slurm_dir / "slurm.conf").is_file() else ""
    lines: list[str] = []
    objs, roles, rules = _k8s_objects(files, name), _teleport_roles(files, name), _rule_files(files)
    has_sacct = bool(files.get("sacctmgr.dump") or files.get("qos.txt"))
    # ---- every refusal first
    if objs and env.running("k8s-apiserver"):
        for o in objs:
            _refuse_clash(env, "k8s", o, name)
    if roles and env.running("teleport"):
        for r in roles:
            _refuse_clash(env, "teleport", r, name)
    rule_dir = env.prom_dir / RULES_REL
    if rules:
        with tempfile.TemporaryDirectory(prefix="lab-rules-") as tmp:
            stage = []
            for f, t in rules.items():
                p = Path(tmp) / f
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(t)
                stage.append(p)
            errs = (env.rules_check or promtool_check)(stage)
        if errs:
            raise LabError("promtool refuses the rules: " + "; ".join(errs[:3]))
    # ---- pre-lab snapshot (once per lab: a second apply keeps the first)
    rd = render_dir(name, env)
    if not old:
        (rd / "pre-lab-config.txt").write_text(_scontrol_config(env))
        if has_sacct and env.running("slurmdbd"):
            (rd / "sacctmgr.pre.dump").write_text(_sacct_dump(env))
            (rd / "qos.pre.txt").write_text(_qos_dump(env))
    prev = (old or {}).get("created_objects", {})
    k8s_made = [{"kind": o["kind"], "name": o["metadata"]["name"], "namespace": o["metadata"].get("namespace")} for o in objs]
    k8s_made += [x for x in prev.get("k8s", []) if x not in k8s_made]  # the other side's objects stay listed until remove
    roles_made = [r["metadata"]["name"] for r in roles] + [x for x in prev.get("teleport_roles", []) if x not in [r["metadata"]["name"] for r in roles]]
    m = {"name": name, "side": side, "time_scale": 1.0, "created": (old or {}).get("created") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "applied": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "keys": sl["keys"], "not_applied_unknown": sl["unknown"],
         "lab_owned": sl["lab_owned"], "restart": False,
         "created_objects": {"k8s": k8s_made, "teleport_roles": roles_made, "rules_files": [f"{name}-{f}" for f in rules],
                             "sacctmgr": bool((old or {}).get("created_objects", {}).get("sacctmgr") or (has_sacct and env.running("slurmdbd")))}}
    for stale in set(prev.get("rules_files", [])) - set(m["created_objects"]["rules_files"]):
        (rule_dir / stale).unlink(missing_ok=True)
    # ---- slurm.conf
    changed = changed_keys(sl["keys"], conf_keys(live_text), (old or {}).get("keys", {}))
    restart = any(lab_slurm.restart_required(k) for k in changed)
    m["restart"] = restart
    write_marker(m, env.root)
    (env.slurm_dir / "slurm.conf").write_text(overlay_text(live_text, sl["keys"], name))
    (rd / "slurm.keys.json").write_text(json.dumps(sl["keys"], indent=2) + "\n")
    if restart:
        lines.append("slurmctld restarts: " + ", ".join(k for k in changed if lab_slurm.restart_required(k)))
        if not env.restart():
            raise LabError("slurmctld did not come back after the restart; look at `docker logs workbench-slurmctld-1`; the lab marker is kept, `benchctl remove lab " + name + "` returns to the plain cluster")
    else:
        r = env.exec("slurmctld", "scontrol", "reconfigure")
        if r.returncode != 0:
            raise LabError(f"scontrol reconfigure failed: {(r.stderr or r.stdout).strip()[-200:]}")
        lines.append("slurmctld reconfigured (no restart)")
    cfg = parse_show_config(_scontrol_config(env))
    bad, unseen = verify(sl["keys"], cfg)
    if bad:
        raise LabError("scontrol show config differs from the rendered settings: " + "; ".join(f"{k} wanted {w}, live {g}" for k, w, g in bad[:5])
                       + f". The lab marker is kept; `benchctl remove lab {name}` returns to the plain cluster")
    lines.append(f"slurm.conf: {len(sl['keys'])} key(s) applied and verified" + (f" ({len(unseen)} not shown by scontrol: {', '.join(unseen[:5])})" if unseen else ""))
    if sl["unknown"]:
        lines.append("not applied: unknown: " + ", ".join(sl["unknown"]))
    if sl["lab_owned"]:
        lines.append("not tested: lab-owned keys keep the base value: " + ", ".join(sl["lab_owned"][:8]) + (" ..." if len(sl["lab_owned"]) > 8 else ""))
    lines.append("times are unscaled (time_scale 1.0): the run scales them")
    lines += partition_warnings(files, live_text)
    # ---- accounting
    if has_sacct:
        if env.running("slurmdbd"):
            lines += _apply_sacct(name, files, env, rd)
        else:
            lines.append("not applied: QOS and accounts (accounting is not running: start it with `benchctl up --with accounting`)")
    # ---- prometheus, kubernetes, teleport
    if rules:
        lines += _apply_rules(name, rules, env)
    if objs:
        lines += _apply_k8s(name, objs, env)
    if roles:
        lines += _apply_roles(name, roles, env)
    return {"lines": lines, "marker": m}


def _refuse_clash(env: Env, kind: str, o: dict, name: str) -> None:
    if kind == "k8s":
        md = o["metadata"]
        args = ["get", o["kind"].lower(), md["name"], *(["-n", md["namespace"]] if md.get("namespace") else []), "-o", "json", "--ignore-not-found"]
        r = env.kube(*args)
        if r.returncode != 0:
            raise LabError(f"kubectl could not check {ref(o)}: {(r.stderr or r.stdout).strip()[-200:]}")
        if not r.stdout.strip():
            return
        have = ((json.loads(r.stdout).get("metadata") or {}).get("labels") or {}).get(LABEL)
    else:
        r = env.exec("teleport", "tctl", "get", f"role/{o['metadata']['name']}", "--format=json")
        if r.returncode != 0:
            return  # not found
        doc = json.loads(r.stdout or "[]")
        doc = doc[0] if isinstance(doc, list) and doc else doc if isinstance(doc, dict) else {}
        have = ((doc.get("metadata") or {}).get("labels") or {}).get(LABEL)
    if have != name:
        what = ref(o) if kind == "k8s" else f"Teleport role {o['metadata']['name']}"
        raise LabError(f"refused: {what} already exists and was not made by lab {name}; rename it in the lab files or remove it from the cluster, then apply again")


def _apply_sacct(name: str, files: dict[str, str], env: Env, rd: Path) -> list[str]:
    out = []
    qos = lab_import.parse_qos(files["qos.txt"]) if files.get("qos.txt") else []
    for row in qos:  # every QOS exists before any of them names another in Preempt
        env.exec("slurmctld", "sacctmgr", "-i", "add", "qos", row["Name"])  # an existing one answers non-zero: that is fine
    for row in qos:
        sets = qos_set(row)
        if sets:
            r = env.exec("slurmctld", "sacctmgr", "-i", "modify", "qos", row["Name"], "set", *sets)
            if r.returncode != 0:
                out.append(f"warning: QOS {row['Name']} not fully set: {(r.stderr or r.stdout).strip()[-120:]}")
    if files.get("sacctmgr.dump"):
        r = env.exec("slurmctld", "sh", "-c", "f=$(mktemp) && cat > $f && sacctmgr -i load $f; rc=$?; rm -f $f; exit $rc", input=with_cluster(files["sacctmgr.dump"]))
        if r.returncode != 0:
            raise LabError(f"sacctmgr load failed: {(r.stderr or r.stdout).strip()[-200:]}. The lab marker is kept; `benchctl remove lab {name}` restores the accounts")
    (rd / "sacctmgr.post.dump").write_text(_sacct_dump(env))
    (rd / "qos.post.txt").write_text(_qos_dump(env))
    out.append(f"accounting: {len(qos)} QOS and the account dump applied" if files.get("sacctmgr.dump") else f"accounting: {len(qos)} QOS applied")
    return out


def _apply_rules(name: str, rules: dict[str, str], env: Env) -> list[str]:
    d = env.prom_dir / RULES_REL
    d.mkdir(parents=True, exist_ok=True)
    for f, t in rules.items():
        (d / f"{name}-{f}").write_text(t)
    return _reload_prometheus(env, f"{len(rules)} rule file(s)")


def _reload_prometheus(env: Env, what: str) -> list[str]:
    if not env.running("prometheus"):
        return [f"prometheus is not running: {what} are in place and load when it starts"]
    r = env.runner(env.dc("kill", "-s", "HUP", "prometheus"))
    if r.returncode != 0:
        raise LabError(f"could not reload Prometheus: {(r.stderr or r.stdout).strip()[-200:]}")
    for _ in range(10):
        env.sleep(1)
        try:  # Prometheus's own /metrics, not a query: this Prometheus does not scrape itself, so the series is not in its database (found live)
            m = re.search(r"^prometheus_config_last_reload_successful\s+(\S+)", env.http_get("http://127.0.0.1:9090/metrics"), re.MULTILINE)
            if m and float(m.group(1)) == 1.0:
                return [f"prometheus reloaded with {what}"]
        except (OSError, ValueError):
            continue
    raise LabError("Prometheus reloaded but prometheus_config_last_reload_successful is not 1: look at `docker logs workbench-prometheus-1`")


def _apply_k8s(name: str, objs: list[dict], env: Env) -> list[str]:
    if not env.running("k8s-apiserver"):
        return [f"not applied: {len(objs)} Kubernetes object(s) (Kubernetes is not running: start it with `benchctl up --with k8s`)"]
    r = env.kube("apply", "-f", "-", stdin=yaml.safe_dump_all(objs, sort_keys=False))
    if r.returncode != 0:
        raise LabError(f"kubectl apply failed: {(r.stderr or r.stdout).strip()[-300:]}. The lab marker is kept; `benchctl remove lab {name}` deletes what was made")
    return [f"kubernetes: {len(objs)} object(s) applied, label {LABEL}={name}"]


def _apply_roles(name: str, roles: list[dict], env: Env) -> list[str]:
    if not env.running("teleport"):
        return [f"not applied: {len(roles)} Teleport role(s) (Teleport is not running: start it with `benchctl up --with teleport`)"]
    r = env.exec("teleport", "sh", "-c", "f=$(mktemp) && cat > $f && tctl create -f $f; rc=$?; rm -f $f; exit $rc", input=yaml.safe_dump_all(roles, sort_keys=False))
    if r.returncode != 0:
        raise LabError(f"tctl create failed: {(r.stderr or r.stdout).strip()[-300:]}. The lab marker is kept; `benchctl remove lab {name}` deletes the roles it made")
    return [f"teleport: {len(roles)} role(s) created"]


# ---------------------------------------------------------------- remove

def remove(name: str, env: Env) -> dict:
    """Return to the plain cluster. Refuses (marker kept) when something the lab made cannot be reached; deletes only what the lab made."""
    lab.valid_name(name)
    m = read_marker(env.root)
    if not m or m["name"] != name:
        raise LabError(f"lab {name} is not active" + (f" (lab {m['name']} is)" if m else "") + ": nothing to remove")
    made = m.get("created_objects", {})
    need = [(bool(made.get("sacctmgr")), "slurmdbd", "accounting", "benchctl up --with accounting"),
            (bool(made.get("k8s")), "k8s-apiserver", "Kubernetes", "benchctl up --with k8s"),
            (bool(made.get("teleport_roles")), "teleport", "Teleport", "benchctl up --with teleport")]
    for wanted, service, label, how in need:
        if wanted and not env.running(service):
            raise LabError(f"cannot remove lab {name}: it made objects in {label}, which is not running. Start it ({how}) and run `benchctl remove lab {name}` again; the lab stays active until then")
    if not env.running("slurmctld"):
        raise LabError(f"cannot remove lab {name}: the cluster is not running. Start it (./scripts/benchctl up) and run `benchctl remove lab {name}` again; the lab stays active until then")
    rd = render_dir(name, env)
    lines: list[str] = []
    if made.get("sacctmgr"):
        lines += _remove_sacct(name, env, rd)
    if made.get("k8s"):
        kinds = sorted({o["kind"].lower() for o in made["k8s"]})
        r = env.kube("delete", ",".join(kinds), "-A", "-l", f"{LABEL}={name}", "--ignore-not-found")
        if r.returncode != 0:
            raise LabError(f"kubectl delete failed: {(r.stderr or r.stdout).strip()[-300:]}; the lab stays active")
        lines.append(f"kubernetes: objects labelled {LABEL}={name} deleted")
    for role in made.get("teleport_roles", []):
        r = env.exec("teleport", "tctl", "rm", f"role/{role}")
        if r.returncode != 0 and "not found" not in (r.stderr + r.stdout):
            raise LabError(f"tctl rm role/{role} failed: {(r.stderr or r.stdout).strip()[-200:]}; the lab stays active")
        lines.append(f"teleport: role {role} removed")
    if made.get("rules_files"):
        for f in made["rules_files"]:
            (env.prom_dir / RULES_REL / f).unlink(missing_ok=True)
        lines += _reload_prometheus(env, "no lab rules")
    # ---- slurm.conf last: every other step is done, so a failure here leaves a marker that a retry resolves
    conf = env.slurm_dir / "slurm.conf"
    if conf.is_file():
        conf.write_text(strip_overlay(conf.read_text()))
    restart = any(lab_slurm.restart_required(k) for k in m.get("keys", {}))
    if restart:
        if not env.restart():
            raise LabError("slurmctld did not come back after the restart; the lab stays active. Look at `docker logs workbench-slurmctld-1`")
    else:
        r = env.exec("slurmctld", "scontrol", "reconfigure")
        if r.returncode != 0:
            raise LabError(f"scontrol reconfigure failed: {(r.stderr or r.stdout).strip()[-200:]}; the lab stays active")
    pre = rd / "pre-lab-config.txt"
    if pre.is_file():
        want = comparable(parse_show_config(pre.read_text()), set(m.get("keys", {})))
        got = comparable(parse_show_config(_scontrol_config(env)), set(m.get("keys", {})))
        bad = sorted(k for k, v in want.items() if k in got and not same_value(v, got[k]))
        if bad:
            raise LabError("scontrol show config still differs from the pre-lab value for " + ", ".join(bad[:8]) + "; the lab stays active, run `benchctl remove lab " + name + "` again")
        lines.append(f"slurm.conf: back to the pre-lab settings ({len(want)} key(s) compared)")
    marker_path(env.root).unlink(missing_ok=True)
    lines.append(f"lab {name} removed: the cluster is the plain sim.yaml cluster again")
    return {"lines": lines}


def _remove_sacct(name: str, env: Env, rd: Path) -> list[str]:
    pre, post = rd / "sacctmgr.pre.dump", rd / "sacctmgr.post.dump"
    if not (pre.is_file() and post.is_file()):
        raise LabError(f"the saved sacctmgr dumps of lab {name} are missing in compose/generated/lab/{name}/render; the lab stays active")
    out, refused = [], []
    made, changed = record_delta(pre.read_text(), post.read_text())
    r = env.exec("slurmctld", "sh", "-c", "f=$(mktemp) && cat > $f && sacctmgr -i load $f; rc=$?; rm -f $f; exit $rc", input=pre.read_text())  # puts changed rows back
    if r.returncode != 0:
        raise LabError(f"sacctmgr load of the saved pre-lab dump failed: {(r.stderr or r.stdout).strip()[-200:]}; the lab stays active")
    for key in sorted(made, key=lambda k: (k.startswith("Account/"), -k.count("/"))):  # users before accounts
        kind, parent, nm = key.split("/", 2)
        cmd = ["sacctmgr", "-i", "delete", "user", nm] + ([f"account={parent}"] if parent else []) if kind == "User" else ["sacctmgr", "-i", "delete", "account", nm]
        if env.exec("slurmctld", *cmd).returncode != 0:
            refused.append(f"{kind.lower()} {nm}")
    qpre = qos_table((rd / "qos.pre.txt").read_text()) if (rd / "qos.pre.txt").is_file() else {}
    qpost = qos_table((rd / "qos.post.txt").read_text()) if (rd / "qos.post.txt").is_file() else {}
    qmade, qchanged = qos_delta(qpre, qpost)
    for n in qchanged:  # the old limits come back from the saved table; an empty one is cleared
        sets = [f"{f}={qpre[n].get(f) or '-1'}" for f in QOS_FIELDS if qpre[n].get(f, "") != qpost[n].get(f, "")]
        if env.exec("slurmctld", "sacctmgr", "-i", "modify", "qos", n, "set", *sets).returncode != 0:
            refused.append(f"qos {n} (limits not restored)")
    for n in qmade:
        if env.exec("slurmctld", "sacctmgr", "-i", "delete", "qos", n).returncode != 0:
            refused.append(f"qos {n}")
    out.append(f"accounting: {len(made)} account/user row(s) and {len(qmade)} QOS removed, {len(changed)} account row(s) and {len(qchanged)} QOS restored")
    if refused:
        out.append("sacctmgr refused: " + ", ".join(refused) + " (still in the accounting database; remove them by hand with sacctmgr)")
    return out


# ---------------------------------------------------------------- the CLI side

def result_path(name: str, root: Path | None = None) -> Path:
    return lab.path(name, root) / "last-result.json"


def execute(op: str, name: str, side: str, env: Env) -> bool:
    """Run apply or remove in this process (the governor calls `benchctl ... --execute`), print the lines, save them for the waiting CLI."""
    ok, lines, error = True, [], None
    try:
        res = apply(name, side, env) if op == "apply" else remove(name, env)
        lines = res["lines"]
    except LabError as e:
        ok, error = False, str(e)
    out = {"op": op, "ok": ok, "lines": lines, "error": error, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        result_path(name, env.root).parent.mkdir(parents=True, exist_ok=True)
        result_path(name, env.root).write_text(json.dumps(out, indent=2) + "\n")
    except OSError:
        pass
    for ln in lines:
        env.say(ln)
    if error:
        env.say(error)
    return ok


def ask_governor(req: dict, name: str, request: Callable[[dict], dict], root: Path | None = None, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 600) -> tuple[bool, list[str]]:
    """Send a lab command to the governor, wait for its change to end, return (ok, lines to print). One message per condition."""
    try:
        r = request(req)
    except OSError:
        return False, ["the governor is not reachable: apply and remove go through it. Start it with ./scripts/benchctl start"]
    if not r.get("ok"):
        return False, [r.get("error", "refused")]
    if r.get("dry_run") or not r.get("job"):
        return True, ["the governor is in dry-run mode: nothing was changed"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ch = request({"cmd": "status"}).get("change") or {}
        if ch.get("job") == r["job"] and ch.get("state") in ("done", "failed"):
            break
        sleep(1)
    else:
        return False, ["timed out waiting for the governor; check `benchctl status`"]
    try:
        res = json.loads(result_path(name, root).read_text())
    except (OSError, ValueError):
        return ch.get("state") == "done", ["finished, but no result was saved: see `benchctl status`"]
    return bool(res["ok"]), res["lines"] + ([res["error"]] if res.get("error") else [])
