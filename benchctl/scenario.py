"""Scenario engine (SPEC C-9): run scenarios/*.yaml against the live stack.

A scenario is `steps` (`at`, `target`, `do`) and optional `expect` assertions. Each step is one HTTP call to a
mock's /sim/inject (or a `scontrol` call in slurmctld). `ramp` steps expand into a series of temperature steps.
Targets whose component is not built yet (k8s, ticket-bot) are reported as skipped, not failed.
"""
import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import authfaults, checks, dcgm, k8s, topology
from .nodes import node_names

ENDPOINTS = {"gpu-exporter": "http://127.0.0.1:9400/sim/inject", "bmc-farm": "http://127.0.0.1:8000/sim/inject",
             "bcm-mock": "http://127.0.0.1:8081/sim/inject", "jira": "http://127.0.0.1:8080/sim/failure",
             "jira-tickets": "http://127.0.0.1:8080/sim/tickets", "jira-activity": "http://127.0.0.1:8080/sim/activity"}
JIRA_ACTIONS = ("create", "transition", "comment", "fault", "reset")
SLURMCTLD = "workbench-slurmctld-1"
LOGIN = "workbench-login-1"
JIRA_REAL = "workbench-jira-real-1"
JIRA_DB = "workbench-jira-db-1"
JIRA_REAL_URL = "http://127.0.0.1:8083"
JIRA_REAL_MODES = ("outage", "restore", "bad_logins", "clear_logins", "rate_limit", "rate_limit_off", "token_revoke", "token_restore",
                   "proxy_503", "proxy_429", "proxy_slow", "proxy_off")  # real faults on the real Jira (issue 303); proxy_* through benchctl/jira_proxy.py (issue 305)
JIRA_REAL_TICKET_ACTIONS = ("create", "transition", "comment")  # `tickets:` steps on the real Jira go through the REST driver (issue 306)
HOST_TARGETS = ("slurm", "ssh", "jira-real", "k8s", "dcgm", "auth")  # steps that need Docker on the host
SSH_STATES = ("down", "up", "hung")
NODE_SSHD_STATES = ("down", "up")
NOT_BUILT = ("ticket-bot",)
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
    if not path.is_file():  # a reference number works too: SC-02 or sc-2
        m = re.fullmatch(r"(?i)sc-?0*(\d+)", name.strip())
        hit = [s for s in list_all(root) if m and s.get("ref") == f"SC-{int(m.group(1)):02d}"]
        if not hit:
            raise ScenarioError(f"no scenario {name!r} (see: benchctl scenario list)")
        return hit[0]
    return yaml.safe_load(path.read_text())


def summary(sc: dict) -> str:
    """One header line before a run: reference, name, steps, length, what it touches."""
    steps = sc.get("steps", [])
    length = max((parse_duration(s["at"]) for s in steps), default=0)
    targets = ", ".join(sorted({s["target"] for s in steps}))
    return f"{sc.get('ref', '-')} {sc['name']}: {len(steps)} steps, {length:.0f} s, targets {targets}"


def page_support(step: Step) -> str:
    """"" when the Web UI can run the step (it has no Docker: only the governor's Slurm steps), else why not. The same rules as `_apply` with host=False."""
    if step.target in NOT_BUILT:
        return f"{step.target} is not built yet"
    if step.target == "switch" or step.target == "slurm" and (set(step.do) & set(DAEMON_KEYS) or set(step.do) & set(SLURM_STATES)):
        return ""
    if step.target in HOST_TARGETS:
        return f"{step.target} steps need the host CLI (no Docker access in the page)"
    return ""


def preview(sc: dict) -> dict:
    """What the Test run page shows before Play (issue 509): the timeline as the run will play it, each step and expectation marked
    with whether the page can do it, and the command that runs all of it."""
    steps = [{"at": s.at, "target": s.target, "do": s.do, "page": not (why := page_support(s)), "why": why} for s in plan(sc)]
    expects = []
    for exp in sc.get("expect") or []:
        label, holds, _ = expectation(exp, host=False)
        expects.append({"label": label, "page": holds is not None})
    return {"name": sc["name"], "ref": sc.get("ref", ""), "description": sc.get("description", ""),
            "length": max((s["at"] for s in steps), default=0), "steps": steps, "expects": expects,
            "cli": f"./scripts/benchctl scenario run {sc['name']}",
            "cli_only": sum(1 for s in steps if not s["page"]) + sum(1 for e in expects if not e["page"])}


def list_all(root: Path = SCENARIOS) -> list[dict]:
    return sorted((yaml.safe_load(p.read_text()) for p in root.glob("*.yaml")), key=lambda s: s["name"])


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
        if t in ("jira-tickets", "jira-activity"):  # the endpoint key, not a target: steps say `jira` with `tickets:`
            problems.append(f"step {i}: unknown target {t!r}")
            continue
        if t == "switch":
            problems += [f"step {i}: {m}" for m in topology.switch_problems(s.get("do") or {}, sim)]
            continue
        if t not in ENDPOINTS and t not in HOST_TARGETS and t not in NOT_BUILT:
            problems.append(f"step {i}: unknown target {t!r}")
        do = s.get("do") or {}
        if t == "ssh":
            problems += [f"step {i}: {m}" for m in ssh_problems(do)]
        if t == "jira" and "tickets" in do:
            problems += [f"step {i}: {m}" for m in jira_ticket_problems(do["tickets"])]
        if t == "slurm":
            problems += [f"step {i}: {m}" for m in daemon_problems(do)]
        if t == "dcgm" and sum(bool(do.get(v)) for v in ("stop", "start", "restart")) != 1:
            problems.append(f"step {i}: dcgm needs one of stop, start, restart: true")
        if t == "auth":
            problems += [f"step {i}: {m}" for m in authfaults.problems(do, nodes, {u["name"] for u in sim.get("users", [])})]
            continue
        if t == "k8s":
            problems += [f"step {i}: {m}" for m in k8s.step_problems(do, {n for n, _ in k8s.node_list(sim)})]
        if t == "jira-real" and "tickets" in do:
            problems += [f"step {i}: {m}" for m in jira_ticket_problems(do["tickets"], JIRA_REAL_TICKET_ACTIONS)]
        elif t == "jira-real" and do.get("fault") not in JIRA_REAL_MODES:
            problems.append(f"step {i}: jira-real needs fault: one of {', '.join(JIRA_REAL_MODES)}, or tickets:")
        if t == "k8s":  # its node names are Kubernetes nodes, checked above
            continue
        names = [n for key in ("nodes", "drain", "down", "resume", "power_off", "power_on", "slurmd_stop", "slurmd_start",
                               "slurmd_restart", "power_down", "power_up", "reboot") for n in do.get(key) or []]
        if isinstance(do.get("submit"), dict) and do["submit"].get("node"):
            names.append(do["submit"]["node"])
        problems += [f"step {i}: unknown node {n!r}" for n in names if n not in nodes]
    return problems


def daemon_problems(do: dict) -> list[str]:
    sub = do.get("submit")
    if sub is None:
        return []
    if not isinstance(sub, dict) or not sub.get("node"):
        return ["submit needs a mapping with `node`"]
    return [f"submit: unknown key {k!r}" for k in sorted(set(sub) - SUBMIT_KEYS)]


def jira_ticket_problems(tk, actions=JIRA_ACTIONS) -> list[str]:
    if not isinstance(tk, dict) or tk.get("action") not in actions:
        return [f"jira tickets needs action: one of {', '.join(actions)}"]
    need = {"create": ("summary",), "transition": ("jql", "to"), "comment": ("jql", "body"), "fault": ("node",), "reset": ()}[tk["action"]]
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


HOLD = "/run/workbench/slurmd.hold"  # images/node-base/slurmd-run waits while this file exists
DAEMON_KEYS = ("slurmd_stop", "slurmd_start", "slurmd_restart", "slurmctld_restart", "submit", "cancel")
SUBMIT_KEYS = {"node", "user", "gpus", "duration"}
SLURM_STATES = {"drain": "drain", "down": "down", "resume": "resume", "power_off": "off", "power_on": "on"}


def disruptive(sc: dict) -> bool:
    """True when a step restarts or stops a Slurm daemon or starts a job: the governor must admit that first (GV-9)."""
    return any(set(s.get("do") or {}) & set(DAEMON_KEYS) for s in sc.get("steps", []))


def submit_command(sub: dict, sim: dict | None = None) -> list[str]:
    """The same job `benchctl submit` makes (a `sleep` for `duration` s, pinned to `node`, `gpus` GPUs on a GPU node)."""
    from . import workload

    sim = sim or yaml.safe_load(Path("sim.yaml").read_text())
    return workload.command(sim, sub["node"], int(sub.get("gpus", 0)), int(sub.get("duration", 600)), sub.get("user"))


def daemon_commands(do: dict) -> list[list[str]]:
    """Docker commands for the resilience steps. A stop creates the hold file first so the supervisor does not bring
    slurmd back; `pkill -x slurmd` stops only the daemon (slurmstepd, the job's step process, is a different name)."""
    cmds: list[list[str]] = []
    for n in do.get("slurmd_stop", []):
        c = f"workbench-{n}-1"
        cmds += [["docker", "exec", c, "touch", HOLD], ["docker", "exec", c, "pkill", "-x", "slurmd"]]
    for n in do.get("slurmd_restart", []):  # no hold file: the supervisor starts slurmd again by itself
        cmds.append(["docker", "exec", f"workbench-{n}-1", "pkill", "-x", "slurmd"])
    for n in do.get("slurmd_start", []):
        cmds.append(["docker", "exec", f"workbench-{n}-1", "rm", "-f", HOLD])
    if do.get("slurmctld_restart"):  # slurmctld is the container's main process, so restarting it is restarting the container
        cmds.append(["docker", "restart", "-t", "10", SLURMCTLD])
    if do.get("submit"):
        cmds.append(submit_command(do["submit"]))
    if do.get("cancel"):
        cmds.append(["docker", "exec", SLURMCTLD, "scancel", "--quiet", "-u", "alice"])
    return cmds


def power_commands(do: dict) -> list[list[str]]:
    """Slurm's own power save and reboot (issue 545), asked of the real slurmctld: `power_down` and `power_up` are `scontrol power down|up`
    (POWERED_DOWN, POWERING_UP; the BMC farm is told by SuspendProgram and ResumeProgram), `reboot` is `scontrol reboot ASAP` (the node's
    RebootProgram cycles it through the BMC and restarts slurmd, and it returns to service: nextstate=resume). `power_off` and `power_on`
    stay what they were: a node that dies and is revived behind Slurm's back, as a power cut would."""
    cmds: list[list[str]] = []
    for key, verb in (("power_down", "down"), ("power_up", "up")):
        if do.get(key):
            cmds.append(["docker", "exec", SLURMCTLD, "scontrol", "power", verb, ",".join(do[key])])
    if do.get("reboot"):
        cmds.append(["docker", "exec", SLURMCTLD, "scontrol", "reboot", "ASAP", "nextstate=resume",
                     f"reason={do.get('reason', 'scenario reboot')}", ",".join(do["reboot"])])
    return cmds


def side_effects(step: Step) -> list[Step]:
    """A power change on the BMC is also a change on the BCM, the GPU exporter and the node itself (and so in Slurm):
    a scenario says it once, as the page's Power off does. `clear` powers the node on again, so it thaws it too. Every
    fault also files, and every recovery resolves, the node's Jira ticket (the mock's `fault` action, #157)."""
    do = step.do
    nodes = do.get("nodes")
    if not nodes or step.target not in ("bmc-farm", "gpu-exporter"):
        return []
    tickets, faults, recovered = [], [], []
    if step.target == "bmc-farm":
        if do.get("power") == "Off":
            faults.append(("power", "BMC power Off"))
        if float(do.get("temp") or 0) >= 85:
            faults.append(("overheat", f"CPU at {do['temp']} C"))
        if int(do.get("fan_fail") or 0) > 0:
            faults.append(("fan", f"{do['fan_fail']} fan(s) failed"))
        if do.get("clear"):
            recovered.append("")  # every kind
        elif do.get("power") == "On":
            recovered.append("power")
    else:
        if int(do.get("xid") or 0) > 0:
            faults.append(("xid", f"XID {do['xid']}"))
        if int(do.get("ib_down") or 0) > 0:
            faults.append(("ib", f"{do['ib_down']} InfiniBand port(s) down"))
        if do.get("nic_flap"):
            faults.append(("nic", "eth0 link flapping"))
        if do.get("clear"):
            recovered += ["xid", "ib", "nic"]
        elif "ib_down" in do and not int(do["ib_down"] or 0):
            recovered.append("ib")
        elif "nic_flap" in do and not do["nic_flap"]:
            recovered.append("nic")
    for n in nodes:
        tickets += [Step(step.at, "jira", {"tickets": {"action": "fault", "node": n, "kind": k, "detail": d}}) for k, d in faults]
        tickets += [Step(step.at, "jira", {"tickets": {"action": "fault", "node": n, "kind": k, "open": False,
                                                      "detail": "The node reports healthy again."}}) for k in recovered]
    on = do.get("power") == "On" or bool(do.get("clear"))
    real = dcgm_steps(step, nodes)
    if step.target != "bmc-farm" or (do.get("power") != "Off" and not on):
        return [*real, *tickets]
    return [Step(step.at, "bcm-mock", {"nodes": nodes, "state": "UP" if on else "DOWN"}),
            Step(step.at, "gpu-exporter", {"nodes": nodes, "down": not on}), *real,
            Step(step.at, "slurm", {"power_on" if on else "power_off": nodes}), *tickets]


def dcgm_steps(step: Step, nodes: list[str]) -> list[Step]:
    """What a fault or recovery means for the real DCGM exporters (the `dcgm` part, issue 435): a dead node's exporter stops answering and
    comes back with the node; DCGM keeps the last XID until its host engine restarts, so a clear restarts the exporter. A node with no
    real exporter is skipped when the step runs. Needs Docker: the Web UI plays these as skipped."""
    do = step.do
    if step.target == "bmc-farm":
        action = "stop" if do.get("power") == "Off" else "restart" if do.get("clear") else "start" if do.get("power") == "On" else None
    else:
        action = "restart" if do.get("clear") else "stop" if do.get("down") else "start" if "down" in do else None
    return [Step(step.at, "dcgm", {"nodes": nodes, action: True})] if action else []


def _dcgm(step: Step, label: str, runner) -> Outcome:
    """Stop, start or restart the real exporter of each node that has one (SPEC C-22); a node without one needs nothing."""
    sim = k8s.current_sim()
    verb = next(v for v in ("stop", "start", "restart") if step.do.get(v))
    done = 0
    for n in step.do.get("nodes", []):
        if not (c := dcgm.container(sim, n)):
            continue
        r = runner(["docker", verb, *(["-t", "1"] if verb != "start" else []), c])
        if r.returncode and "No such container" not in r.stderr:
            return Outcome(label, "failed", (r.stderr or r.stdout).strip())
        done += r.returncode == 0
    return Outcome(label, "ok", f"{verb} {done} real exporter(s)" if done else "no real exporter on these nodes")


def execute(step: Step, poster=post, runner=checks.run, host: bool = True, slurm=None, daemon=None) -> Outcome:
    """Apply one step (and what it implies, see side_effects); the label names what was done, status says whether it worked.

    host=False is for callers without Docker access (the Web UI): `slurm(node, state, reason)` (the governor) takes
    Slurm steps when given, otherwise they are skipped, not attempted; `daemon(do)` -> (done, why not) takes the job and
    daemon steps (DAEMON_KEYS) the same way.
    """
    main = _apply(step, poster, runner, host, slurm, daemon)
    if main.status != "ok":
        return main
    failed, notes = [], []
    for extra in side_effects(step):
        o = _apply(extra, poster, runner, host, slurm, daemon)
        if extra.target == "jira" and o.status == "failed":  # no Jira in this cluster: a fault is still a fault
            o = Outcome(o.label, "skipped", "no Jira to file the ticket in")
        (failed if o.status == "failed" else notes if o.status == "skipped" else []).append(f"{extra.target}: {o.detail}")
    if failed:
        return Outcome(main.label, "failed", "; ".join(failed))
    return Outcome(main.label, "ok", "; ".join(notes))


def _apply(step: Step, poster, runner, host: bool, slurm, daemon=None) -> Outcome:
    label = f"{step.target} {json.dumps(step.do, sort_keys=True)}"
    if step.target == "switch":  # a leaf switch is its nodes: down drains exactly those, up resumes exactly those (issue 543)
        sim = k8s.current_sim()
        if topology.switch_problems(step.do, sim):
            return Outcome(label, "failed", "; ".join(topology.switch_problems(step.do, sim)))
        inner = _apply(Step(step.at, "slurm", topology.switch_step(step.do, sim)), poster, runner, host, slurm, daemon)
        return Outcome(label, inner.status, inner.detail)
    if step.target in NOT_BUILT:
        return Outcome(label, "skipped", f"{step.target} is not built yet")
    if step.target == "slurm" and not host and daemon and set(step.do) & set(DAEMON_KEYS):  # no Docker: the governor does it
        ok, detail = daemon(step.do)
        return Outcome(label, "ok" if ok else "skipped", detail)  # a refusal (calm levels only) is not a failure of the scenario
    if step.target == "slurm" and not host and slurm and not set(step.do) & set(DAEMON_KEYS) and set(step.do) & set(SLURM_STATES):  # no Docker: the governor does it
        for key, state in SLURM_STATES.items():
            for n in step.do.get(key, []):
                ok, detail = slurm(n, state, step.do.get("reason"))
                if not ok:
                    return Outcome(label, "skipped", detail)  # a refusal (calm levels only) is not a failure of the scenario
        return Outcome(label, "ok")
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
            for cmd in daemon_commands(step.do):
                r = runner(cmd)
                if r.returncode and cmd[3:4] != ["pkill"]:  # pkill exits 1 when slurmd is already down: that is the goal
                    return Outcome(label, "failed", (r.stderr or r.stdout).strip())
            for cmd in power_commands(step.do):
                r = runner(cmd)
                if r.returncode:
                    return Outcome(label, "failed", (r.stderr or r.stdout).strip())
        elif step.target == "dcgm":
            return _dcgm(step, label, runner)
        elif step.target == "k8s":
            k8s.write_pod(step.do)
            for args in k8s.step_args(step.do):
                r = runner(k8s.kubectl_cmd(k8s.current_sim(), *args))
                if r.returncode:
                    return Outcome(label, "failed", (r.stderr or r.stdout).strip()[-300:])
        elif step.target == "jira-real":
            return jira_real_fault(step, label, runner)
        elif step.target == "auth":
            bad = authfaults.apply(step.do, runner, poster, k8s.current_sim())
            return Outcome(label, "failed", bad) if bad else Outcome(label, "ok")
        elif step.target == "jira":
            if "tickets" in step.do:  # {tickets: {action: create|transition|comment|reset, ...}}
                poster(ENDPOINTS["jira-tickets"], step.do["tickets"])
            elif "activity" in step.do:  # {activity: {on: true, every: 10}}
                poster(ENDPOINTS["jira-activity"], step.do["activity"])
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


def jira_real_fault(step: Step, label: str, runner) -> Outcome:
    """Real faults on the real Jira (no fault proxy, issue 303): `outage` pauses its container (clients time out, as with a
    hung node), `restore` unpauses it, `bad_logins` sends wrong-password logins until Jira's own CAPTCHA lock answers 403
    (the 401 then 403 pattern the Atlassian community reports); `clear_logins` resets the failed-login count (as an admin does in User management)."""
    if "tickets" in step.do:
        return jira_real_tickets(step, label)
    fault = step.do["fault"]
    if fault.startswith("proxy_"):
        return jira_real_proxy(step, label)
    if fault in ("token_revoke", "token_restore"):
        return jira_real_token(step, label)
    if fault in ("outage", "restore"):
        r = runner(["docker", "pause" if fault == "outage" else "unpause", JIRA_REAL])
        if r.returncode and "already" not in r.stderr and "is not paused" not in r.stderr:
            return Outcome(label, "failed", (r.stderr or r.stdout).strip())
        return Outcome(label, "ok")
    if fault == "clear_logins":  # what an admin does in User management ("reset failed login count"): zero it in Jira's own table
        r = runner(["docker", "exec", JIRA_DB, "psql", "-U", "jira", "-d", "jira", "-qc",
                    "update cwd_user_attributes set attribute_value='0' where attribute_name='login.currentFailedCount'"])
        return Outcome(label, "failed", (r.stderr or r.stdout).strip()) if r.returncode else Outcome(label, "ok")
    if fault in ("rate_limit", "rate_limit_off"):
        return jira_real_rate_limit(step, label, runner)
    user, n = str(step.do.get("user", "alice")), int(step.do.get("count", 5))
    for _ in range(n):
        req = urllib.request.Request(f"{JIRA_REAL_URL}/rest/api/2/myself", headers={
            "Authorization": "Basic " + base64.b64encode(f"{user}:wrong-password".encode()).decode()})
        try:
            urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError:
            pass  # 401, then 403 once the CAPTCHA lock is on: both are the goal
        except OSError as e:
            return Outcome(label, "failed", str(e))
    return Outcome(label, "ok")


def jira_real_proxy(step: Step, label: str) -> Outcome:
    """`proxy_503`, `proxy_429`, `proxy_slow` (count, delay, retry_after) fail the next requests that reach the fault proxy
    (127.0.0.1:8084, started with the scenario); `proxy_off` clears it. A client on 127.0.0.1:8083 never meets these (issue 305)."""
    from . import jira_proxy
    fault = step.do["fault"]
    body = None if fault == "proxy_off" else {"mode": fault.removeprefix("proxy_"), **{k: step.do[k] for k in ("count", "delay", "retry_after") if k in step.do}}
    try:
        jira_proxy.shared().fault.set(body)
    except (OSError, ValueError) as e:
        return Outcome(label, "failed", f"fault proxy: {e}")
    return Outcome(label, "ok")


def jira_real_token(step: Step, label: str) -> Outcome:
    """`token_revoke` deletes the user's personal access token in Jira (a client holding it gets a real 401); `token_restore`
    makes a new one and rewrites PAT_<USER> in compose/generated/secrets/jira-real.env (never printed). Default user alice:
    the ticket driver uses admin's token, so it keeps working."""
    from . import jira_real
    user = str(step.do.get("user", "alice"))
    try:
        if step.do["fault"] == "token_revoke":
            n = jira_real.revoke_token(user)
            return Outcome(label, "ok" if n else "skipped", "" if n else f"{user} had no workbench token")
        jira_real.reissue_token(user)
    except (jira_real.JiraRealError, OSError) as e:
        return Outcome(label, "failed", str(e))
    return Outcome(label, "ok")


def real_driver():
    from .jira_driver import JiraDriver
    return JiraDriver.for_target("real")


def jira_real_tickets(step: Step, label: str) -> Outcome:
    """`tickets: {action: create|transition|comment, ...}` on the real Jira through the standard REST API: the same body as the
    mock's `/sim/tickets` (create: summary with {n}, count, labels, priority, description; transition: jql and to; comment: jql and body)."""
    from .jira_driver import JiraError
    tk = step.do["tickets"]
    try:
        drv = real_driver()
        if tk["action"] == "create":
            for n in range(1, min(int(tk.get("count", 1)), 200) + 1):
                drv.create(str(tk["summary"]).replace("{n}", str(n)), description=str(tk.get("description", "")).replace("{n}", str(n)),
                           labels=tk.get("labels") or [], priority=tk.get("priority", "Medium"))
        else:
            for hit in drv.search(str(tk["jql"])):
                if tk["action"] == "comment":
                    drv.comment(hit["key"], str(tk["body"]))
                else:
                    drv.move(hit["key"], tk["to"])
    except JiraError as e:
        return Outcome(label, "failed", str(e))
    except OSError as e:
        return Outcome(label, "failed", f"real Jira unreachable: {e}")
    return Outcome(label, "ok")


def rate_limit_sql(on: bool, capacity: int = 3, fill_rate: int = 1) -> str:
    """Jira's own rate limiting (admin Rate limiting page) set in its tables, then the settings version is bumped so the
    running Jira reloads it (no restart; the reload job runs about once a minute, issue 303)."""
    mode = "ON" if on else "OFF"
    return (f"update \"AO_AC3877_SYSTEM_RL_SETTINGS\" set \"MODE\"='{mode}', \"CAPACITY\"={capacity}, \"FILL_RATE\"={fill_rate}, "
            "\"INTERVAL_FREQUENCY\"=1, \"INTERVAL_TIME_UNIT\"='SECONDS'; "
            "update \"AO_AC3877_SETTINGS_VERSION\" set \"VERSION\"=\"VERSION\"+1;")


def jira_real_rate_limit(step: Step, label: str, runner) -> Outcome:
    on = step.do["fault"] == "rate_limit"
    sql = rate_limit_sql(on, int(step.do.get("capacity", 3)), int(step.do.get("fill_rate", 1)))
    r = runner(["docker", "exec", JIRA_DB, "psql", "-U", "jira", "-d", "jira", "-qc", sql])
    return Outcome(label, "failed", (r.stderr or r.stdout).strip()) if r.returncode else Outcome(label, "ok")


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


def real_search(jql: str) -> list[dict]:
    """The same search on the real Jira (an expectation with `target: real`); an expectation that cannot ask does not hold."""
    try:
        return real_driver().search(jql)
    except Exception:  # noqa: BLE001  JiraError, OSError, a missing setup: the expectation just does not hold
        return []


def jira_status(jql: str, status: str, count: int | None = None, search=None) -> bool:
    """Every ticket matching `jql` is in `status` (and, when given, there are exactly `count` of them)."""
    found = (search or jira_search)(jql)
    return bool(found) and all(i["fields"]["status"]["name"] == status for i in found) and count in (None, len(found))


def job_states(node: str, runner=checks.run) -> list[str]:
    r = runner(["docker", "exec", SLURMCTLD, "squeue", "-h", "-w", node, "-o", "%T"])
    return r.stdout.split() if r.returncode == 0 else []


def stepd_alive(node: str, runner=checks.run) -> bool:
    return runner(["docker", "exec", f"workbench-{node}-1", "pgrep", "-x", "slurmstepd"]).returncode == 0


def expectation(exp: dict, runner=checks.run, host: bool = True):
    """(label, holds(), within seconds) for one assertion; holds is None when it cannot be evaluated here."""
    (kind, args), = exp.items()
    label = f"{kind} {json.dumps({k: v for k, v in args.items() if k != 'within'}, sort_keys=True)}"
    within = parse_duration(args.get("within", "0s"))
    if kind == "slurm.node_state" and host:
        return label, lambda: args["state"] in node_state(args["node"], runner), within
    if kind == "slurm.job_state" and host:  # a job on `node` is in `state` (RUNNING, PENDING, ...)
        return label, lambda: args["state"] in job_states(args["node"], runner), within
    if kind == "slurm.stepd" and host:  # a slurmstepd runs on `node` (alive: true) or none does (alive: false)
        return label, lambda: stepd_alive(args["node"], runner) == bool(args.get("alive", True)), within
    if kind == "k8s.node_ready" and host:  # the node's Ready condition is `ready` (default true)
        return label, lambda: k8s.node_ready(args["node"], runner) == bool(args.get("ready", True)), within
    if kind == "k8s.pod_status" and host:  # the pod's phase or a container's waiting/terminated reason is `reason`
        return label, lambda: args["reason"] in k8s.pod_reasons(args["namespace"], args["name"], runner).split(), within
    if kind == "jira.issue_created":
        return label, lambda: jira_labels(args["labels"]), within
    search = (lambda q: real_search(q)) if args.get("target") == "real" else (lambda q: jira_search(q))  # `target: real` asks the real Jira
    if kind == "jira.issue_status":
        return label, lambda: jira_status(args["jql"], args["status"], args.get("count"), search), within
    if kind == "jira.issue_count":
        return label, lambda: len(search(args["jql"])) == int(args["count"]), within
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
        on_step=lambda step, outcome: None, on_expect=lambda outcome: None, slurm=None, daemon=None,
        on_before=lambda step: None) -> Report:
    """Play the scenario: wait for each step's time (divided by speed x time_dilation) and apply it.

    Every expectation is watched from the start: it passes the moment it holds (even if a later step undoes it) and
    fails if its `within` (from the start) runs out first.
    """
    factor = max(speed * dilation, 1e-6)
    report = Report(sc["name"])
    if host and any(s["target"] == "jira-real" and str((s.get("do") or {}).get("fault", "")).startswith("proxy_") for s in sc.get("steps", [])):
        from . import jira_proxy
        try:  # up before the first step, so a client can already be pointed at it
            say(f"fault proxy for the real Jira on 127.0.0.1:{jira_proxy.shared().port} (point the client there for the proxy faults)")
        except OSError as e:
            say(f"fault proxy could not start: {e}")
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
        on_before(step)
        outcome = execute(step, poster, runner, host, slurm, daemon)
        report.steps.append(outcome)
        on_step(step, outcome)
        say(f"[{step.at:6.1f}s] {outcome.status:7} {outcome.label}" + (f"  ({outcome.detail})" if outcome.detail else ""))
        watch()
    while pending and not should_stop():
        sleep(1.0)
        watch()
    report.stopped = should_stop()
    return report
