"""Control socket: JSON-lines over a Unix socket with a fixed allow-list (SPEC 4a, W-3)."""
import json
import logging
import os
import re
import socketserver
import threading
from collections.abc import Callable
from pathlib import Path

from . import netem

log = logging.getLogger("governor.control")

ALLOWED = {"status", "scale", "freeze", "thaw", "stop", "start", "set-profile", "admit", "apply-plan", "node-state", "submit-job", "slurm-step", "k8s-step", "slurm-trace", "slurm-stats", "capture-start", "capture-read", "capture-stop", "kube-audit", "refresh-jira-licence", "ticket-bot-set", "ticket-bot-get", "netem-set", "netem-clear"}
LAB_VERBS = {"lab-validate"}  # the config lab verbs (ADR 0049); issue 553 adds lab-apply and lab-restore here
ALLOWED |= LAB_VERBS
LAB_APPLY_VERBS = {"lab-apply", "lab-restore"}  # issue 553: apply a side of a lab; return to the plain cluster (never the A/B snapshot restore, which gets its own name)
ALLOWED |= LAB_APPLY_VERBS
LAB_RUN_VERBS = {"lab-run", "lab-stop"}  # issue 554: start a supervised replay run (L0-L1, admitted); stop it (any level)
ALLOWED |= LAB_RUN_VERBS
TARGET_HOURS = (0.5, 6.0)  # mirrors benchctl.lab_slurm.MIN_TARGET_HOURS, MAX_TARGET_HOURS
MAX_SEED = 2**31 - 1
PROFILE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")  # a file profiles/NAME.yaml: no path, no leading dash (#986); `benchctl up` reports a missing one
SCENARIO_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")  # a scenario file's name (scenarios/NAME.yaml); the governor checks that the file exists
MAX_REQUEST = 4096
TARGETED = {"freeze", "thaw", "stop", "start"}  # require a known group name

Handler = Callable[[str, dict], dict]


LAB_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")  # mirrors benchctl.lab.NAME
NODE_RE = re.compile(r"[a-z]{2,8}[0-9]{3}")  # sim.yaml node names: cn001, gpu002
SLURM_STEPS = ("slurmd-restart", "slurmd-stop", "slurmd-start", "slurmctld-restart", "cancel-jobs")  # resilience scenarios (#220)
NODE_STEPS = ("slurmd-restart", "slurmd-stop", "slurmd-start")
K8S_NAME_RE = re.compile(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?")  # mirrors benchctl.k8s._NAME (a DNS-1123 label)
K8S_FAULTS = ("crashloop", "oom", "imagepull", "jobfail")  # mirrors benchctl.k8s.FAULTS
K8S_NODE_KEYS = ("down", "up", "cordon", "uncordon")  # node steps; the only other keys are pod, delete_pod and scale (issue 386)
TICKET_RANGES = {"every": (10, 3600), "resolve": (10, 86400), "ignore": (0, 1), "reopen": (0, 1)}  # mirrors benchctl.workload.TICKET_KEYS
REASON_RE = re.compile(r"[A-Za-z0-9 .,:_/()+-]{0,60}")  # a drain reason: short, no shell characters
LOGIN_MODES = {"ssh", "teleport", "both"}  # mirrors benchctl.logins.MODES
BMC_GROUPS = {"slurm-cpu", "slurm-gpu", "k8s-cpu", "k8s-gpu"}  # mirrors benchctl.nodes.GROUPS
LOGIN_KINDS = {"slurm", "k8s", "both"}  # mirrors benchctl.logins.KINDS; an entry is MODE or MODE:KIND
PLAN_PARTS = {"jira", "jira-real", "bmc", "bcm", "accounting", "teleport", "k8s", "jump", "dcgm", "real-gpu", "objectstore"}  # mirrors benchctl.workbench.PARTS


def _int(v, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _kname(v) -> bool:
    return isinstance(v, str) and K8S_NAME_RE.fullmatch(v) is not None


def k8s_step_error(req: dict) -> str | None:
    """`k8s-step` (issue 386): the page plays a scenario's Kubernetes step through the governor, which runs `benchctl set k8s-step` as the user.
    Only these keys, each with a fixed shape and no free text: `down|up|cordon|uncordon` (1..8 node names), `pod` (namespace, name, optional fault from
    a fixed list, gpus 0..8, node, priority_class), `delete_pod` (namespace, name), `scale` (namespace, deploy, replicas 0..100). benchctl checks it again against the cluster."""
    do = req.get("do")
    if not (isinstance(do, dict) and do and set(do) <= {*K8S_NODE_KEYS, "pod", "delete_pod", "scale"}):
        return f"k8s-step needs do: an object with only {', '.join((*K8S_NODE_KEYS, 'pod', 'delete_pod', 'scale'))}"
    for k in K8S_NODE_KEYS:
        if k in do and not (isinstance(do[k], list) and 1 <= len(do[k]) <= 8 and all(isinstance(n, str) and NODE_RE.fullmatch(n) for n in do[k])):
            return f"k8s-step {k} needs 1 to 8 node names"
    if "pod" in do:
        p = do["pod"]
        if not (isinstance(p, dict) and set(p) <= {"namespace", "name", "fault", "gpus", "node", "priority_class"} and _kname(p.get("namespace")) and _kname(p.get("name"))
                and p.get("fault") in (None, *K8S_FAULTS) and (p.get("gpus") is None or _int(p["gpus"], 0, 8))
                and (p.get("node") is None or (isinstance(p["node"], str) and NODE_RE.fullmatch(p["node"])))
                and (p.get("priority_class") is None or _kname(p["priority_class"]))):
            return "k8s-step pod needs namespace and name (lowercase letters, digits, -), and optionally fault, gpus 0..8, node, priority_class"
    if "delete_pod" in do and not (isinstance(do["delete_pod"], dict) and set(do["delete_pod"]) == {"namespace", "name"}
                                   and _kname(do["delete_pod"]["namespace"]) and _kname(do["delete_pod"]["name"])):
        return "k8s-step delete_pod needs namespace and name"
    if "scale" in do and not (isinstance(do["scale"], dict) and set(do["scale"]) == {"namespace", "deploy", "replicas"}
                              and _kname(do["scale"]["namespace"]) and _kname(do["scale"]["deploy"]) and _int(do["scale"]["replicas"], 0, 100)):
        return "k8s-step scale needs namespace, deploy and replicas 0..100"
    return None


def lab_args_error(cmd: str, req: dict) -> str | None:
    """Shared argument check of every lab verb: a lab name that matches the pattern and an optional side (baseline or candidate)."""
    name = req.get("name")
    if not (isinstance(name, str) and LAB_NAME_RE.fullmatch(name) and req.get("side", "baseline") in ("baseline", "candidate")):
        return f"{cmd} needs name:NAME (lowercase letters, digits, -) and an optional side: baseline"
    return None


def lab_run_error(req: dict) -> str | None:
    """`lab-run`: a lab name, then only an optional bool `ab`, a number `target_hours` 0.5..6, an int `seed` 0..2^31-1 and a `scenario`
    name (lowercase letters, digits, -; the governor checks it against scenarios/*.yaml) (W-3: nothing free-form)."""
    if not (isinstance(req.get("name"), str) and LAB_NAME_RE.fullmatch(req["name"])):
        return f"{req.get('cmd')} needs name:NAME (lowercase letters, digits, -)"
    if req.get("cmd") == "lab-stop":
        return None if isinstance(req.get("force", False), bool) else "lab-stop force must be true or false"
    if "ab" in req and not isinstance(req["ab"], bool):
        return "lab-run ab must be true or false"
    h = req.get("target_hours")
    if h is not None and not (isinstance(h, (int, float)) and not isinstance(h, bool) and TARGET_HOURS[0] <= h <= TARGET_HOURS[1]):
        return f"lab-run target_hours must be a number from {TARGET_HOURS[0]:g} to {TARGET_HOURS[1]:g}"
    s = req.get("seed")
    if s is not None and not (isinstance(s, int) and not isinstance(s, bool) and 0 <= s <= MAX_SEED):
        return f"lab-run seed must be an int 0..{MAX_SEED}"
    sc = req.get("scenario")
    if sc is not None and not (isinstance(sc, str) and SCENARIO_RE.fullmatch(sc)):
        return "lab-run scenario must be a scenario name such as drain-mid-job (lowercase letters, digits, -)"
    return None


def plan_error(req: dict) -> str | None:
    """Only small integers and known part names reach the command line; nothing free-form."""
    for key, lo, hi in (("cpu_nodes", 0, 64), ("gpu_nodes", 0, 16), ("gpus_per_node", 1, 8)):
        v = req.get(key)
        if not (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi):
            return f"apply-plan needs {key}: int {lo}..{hi}"
    for key, hi in (("k8s_nodes", 16), ("k8s_cpu_nodes", 64)):  # optional: absent keeps sim.yaml's count
        v = req.get(key)
        if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= hi):
            return f"apply-plan {key} must be an int 0..{hi}"
    v = req.get("dcgm_nodes")  # optional: GPU nodes with a real DCGM exporter (benchctl.dcgm.MAX_NODES)
    if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 8):
        return "apply-plan dcgm_nodes must be an int 1..8"
    parts = req.get("parts")
    if not (isinstance(parts, list) and all(isinstance(p, str) and p in PLAN_PARTS for p in parts)):
        return f"apply-plan parts must be a list of: {', '.join(sorted(PLAN_PARTS))}"
    nodes = req.get("bmc_nodes")  # optional: the nodes that get a BMC; absent means every node
    if nodes is not None and not (isinstance(nodes, list) and len(nodes) <= 80
                                  and all(isinstance(n, str) and NODE_RE.fullmatch(n) for n in nodes)):
        return "apply-plan bmc_nodes must be a list of node names such as cn001"
    groups = req.get("bmc_groups")  # optional: the node groups that get BMCs; absent means every group
    if groups is not None and not (isinstance(groups, list) and all(isinstance(g, str) and g in BMC_GROUPS for g in groups)):
        return f"apply-plan bmc_groups must be a list of: {', '.join(sorted(BMC_GROUPS))}"
    bcm_k8s = req.get("bcm_k8s")  # optional: whether BCM manages the Kubernetes nodes (issue 413)
    if bcm_k8s is not None and not isinstance(bcm_k8s, bool):
        return "apply-plan bcm_k8s must be true or false"
    ctl = req.get("controller")  # optional: a controller node (issue 416)
    if ctl is not None and not isinstance(ctl, bool):
        return "apply-plan controller must be true or false"
    modes = req.get("login_nodes")  # optional: one auth mode per login node; absent keeps sim.yaml's
    if modes is not None and not (isinstance(modes, list) and 1 <= len(modes) <= 8 and all(isinstance(m, str) and m.partition(":")[0] in LOGIN_MODES and (":" not in m or m.partition(":")[2] in LOGIN_KINDS) for m in modes)):
        return f"apply-plan login_nodes must be 1 to 8 of: {', '.join(sorted(LOGIN_MODES))}, each optionally :{'|'.join(sorted(LOGIN_KINDS))}"
    return None


def dispatch(line: bytes, handle: Handler, groups: Callable[[], set[str]]) -> dict:
    """Validate one request and route it. Never raises."""
    try:
        if len(line) > MAX_REQUEST:
            return {"ok": False, "error": "request too large"}
        req = json.loads(line)
        if not isinstance(req, dict):
            return {"ok": False, "error": "request must be an object"}
        cmd = req.get("cmd")
        if cmd not in ALLOWED:
            return {"ok": False, "error": f"unknown command: {cmd!r}"}
        if cmd in TARGETED and req.get("target") not in groups():
            return {"ok": False, "error": f"unknown group: {req.get('target')!r}"}
        if cmd == "scale" and not (
            isinstance(req.get("group"), str) and isinstance(req.get("n"), int) and not isinstance(req["n"], bool)
            and 0 <= req["n"] <= 64
        ):
            return {"ok": False, "error": "scale needs group:str and n:int 0..64"}
        if cmd == "set-profile" and not (isinstance(req.get("profile"), str) and PROFILE_RE.fullmatch(req["profile"])):
            return {"ok": False, "error": "set-profile needs profile: a profile name such as std (lowercase letters, digits, -)"}
        if cmd == "apply-plan" and (err := plan_error(req)):
            return {"ok": False, "error": err}
        if cmd == "node-state" and not (isinstance(req.get("node"), str) and NODE_RE.fullmatch(req["node"])
                                        and req.get("state") in ("off", "on", "down", "resume", "drain")
                                        and REASON_RE.fullmatch(req.get("reason") or "")):
            return {"ok": False, "error": "node-state needs node:NAME (cn001), state: off|on|down|resume|drain and an optional short reason"}
        if cmd == "submit-job" and not (isinstance(req.get("node"), str) and NODE_RE.fullmatch(req["node"])
                                        and all(isinstance(req.get(k), int) and not isinstance(req.get(k), bool) and lo <= req[k] <= hi
                                                for k, lo, hi in (("gpus", 0, 8), ("duration", 10, 3600)))
                                        and re.fullmatch(r"[a-z][a-z0-9_-]{0,15}", req.get("user") or "x")):
            return {"ok": False, "error": "submit-job needs node:NAME, gpus:int 0..8, duration:int 10..3600 and an optional user"}
        if cmd == "ticket-bot-set":  # the ticket bot's settings (issue 728): numbers only, ranges as `benchctl set ticket-bot`
            given = {k: req[k] for k in TICKET_RANGES if req.get(k) is not None}
            if not given or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and TICKET_RANGES[k][0] <= v <= TICKET_RANGES[k][1]
                                    for k, v in given.items()):
                return {"ok": False, "error": "ticket-bot-set needs at least one of every 10..3600, resolve 10..86400, ignore 0..1, reopen 0..1 (numbers)"}
        if cmd == "slurm-step" and not (req.get("name") in SLURM_STEPS
                                        and (req["name"] not in NODE_STEPS or (isinstance(req.get("node"), str) and NODE_RE.fullmatch(req["node"])))):
            return {"ok": False, "error": f"slurm-step needs name: {'|'.join(SLURM_STEPS)} (and node:NAME for slurmd-*)"}
        if cmd == "k8s-step" and (err := k8s_step_error(req)):
            return {"ok": False, "error": err}
        if cmd == "slurm-trace" and not (isinstance(req.get("nodes"), list) and len(req["nodes"]) <= 8
                                         and all(isinstance(n, str) and NODE_RE.fullmatch(n) for n in req["nodes"])
                                         and isinstance(req.get("since"), (int, float)) and not isinstance(req.get("since"), bool)):
            return {"ok": False, "error": "slurm-trace needs nodes:[NAME, ...] (at most 8) and since:number (unix seconds)"}
        if cmd == "capture-start" and not (isinstance(req.get("node"), str) and NODE_RE.fullmatch(req["node"])):
            return {"ok": False, "error": "capture-start needs node:NAME (cn001)"}
        if cmd == "netem-set":
            if not (isinstance(req.get("node"), str) and NODE_RE.fullmatch(req["node"])):
                return {"ok": False, "error": "netem-set needs node:NAME (cn001)"}
            if bad := netem.problems(req.get("delay_ms"), req.get("loss_pct"), req.get("ttl_s")):
                return {"ok": False, "error": "; ".join(bad)}
        if cmd == "netem-clear" and req.get("node") is not None and not (isinstance(req["node"], str) and NODE_RE.fullmatch(req["node"])):
            return {"ok": False, "error": "netem-clear takes an optional node:NAME (cn001)"}
        if cmd == "kube-audit" and not (isinstance(req.get("since", 0), (int, float)) and not isinstance(req.get("since", 0), bool) and req.get("since", 0) >= 0):
            return {"ok": False, "error": "kube-audit takes an optional since:number>=0 (unix seconds)"}
        if cmd == "capture-read" and not (isinstance(req.get("since", 0), int) and not isinstance(req.get("since", 0), bool) and req.get("since", 0) >= 0):
            return {"ok": False, "error": "capture-read takes an optional since:int>=0"}
        if cmd in LAB_VERBS | LAB_APPLY_VERBS and (err := lab_args_error(cmd, req)):
            return {"ok": False, "error": err}
        if cmd in LAB_RUN_VERBS and (err := lab_run_error(req)):
            return {"ok": False, "error": err}
        if cmd == "admit" and not (isinstance(req.get("budget_mb"), int) and not isinstance(req["budget_mb"], bool)
                                   and req["budget_mb"] >= 0):
            return {"ok": False, "error": "admit needs budget_mb:int>=0"}
        return handle(cmd, req)
    except (ValueError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"bad json: {e}"}
    except Exception as e:  # keep the governor alive whatever a client sends
        log.exception("handler failed")
        return {"ok": False, "error": f"internal: {e}"}


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(path: str, handle: Handler, groups: Callable[[], set[str]],
          mode: int = 0o660, gid: int | None = None) -> socketserver.UnixStreamServer:
    """Start the control server in a daemon thread and return it (call .shutdown() to stop)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.unlink()

    class H(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.request.settimeout(5)
            try:
                line = self.rfile.readline(MAX_REQUEST + 2)
            except OSError:
                return
            if not line.strip():
                return
            reply = dispatch(line, handle_cmd, groups)
            self.wfile.write(json.dumps(reply).encode() + b"\n")

    handle_cmd = handle
    srv = _Server(path, H)
    os.chmod(path, mode)
    if gid is not None:
        os.chown(path, -1, gid)
        # the group must also traverse the directory holding the socket
        os.chown(p.parent, -1, gid)
        os.chmod(p.parent, 0o750)
    threading.Thread(target=srv.serve_forever, daemon=True, name="control").start()
    return srv
