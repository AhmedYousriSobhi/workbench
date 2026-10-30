"""`benchctl describe node NAME` and the web page's node detail: one gatherer, two renderers (issue 93).

Sources are the same ones the rest of the tool uses: the BMC farm and the GPU exporter over localhost HTTP,
and, when a `runner` is given (the CLI has Docker, the web page by design does not), Docker and Slurm. A source that
does not answer leaves its fields out and adds one line to `notes`; nothing here changes the cluster."""
import json
import re
import urllib.request
from collections.abc import Callable

from . import nodes
from .stack import PROJECT

BMC, EXPORTER = "http://127.0.0.1:8000", "http://127.0.0.1:9400"
BMC_TLS_PORT = 8443
BUSY_UTIL = 50.0  # a GPU at or above this utilisation counts as busy (idle ~0-3, busy 70-100)


class UnknownNode(KeyError):
    pass


def http_get(url: str, timeout: float = 2.0):
    """GET a localhost URL; JSON when it parses, else text. The probe header keeps the mocks from counting it as client traffic."""
    req = urllib.request.Request(url, headers={"X-Opti-Probe": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _util(metrics: str, node: str) -> dict[int, float]:
    pat = re.compile(r'DCGM_FI_DEV_GPU_UTIL\{[^}]*(?:node|Hostname|hostname)="' + re.escape(node) + r'"[^}]*\} (\S+)')
    out: dict[int, float] = {}
    for line in metrics.splitlines():
        m = pat.match(line)
        g = re.search(r'gpu="(\d+)"', line)
        if m and g:
            out[int(g[1])] = float(m[1])
    return out


def _scontrol(text: str) -> dict[str, str]:
    """`scontrol show node -o` is one line of Key=Value pairs; keep the ones a person looks for."""
    wanted = ("State", "Partitions", "Reason", "CPUTot", "CPUAlloc", "RealMemory", "Gres", "GresUsed", "Features")
    found = dict(re.findall(r"(\w+)=(.*?)(?= \w+=|$)", text.strip()))
    return {k: found[k] for k in wanted if found.get(k) not in (None, "(null)", "")}


def gather(sim: dict, name: str, fetch: Callable, runner: Callable | None = None) -> dict:
    """Everything known about one node. `fetch(url)` returns parsed JSON or text and may raise OSError/ValueError."""
    kinds = dict(nodes.node_names(sim))
    if name not in kinds:
        raise UnknownNode(f"unknown node {name!r}; nodes: {', '.join(kinds)}")
    info: dict = {"name": name, "kind": kinds[name], "notes": []}

    def get(url: str):
        try:
            return fetch(url)
        except (OSError, ValueError):
            return None

    if runner:
        r = runner(["docker", "inspect", f"{PROJECT}-{name}-1"])
        if r.returncode == 0 and r.stdout.strip():
            d = json.loads(r.stdout)[0]
            mem = d["HostConfig"].get("Memory") or 0
            info["container"] = {"state": d["State"]["Status"], "image": d["Config"]["Image"],
                                 "memory_limit_mb": mem // (1024 * 1024) or None,
                                 "class": d["Config"]["Labels"].get("hpcsim.class")}
        else:
            info["container"] = {"state": "absent"}
            info["notes"].append("container not found: is the stack up? (benchctl up)")
        sc = runner(["docker", "exec", f"{PROJECT}-slurmctld-1", "scontrol", "show", "node", name, "-o"])
        if sc.returncode == 0 and sc.stdout.strip():
            info["slurm"] = _scontrol(sc.stdout)
            jobs = runner(["docker", "exec", f"{PROJECT}-slurmctld-1", "squeue", "-h", "-w", name, "-o", "%i|%u|%P|%T"])
            info["jobs"] = [dict(zip(("id", "user", "partition", "state"), ln.split("|"), strict=False))
                            for ln in jobs.stdout.splitlines() if ln.strip()]
        else:
            info["notes"].append("Slurm did not answer for this node")
    system = get(f"{BMC}/{name}/redfish/v1/Systems/1")
    thermal = get(f"{BMC}/{name}/redfish/v1/Chassis/1/Thermal")
    if isinstance(system, dict):
        thermal = thermal if isinstance(thermal, dict) else {}
        temps = [t.get("ReadingCelsius") for t in thermal.get("Temperatures", []) if t.get("Name") == "CPU"]
        fans = thermal.get("Fans", [])
        info["bmc"] = {"address": f"bmc-{name}:{BMC_TLS_PORT} (Host header bmc-{name} on 127.0.0.1:{BMC_TLS_PORT})",
                       "power": system.get("PowerState"), "cpu_temp_c": temps[0] if temps else None,
                       "fans": len(fans), "fans_failed": sum(1 for f in fans if f.get("Status", {}).get("Health") == "Critical")}
    else:
        info["notes"].append("BMC farm did not answer")
    if info["kind"] == "gpu":
        gpu = sim.get("gpu", {})
        metrics = get(f"{EXPORTER}/metrics")
        if isinstance(metrics, str):
            util = _util(metrics, name)
            xid = [int(float(ln.rsplit(" ", 1)[1])) for ln in metrics.splitlines()
                   if ln.startswith("DCGM_FI_DEV_XID_ERRORS") and re.search(rf'="{re.escape(name)}"', ln)]
            info["gpus"] = {"type": gpu.get("type"), "count": gpu.get("gpus_per_node"),
                            "busy": sorted(g for g, u in util.items() if u >= BUSY_UTIL),
                            "idle": sorted(g for g, u in util.items() if u < BUSY_UTIL),
                            "xid": max(xid, default=0)}
        else:
            info["notes"].append("GPU exporter did not answer")
    info["ssh"] = f"ssh {name} (from the login node: benchctl exec login -u alice -- ssh {name} hostname)"
    return info


def render(info: dict) -> str:
    """Plain text, like `kubectl describe`: a header, then one block per source."""
    lines = [f"Name:        {info['name']}", f"Kind:        {info['kind']}"]
    if c := info.get("container"):
        lines.append(f"Container:   {c['state']}" + (f"  image {c['image']}, memory limit {c['memory_limit_mb']} MB, "
                                                    f"class {c['class']}" if c["state"] != "absent" else ""))
    if s := info.get("slurm"):
        lines.append("Slurm:")
        lines += [f"  {k + ':':12} {v}" for k, v in s.items()]
    if "jobs" in info:
        lines.append("Jobs:        " + (", ".join(f"{j['id']} ({j['user']}, {j['partition']}, {j['state']})"
                                                  for j in info["jobs"]) or "none"))
    if g := info.get("gpus"):
        lines.append(f"GPUs:        {g['count']} x {g['type']}; busy {g['busy'] or 'none'}; idle {len(g['idle'])}; "
                     f"XID {g['xid'] or 'none'}")
    if b := info.get("bmc"):
        lines.append(f"BMC:         {b['address']}")
        lines.append(f"             power {b['power']}, CPU {b['cpu_temp_c']} C, fans {b['fans']} ({b['fans_failed']} failed)")
    lines.append(f"SSH:         {info['ssh']}")
    lines += [f"Note:        {n}" for n in info["notes"]]
    return "\n".join(lines)
