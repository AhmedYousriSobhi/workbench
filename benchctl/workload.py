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


def command(sim: dict, node: str, gpus: int = 0, duration: int = 300, user: str | None = None, cpus: int = 1) -> list[str]:
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
    per_node = sim.get("gpu", {}).get("gpus_per_node", 0) if kinds[node] == "gpu" else 0
    if not 0 <= gpus <= per_node:
        raise WorkloadError(f"{node} has {per_node} GPU(s); asked for {gpus}")
    if not 1 <= cpus <= 64:
        raise WorkloadError("cpus must be 1 to 64")
    part = "gpu" if kinds[node] == "gpu" else "cpu"
    args = ["sbatch", "--parsable", f"--nodelist={node}", f"--partition={part}", f"--cpus-per-task={cpus}",
            f"--job-name=wb-{node}", f"--time={duration // 60 + 1}", *([f"--gres=gpu:{gpus}"] if gpus else []),
            f"--wrap=sleep {duration}"]
    return ["docker", "exec", "-u", user, LOGIN, "bash", "-lc", shlex.join(args)]
