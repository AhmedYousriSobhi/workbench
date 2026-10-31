"""Scenario engine (SPEC C-9): run scenarios/*.yaml against the live stack.

A scenario is `steps` (`at`, `target`, `do`) and optional `expect` assertions. Each step is one HTTP call to a
mock's /sim/inject (or a `scontrol` call in slurmctld). `ramp` steps expand into a series of temperature steps.
Targets whose component is not built yet (k8s, ticket-bot) are reported as skipped, not failed.
"""
import json
import re
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
             "bcm-mock": "http://127.0.0.1:8081/sim/inject", "jira": "http://127.0.0.1:8080/sim/failure",
             "jira-tickets": "http://127.0.0.1:8080/sim/tickets"}
JIRA_ACTIONS = ("create", "transition", "comment", "reset")
SLURMCTLD = "workbench-slurmctld-1"
LOGIN = "workbench-login-1"
HOST_TARGETS = ("slurm", "ssh")  # steps that need Docker on the host
SSH_STATES = ("down", "up", "hung")
NODE_SSHD_STATES = ("down", "up")
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
        raise ScenarioError(f"no scenario {name!r} (see: benchctl scenario list)")
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
        if t == "jira-tickets":  # the endpoint key, not a target: steps say `jira` with `tickets:`
            problems.append(f"step {i}: unknown target {t!r}")
            continue
        if t not in ENDPOINTS and t not in HOST_TARGETS and t not in NOT_BUILT:
            problems.append(f"step {i}: unknown target {t!r}")
        do = s.get("do") or {}
        if t == "ssh":
            problems += [f"step {i}: {m}" for m in ssh_problems(do)]
        if t == "jira" and "tickets" in do:
            problems += [f"step {i}: {m}" for m in jira_ticket_problems(do["tickets"])]
        names = [n for key in ("nodes", "drain", "down", "resume", "power_off", "power_on") for n in do.get(key) or []]
        problems += [f"step {i}: unknown node {n!r}" for n in names if n not in nodes]
    return problems


def jira_ticket_problems(tk) -> list[str]:
    if not isinstance(tk, dict) or tk.get("action") not in JIRA_ACTIONS:
        return [f"jira tickets needs action: one of {', '.join(JIRA_ACTIONS)}"]
    need = {"create": ("summary",), "transition": ("jql", "to"), "comment": ("jql", "body"), "reset": ()}[tk["action"]]
    return [f"jira tickets {tk['action']} needs {k}" for k in need if not tk.get(k)]


def ssh_problems(do: dict) -> list[str]:
    known = {"state", "rotate_host_key", "max_sessions", "reset", "reject_auth", "node_sshd", "nodes"}
    out = [f"unknown ssh key {k!r}" for k in do if k not in known]
    if "node_sshd" in do:
        if do["node_sshd"] not in NODE_SSHD_STATES:
            out.append(f"ssh node_sshd must be one of {', '.join(NODE_SSHD_STATES)}")
        if not do.get("nodes"):
            out.append("ssh node_sshd needs nodes: [..]")
    elif "nodes" in do:
        out.append("ssh nodes only goes with node_sshd")
    if "state" in do and do["state"] not in SSH_STATES:
        out.append(f"ssh state must be one of {', '.join(SSH_STATES)}")
    ms = do.get("max_sessions")
    if "max_sessions" in do and not (isinstance(ms, int) and not isinstance(ms, bool) and ms >= 1):
        out.append("ssh max_sessions must be a whole number of at least 1")
    return out or ([] if do else ["ssh step does nothing"])


SSHD_HUP = "kill -HUP $(cat /run/sshd.pid)"  # sshd re-executes and re-reads its config and host keys; open sessions stay
SSH_SCRIPTS = {
    "rotate_host_key": "rm -f /etc/ssh/ssh_host_ed25519_key /etc/ssh/ssh_host_ed25519_key.pub && "
                       "ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/ssh_host_ed25519_key && " + SSHD_HUP,
    "reset": "install -m 600 /run/workbench/ssh/ssh_host_ed25519_key /etc/ssh/ssh_host_ed25519_key && "
             "rm -f /etc/ssh/ssh_host_ed25519_key.pub && sed -i '/^MaxSessions/d' /etc/ssh/sshd_config && "
             "install -m 644 /run/workbench/ssh/authorized_keys /etc/ssh/authorized_keys && " + SSHD_HUP,
    # sshd reads authorized_keys on every login, so no HUP: only the client's key goes, the login -> node key stays
    "reject_auth": "grep -v -e -id_ed25519 /run/workbench/ssh/authorized_keys > /etc/ssh/authorized_keys",
    "node_sshd_down": "kill $(cat /run/sshd.pid)",
    "node_sshd_up": "[ -f /run/sshd.pid ] && kill -0 $(cat /run/sshd.pid) 2>/dev/null || /usr/sbin/sshd -e",
}


def ssh_commands(do: dict) -> list[list[str]]:
    """The docker commands for one ssh step, in order: the login node's state, then sshd settings inside it."""
    cmds = []
    state = {"down": ["docker", "stop", "-t", "1", LOGIN], "hung": ["docker", "pause", LOGIN]}
    if do.get("reset") or do.get("state") == "up":  # undo a hang and a stop before touching sshd
        cmds += [["docker", "unpause", LOGIN], ["docker", "start", LOGIN]]
    if do.get("state") in state:
        cmds.append(state[do["state"]])
    exec_ = ["docker", "exec", LOGIN, "sh", "-c"]
    if do.get("reset"):
        cmds.append([*exec_, SSH_SCRIPTS["reset"]])
    if do.get("rotate_host_key"):
        cmds.append([*exec_, SSH_SCRIPTS["rotate_host_key"]])
    if do.get("reject_auth"):
        cmds.append([*exec_, SSH_SCRIPTS["reject_auth"]])
    if "node_sshd" in do:  # one node's sshd only; the container, and the login node's own sshd, stay up
        cmds += [["docker", "exec", f"workbench-{n}-1", "sh", "-c", SSH_SCRIPTS[f"node_sshd_{do['node_sshd']}"]] for n in do["nodes"]]
    if "max_sessions" in do:
        n = int(do["max_sessions"])
        cmds.append([*exec_, f"sed -i '/^MaxSessions/d' /etc/ssh/sshd_config && echo 'MaxSessions {n}' >> /etc/ssh/sshd_config && {SSHD_HUP}"])
    return cmds


def post(url: str, body: dict, timeout: float = 10) -> None:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    urllib.request.urlopen(req, timeout=timeout).close()


def execute(step: Step, poster=post, runner=checks.run, host: bool = True) -> Outcome:
    """Apply one step; the label names what was done, status says whether it worked.

    host=False is for callers without Docker access (the Web UI): Slurm steps are skipped, not attempted.
    """
    label = f"{step.target} {json.dumps(step.do, sort_keys=True)}"
    if step.target in NOT_BUILT:
        return Outcome(label, "skipped", f"{step.target} is not built yet")
    if step.target in HOST_TARGETS and not host:
        return Outcome(label, "skipped", f"{step.target} steps need the host CLI (no Docker access here)")
    try:
        if step.target == "ssh":
            for cmd in ssh_commands(step.do):
                r = runner(cmd)
                # unpause/start on a container that is already running are expected to complain; only real steps fail
                if r.returncode and cmd[1] not in ("unpause", "start"):
                    return Outcome(label, "failed", (r.stderr or r.stdout).strip())
        elif step.target == "slurm":
            for n in step.do.get("drain", []):
                r = runner(["docker", "exec", SLURMCTLD, "scontrol", "update", f"nodename={n}", "state=drain",
                            f"reason={step.do.get('reason', 'scenario')}"])
                if r.returncode:
                    return Outcome(label, "failed", (r.stderr or r.stdout).strip())
            for n in step.do.get("down", []):
                r = runner(["docker", "exec", SLURMCTLD, "scontrol", "update", f"nodename={n}", "state=down",
                            f"reason={step.do.get('reason', 'scenario')}"])
                if r.returncode:
                    return Outcome(label, "failed", (r.stderr or r.stdout).strip())
            for verb, key in (("pause", "power_off"), ("unpause", "power_on")):
                # the node stops answering (or answers again) and slurmctld notices by itself, as with a dead host
                for n in step.do.get(key, []):
                    r = runner(["docker", verb, f"workbench-{n}-1"])
                    if r.returncode and f"already {verb}d" not in r.stderr and "is not paused" not in r.stderr:
                        return Outcome(label, "failed", (r.stderr or r.stdout).strip())
            for n in step.do.get("resume", []):
                r = runner(["docker", "exec", SLURMCTLD, "scontrol", "update", f"nodename={n}", "state=resume"])
                if r.returncode:
                    return Outcome(label, "failed", (r.stderr or r.stdout).strip())
        elif step.target == "jira":
            if "tickets" in step.do:  # {tickets: {action: create|transition|comment|reset, ...}}
                poster(ENDPOINTS["jira-tickets"], step.do["tickets"])
            else:
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


def jira_search(jql: str) -> list[dict]:
    url = "http://127.0.0.1:8080/rest/api/2/search?fields=status,summary&jql=" + urllib.parse.quote(jql)
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())["issues"]
    except (OSError, ValueError, KeyError):
        return []


def jira_labels(labels: list[str]) -> bool:
    jql = "labels = " + " AND labels = ".join(labels)
    url = "http://127.0.0.1:8080/rest/api/2/search?jql=" + urllib.parse.quote(jql)
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())["total"] > 0
    except (OSError, ValueError):
        return False


def jira_status(jql: str, status: str, count: int | None = None) -> bool:
    """Every ticket matching `jql` is in `status` (and, when given, there are exactly `count` of them)."""
    found = jira_search(jql)
    return bool(found) and all(i["fields"]["status"]["name"] == status for i in found) and count in (None, len(found))


def expectation(exp: dict, runner=checks.run, host: bool = True):
    """(label, holds(), within seconds) for one assertion; holds is None when it cannot be evaluated here."""
    (kind, args), = exp.items()
    label = f"{kind} {json.dumps({k: v for k, v in args.items() if k != 'within'}, sort_keys=True)}"
    within = parse_duration(args.get("within", "0s"))
    if kind == "slurm.node_state" and host:
        return label, lambda: args["state"] in node_state(args["node"], runner), within
    if kind == "jira.issue_created":
        return label, lambda: jira_labels(args["labels"]), within
    if kind == "jira.issue_status":
        return label, lambda: jira_status(args["jql"], args["status"], args.get("count")), within
    if kind == "jira.issue_count":
        return label, lambda: len(jira_search(args["jql"])) == int(args["count"]), within
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
