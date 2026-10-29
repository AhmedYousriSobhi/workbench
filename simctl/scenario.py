"""Scenario engine (SPEC C-9): run scenarios/*.yaml against the live stack.

A scenario is `steps` (`at`, `target`, `do`) and optional `expect` assertions. Each step is one HTTP call to a
mock's /sim/inject (or a `scontrol` call in slurmctld). `ramp` steps expand into a series of temperature steps.
Targets whose component is not built yet (k8s, ticket-bot) are reported as skipped, not failed.
The `ssh` target acts on the login node and the nodes' sshd through Docker, like `slurm` it needs the host.
"""
import json
import re
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import checks
from .nodes import node_names

ENDPOINTS = {"gpu-exporter": "http://127.0.0.1:9400/sim/inject", "bmc-farm": "http://127.0.0.1:8000/sim/inject",
             "bcm-mock": "http://127.0.0.1:8081/sim/inject", "jira": "http://127.0.0.1:8080/sim/failure"}
SLURMCTLD = "hpcsim-slurmctld-1"
LOGIN = "hpcsim-login-1"
SSH_DIR = Path("compose/generated/secrets/ssh")
SSH_FAULT_FILE = "/etc/ssh/sshd_config.d/90-fault.conf"
LOGIN_ACTIONS = ("down", "up", "hang", "resume", "reject_auth", "accept_auth", "rotate_host_key", "restore_host_key",
                 "limit", "unlimit")
SSHD_ACTIONS = ("down", "up")
NOT_BUILT = ("k8s", "ticket-bot")
RAMP_STEP_S = 10.0
RAMP_START_C = 50.0
SCENARIOS = Path("scenarios")


class ScenarioError(ValueError):
    pass


def parse_duration(text) -> float:
    """'90s' / '2m' / '1h' / 45 -> seconds."""
    if isinstance(text, (int, float)):
        return float(text)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(s|m|h)?\s*", str(text))
    if not m:
        raise ScenarioError(f"bad duration {text!r}")
    return float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, None: 1}[m.group(2)]


@dataclass
class Step:
    at: float
    target: str
    do: dict


@dataclass
class Outcome:
    label: str
    status: str  # ok | failed | skipped
    detail: str = ""


@dataclass
class Report:
    name: str
    steps: list[Outcome] = field(default_factory=list)
    expects: list[Outcome] = field(default_factory=list)
    stopped: bool = False

    @property
    def ok(self) -> bool:
        return all(o.status != "failed" for o in self.steps + self.expects)


def load(name: str, root: Path = SCENARIOS) -> dict:
    path = root / f"{name}.yaml"
    if not path.is_file():
        raise ScenarioError(f"no scenario {name!r} (see: simctl scenario list)")
    return yaml.safe_load(path.read_text())


def list_all(root: Path = SCENARIOS) -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted(root.glob("*.yaml"))]


def plan(sc: dict) -> list[Step]:
    """Steps in time order; a `ramp: {to, over}` step becomes temperature steps every RAMP_STEP_S."""
    out: list[Step] = []
    for s in sc.get("steps", []):
        at, target, do = parse_duration(s["at"]), s["target"], dict(s.get("do") or {})
        ramp = do.pop("ramp", None)
        if ramp:
            over, goal = parse_duration(ramp["over"]), float(str(ramp["to"]).rstrip("C"))
            n = max(1, int(over // RAMP_STEP_S))
            for i in range(1, n + 1):
                out.append(Step(at + over * i / n, target, {**do, "temp": round(RAMP_START_C + (goal - RAMP_START_C) * i / n, 1)}))
        if not ramp or set(do) - {"nodes"}:  # a ramp's own step carries nothing but the node list
            out.append(Step(at, target, do))
    return sorted(out, key=lambda s: s.at)


def validate(sc: dict, sim: dict) -> list[str]:
    """Problems found without touching the stack: unknown targets, nodes or durations."""
    problems = []
    nodes = {n for n, _ in node_names(sim)}
    for i, s in enumerate(sc.get("steps", []), 1):
        try:
            parse_duration(s["at"])
        except (KeyError, ScenarioError):
            problems.append(f"step {i}: bad or missing 'at'")
        t = s.get("target")
        if t not in ENDPOINTS and t not in ("slurm", "ssh") and t not in NOT_BUILT:
            problems.append(f"step {i}: unknown target {t!r}")
        do = s.get("do") or {}
        if t == "ssh":
            problems += [f"step {i}: {p}" for p in validate_ssh(do)]
        names = (do.get("nodes") or []) + (do.get("drain") or []) + (do.get("resume") or [])
        problems += [f"step {i}: unknown node {n!r}" for n in names if n not in nodes]
    return problems


def validate_ssh(do: dict) -> list[str]:
    problems = []
    if "login" not in do and "sshd" not in do:
        problems.append("ssh needs `login` or `sshd`")
    if "login" in do and do["login"] not in LOGIN_ACTIONS:
        problems.append(f"unknown login action {do['login']!r} (one of {', '.join(LOGIN_ACTIONS)})")
    if "sshd" in do and do["sshd"] not in SSHD_ACTIONS:
        problems.append(f"unknown sshd action {do['sshd']!r} (down or up)")
    if "sshd" in do and not do.get("nodes"):
        problems.append("sshd needs `nodes`")
    for k in ("max_startups", "max_sessions"):
        if k in do and not (isinstance(do[k], int) and do[k] >= 1):
            problems.append(f"{k} must be a whole number of 1 or more")
    return problems


def post(url: str, body: dict, timeout: float = 10) -> None:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    urllib.request.urlopen(req, timeout=timeout).close()


def rotate_host_key(runner=checks.run, path: Path | None = None) -> str:
    """New login host key (the original is kept as .orig for restore); returns an error text or ''."""
    path = path or SSH_DIR
    key = path / "ssh_host_ed25519_key"
    if not key.exists():
        return "no login host key yet (run simctl up first)"
    for suffix in ("", ".pub"):
        orig = Path(f"{key}{suffix}.orig")
        if not orig.exists():
            Path(f"{key}{suffix}").rename(orig)
        else:
            Path(f"{key}{suffix}").unlink(missing_ok=True)
    r = runner(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "hpcsim-rotated", "-f", str(key)])
    return "" if r.returncode == 0 else (r.stderr or r.stdout).strip()


def restore_host_key(path: Path | None = None) -> str:
    path = path or SSH_DIR
    key = path / "ssh_host_ed25519_key"
    if not Path(f"{key}.orig").exists():
        return ""  # never rotated: nothing to restore
    for suffix in ("", ".pub"):
        Path(f"{key}{suffix}.orig").replace(f"{key}{suffix}")
    return ""


def fault_conf(lines: list[str] | None) -> list[str]:
    """Command that sets (or, for None, removes) the login node's sshd fault file and makes sshd re-read it."""
    write = f"printf '%s\\n' {' '.join(shlex.quote(x) for x in lines)} > {SSH_FAULT_FILE}" if lines else f"rm -f {SSH_FAULT_FILE}"
    return ["docker", "exec", LOGIN, "sh", "-c", f"{write} && kill -HUP $(cat /run/sshd.pid)"]


def ssh_commands(do: dict) -> list[list[str]]:
    """The Docker commands for one `ssh` step, in order."""
    cmds: list[list[str]] = []
    login = do.get("login")
    if login in ("down", "up", "hang", "resume"):
        cmds.append(["docker", {"down": "stop", "up": "start", "hang": "pause", "resume": "unpause"}[login], LOGIN])
    elif login == "reject_auth":
        cmds.append(fault_conf(["DenyUsers *"]))
    elif login == "limit":
        n = do.get("max_startups", 1)  # N:100:N allows N unauthenticated connections at once and refuses the next
        lines = [f"MaxStartups {n}:100:{n}"]
        if "max_sessions" in do:
            lines.append(f"MaxSessions {do['max_sessions']}")
        cmds.append(fault_conf(lines))
    elif login in ("accept_auth", "unlimit"):
        cmds.append(fault_conf(None))
    for n in do.get("nodes", []) if "sshd" in do else []:
        cmds.append(["docker", "exec", f"hpcsim-{n}-1", "sh", "-c",
                     "kill $(cat /run/sshd.pid)" if do["sshd"] == "down" else "/usr/sbin/sshd"])
    return cmds


def execute_ssh(do: dict, label: str, runner=checks.run) -> Outcome:
    login = do.get("login")
    if login == "rotate_host_key":
        if err := rotate_host_key(runner):
            return Outcome(label, "failed", err)
    elif login == "restore_host_key":
        restore_host_key()
    for cmd in ssh_commands(do):
        r = runner(cmd)
        if r.returncode:
            return Outcome(label, "failed", (r.stderr or r.stdout).strip())
    if login in ("rotate_host_key", "restore_host_key"):  # sshd reads its host key only at start
        r = runner(["docker", "restart", LOGIN])
        if r.returncode:
            return Outcome(label, "failed", (r.stderr or r.stdout).strip())
    return Outcome(label, "ok")


def execute(step: Step, poster=post, runner=checks.run, host: bool = True) -> Outcome:
    """Apply one step; the label names what was done, status says whether it worked.

    host=False is for callers without Docker access (the Web UI): Slurm steps are skipped, not attempted.
    """
    label = f"{step.target} {json.dumps(step.do, sort_keys=True)}"
    if step.target in NOT_BUILT:
        return Outcome(label, "skipped", f"{step.target} is not built yet")
    if step.target in ("slurm", "ssh") and not host:
        return Outcome(label, "skipped", f"{step.target} steps need the host CLI (no Docker access here)")
    if step.target == "ssh":
        return execute_ssh(step.do, label, runner)
    try:
        if step.target == "slurm":
            for n in step.do.get("drain", []):
                r = runner(["docker", "exec", SLURMCTLD, "scontrol", "update", f"nodename={n}", "state=drain",
                            f"reason={step.do.get('reason', 'scenario')}"])
                if r.returncode:
                    return Outcome(label, "failed", (r.stderr or r.stdout).strip())
            for n in step.do.get("resume", []):
                r = runner(["docker", "exec", SLURMCTLD, "scontrol", "update", f"nodename={n}", "state=resume"])
                if r.returncode:
                    return Outcome(label, "failed", (r.stderr or r.stdout).strip())
        elif step.target == "jira":
            poster(ENDPOINTS["jira"], step.do.get("failure", {"mode": None}))
        else:
            for n in step.do.get("nodes", []):
                poster(ENDPOINTS[step.target], {"node": n, **{k: v for k, v in step.do.items() if k != "nodes"}})
    except urllib.error.HTTPError as e:
        return Outcome(label, "failed", f"{step.target} answered {e.code}")
    except (OSError, KeyError) as e:
        return Outcome(label, "failed", f"{step.target} unreachable or bad step: {e}")
    return Outcome(label, "ok")


def node_state(node: str, runner=checks.run) -> str:
    r = runner(["docker", "exec", SLURMCTLD, "sinfo", "-h", "-N", "-n", node, "-o", "%T"])
    return r.stdout.strip().split("\n")[0] if r.returncode == 0 else ""


def jira_labels(labels: list[str]) -> bool:
    jql = "labels = " + " AND labels = ".join(labels)
    url = "http://127.0.0.1:8080/rest/api/2/search?jql=" + urllib.parse.quote(jql)
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())["total"] > 0
    except (OSError, ValueError):
        return False


def expectation(exp: dict, runner=checks.run, host: bool = True):
    """(label, holds(), within seconds) for one assertion; holds is None when it cannot be evaluated here."""
    (kind, args), = exp.items()
    label = f"{kind} {json.dumps({k: v for k, v in args.items() if k != 'within'}, sort_keys=True)}"
    within = parse_duration(args.get("within", "0s"))
    if kind == "slurm.node_state" and host:
        return label, lambda: args["state"] in node_state(args["node"], runner), within
    if kind == "jira.issue_created":
        return label, lambda: jira_labels(args["labels"]), within
    return label, None, within


def check_expect(exp: dict, factor: float, sleep=time.sleep, clock=time.monotonic, runner=checks.run) -> Outcome:
    """Poll one assertion until it holds or `within` (scaled by the speed factor) runs out."""
    label, holds, within = expectation(exp, runner)
    if holds is None:
        return Outcome(label, "skipped", "cannot be evaluated here")
    deadline = clock() + within / factor
    while not holds():
        if clock() >= deadline:
            return Outcome(label, "failed", "did not hold within the time limit")
        sleep(min(1.0, max(0.0, deadline - clock())))
    return Outcome(label, "ok")


def run(sc: dict, speed: float = 1.0, dilation: float = 1.0, say=print, sleep=time.sleep, clock=time.monotonic,
        poster=post, runner=checks.run, host: bool = True, should_stop=lambda: False,
        on_step=lambda step, outcome: None, on_expect=lambda outcome: None) -> Report:
    """Play the scenario: wait for each step's time (divided by speed x time_dilation) and apply it.

    Every expectation is watched from the start: it passes the moment it holds (even if a later step undoes it) and
    fails if its `within` (from the start) runs out first.
    """
    factor = max(speed * dilation, 1e-6)
    report = Report(sc["name"])
    start = clock()
    pending = []
    for exp in sc.get("expect") or []:
        label, holds, within = expectation(exp, runner, host)
        if holds is None:
            report.expects.append(Outcome(label, "skipped", "cannot be evaluated here"))
            on_expect(report.expects[-1])
        else:
            pending.append((label, holds, start + within / factor))

    def watch() -> None:
        for item in list(pending):
            label, holds, deadline = item
            if holds():
                outcome = Outcome(label, "ok")
            elif clock() >= deadline:
                outcome = Outcome(label, "failed", "did not hold within the time limit")
            else:
                continue
            pending.remove(item)
            report.expects.append(outcome)
            on_expect(outcome)
            say(f"expect  {outcome.status:7} {outcome.label}" + (f"  ({outcome.detail})" if outcome.detail else ""))

    def wait_until(t: float) -> None:
        while (left := t - clock()) > 0 and not should_stop():
            sleep(min(left, 1.0))  # short slices so Stop and expectations are noticed promptly
            watch()

    for step in plan(sc):
        wait_until(start + step.at / factor)
        if should_stop():
            report.stopped = True
            return report
        outcome = execute(step, poster, runner, host)
        report.steps.append(outcome)
        on_step(step, outcome)
        say(f"[{step.at:6.1f}s] {outcome.status:7} {outcome.label}" + (f"  ({outcome.detail})" if outcome.detail else ""))
        watch()
    while pending and not should_stop():
        sleep(1.0)
        watch()
    report.stopped = should_stop()
    return report
