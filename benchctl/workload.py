"""Submit a job pinned to one node (`benchctl submit`, the node panel's workload buttons; issue 156).

The job is a plain `sleep`, so it holds its CPUs and GPUs for the duration: `squeue` lists it, the node shows allocated and
the GPU exporter shows those GPUs busy (it follows Slurm's allocations). It runs as a simulated user on the login node."""
import re
import shlex

from . import nodes

LOGIN = "workbench-login-1"
USER_RE = re.compile(r"[a-z][a-z0-9_-]{0,15}")
DURATION = (10, 3600)


class WorkloadError(ValueError):
    pass


def command(sim: dict, node: str, gpus: int = 0, duration: int = 300, user: str | None = None, cpus: int = 1,
            qos: str | None = None) -> list[str]:
    """The `docker exec ... sbatch` for one job on `node`; raises WorkloadError for anything the cluster cannot honour."""
    kinds = dict(nodes.node_names(sim))
    if node not in kinds:
        raise WorkloadError(f"unknown node {node!r}: {', '.join(kinds)}")
    users = [u["name"] for u in sim.get("users", [])]
    user = user or (users[0] if users else "alice")
    if not USER_RE.fullmatch(user) or (users and user not in users):
        raise WorkloadError(f"unknown user {user!r}: sim.yaml has {', '.join(users)}")
    if not DURATION[0] <= duration <= DURATION[1]:
        raise WorkloadError(f"duration must be {DURATION[0]} to {DURATION[1]} seconds")
    per_node = sim.get("gpu", {}).get("gpus_per_node", 0) if kinds[node] == "gpu" else 1 if kinds[node] == "real-gpu" else 0
    if not 0 <= gpus <= per_node:
        raise WorkloadError(f"{node} has {per_node} GPU(s); asked for {gpus}")
    if not 1 <= cpus <= 64:
        raise WorkloadError("cpus must be 1 to 64")
    declared = {q["name"] for q in sim.get("qos") or []} | {"normal"}
    if qos is not None and qos not in declared:  # issue 544: a QOS the cluster has (sim.yaml `qos:`; normal is built in)
        raise WorkloadError(f"unknown qos {qos!r}: {', '.join(sorted(declared))}")
    # --output/--error: the working directory here is /, which a user cannot write, so a default slurm-N.out fails the job at launch
    part = "real-gpu" if kinds[node] == "real-gpu" else "gpu" if kinds[node] == "gpu" else "cpu"
    args = ["sbatch", "--parsable", f"--nodelist={node}", f"--partition={part}", f"--cpus-per-task={cpus}",
            f"--job-name=wb-{node}", f"--time={duration // 60 + 1}", "--output=/dev/null", "--error=/dev/null", *([f"--gres=gpu:{gpus}"] if gpus else []),
            *([f"--qos={qos}"] if qos else []),
            f"--wrap=sleep {duration}"]
    return ["docker", "exec", "-u", user, LOGIN, "bash", "-lc", shlex.join(args)]


SIM_SCRIPT = "images/login/user_sim.py"
SIM_PATH = "/usr/local/bin/user-sim"
MOCK_SCRIPT = "images/login/mock_sim.py"
MOCK_PATH = "/usr/local/bin/mock-sim"


def load_on(sim: dict, rate: float = 6, duration: float = 120, seed: int | None = None, personas: bool = False,
            day: float = 0) -> list[list[str]]:
    """Commands that (re)start the user simulator on the login node. The script is copied in first, so a cluster built before
    it existed needs no rebuild."""
    if not 0.1 <= rate <= 120:
        raise WorkloadError("rate must be 0.1 to 120 jobs per minute")
    if not 5 <= duration <= 3600:
        raise WorkloadError("duration must be 5 to 3600 seconds")
    if day and not 60 <= day <= 86400:
        raise WorkloadError("day must be 60 to 86400 seconds (0: no daily rhythm)")
    users = ",".join(u["name"] + (f":{u['persona']}" if personas and u.get("persona") else "") for u in sim.get("users", [])) or "alice,bob"
    gpus = sim.get("gpu", {}).get("gpus_per_node", 0) if sim.get("gpu", {}).get("nodes") else 0
    return [["docker", "cp", SIM_SCRIPT, f"{LOGIN}:{SIM_PATH}"], load_off(),
            ["docker", "exec", "-d", LOGIN, "python3", SIM_PATH, "--rate", f"{rate:g}", "--duration", f"{duration:g}", "--users", users,
             "--gpus", str(gpus), *(["--personas"] if personas else []), *(["--day", f"{day:g}"] if day else []), *(["--seed", str(seed)] if seed is not None else [])]]


def load_off() -> list[str]:
    return ["docker", "exec", LOGIN, "pkill", "-f", SIM_PATH]


def mocks_on(sim: dict, rate: float = 30) -> list[list[str]]:
    """Commands that (re)start the demo client for the mocks on the login node (issue 650): the calls a monitoring client makes to
    jira-mock, bcm-mock and bmc-farm, so the Traffic tab has counted requests. Copied in first, so no rebuild is needed."""
    if not 1 <= rate <= 600:
        raise WorkloadError("mock rate must be 1 to 600 requests per minute")
    names = [n for n, kind in nodes.node_names(sim) if kind == "compute"][:3] or [n for n, _ in nodes.node_names(sim)][:1] or ["cn001"]
    return [["docker", "cp", MOCK_SCRIPT, f"{LOGIN}:{MOCK_PATH}"], mocks_off(),
            ["docker", "exec", "-d", LOGIN, "python3", MOCK_PATH, "--rate", f"{rate:g}", "--nodes", ",".join(names)]]


def mocks_off() -> list[str]:
    return ["docker", "exec", LOGIN, "pkill", "-f", MOCK_PATH]
