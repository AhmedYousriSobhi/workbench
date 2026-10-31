"""Cluster Canvas backend (SPEC W-4): live node state from the mocks and the fault actions applied to them.

Reads the BMC farm, BCM mock and GPU exporter over localhost HTTP; actions reuse the scenario engine's steps, so a
click does exactly what the same step in a scenario does. No Docker access: Slurm drain is not offered here.
"""
import json
import re
import urllib.error
import urllib.request

from benchctl import scenario
from benchctl.nodes import node_names

BMC, BCM, JIRA, EXPORTER = ("http://127.0.0.1:8000", "http://127.0.0.1:8081", "http://127.0.0.1:8080",
                            "http://127.0.0.1:9400")
SERVICES = {"bmc-farm": f"{BMC}/healthz", "bcm-mock": f"{BCM}/healthz", "jira-mock": f"{JIRA}/healthz",
            "gpu-exporter": f"{EXPORTER}/metrics"}
HOT_C = 90.0
# action -> (label, only for gpu nodes, steps as (target, do-template))
ACTIONS = {
    "power-off": ("Power off", False, [("bmc-farm", {"power": "Off"}), ("bcm-mock", {"state": "DOWN"}),
                                       ("gpu-exporter", {"down": True})]),
    "power-on": ("Power on", False, [("bmc-farm", {"power": "On"}), ("bcm-mock", {"state": "UP"}),
                                     ("gpu-exporter", {"down": False})]),
    "overheat": ("Overheat (92 °C)", False, [("bmc-farm", {"temp": 92})]),
    "fan-fail": ("Fan failure", False, [("bmc-farm", {"fan_fail": 3})]),
    "gpu-xid": ("GPU error (XID 79)", True, [("gpu-exporter", {"xid": 79})]),
    "clear": ("Clear faults", False, [("bmc-farm", {"clear": True}), ("gpu-exporter", {"clear": True}),
                                      ("bcm-mock", {"state": "UP"})]),
}


class ActionError(ValueError):
    pass


def http_json(url: str, body: dict | None = None, timeout: float = 2.0):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST",
                                 headers={"X-Workbench-Probe": "1"})  # the mocks do not count this as client traffic
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode()


def xid_by_node(metrics: str) -> dict[str, int]:
    """Highest DCGM_FI_DEV_XID_ERRORS per node from a Prometheus text scrape."""
    out: dict[str, int] = {}
    for line in metrics.splitlines():
        if line.startswith("DCGM_FI_DEV_XID_ERRORS"):
            m = re.search(r'(?:hostname|node|Hostname)="([^"]+)"', line)
            if m:
                out[m.group(1)] = max(out.get(m.group(1), 0), int(float(line.rsplit(" ", 1)[1])))
    return out


def gpu_util_by_node(metrics: str) -> dict[str, list[float]]:
    """DCGM_FI_DEV_GPU_UTIL per GPU, grouped by node, from a Prometheus text scrape."""
    out: dict[str, list[float]] = {}
    for line in metrics.splitlines():
        if line.startswith("DCGM_FI_DEV_GPU_UTIL"):
            m = re.search(r'(?:hostname|node|Hostname)="([^"]+)"', line)
            if m:
                out.setdefault(m.group(1), []).append(float(line.rsplit(" ", 1)[1]))
    return out


def classify(n: dict) -> tuple[str, str]:
    """(state key, plain label) for a node card; the most serious condition wins."""
    if n["power"] is None:
        return "unknown", "No data"
    if n["power"] != "On":
        return "down", "Powered off"
    if n["xid"]:
        return "fault", f"GPU error XID {n['xid']}"
    if n["temp"] is not None and n["temp"] >= HOT_C:
        return "fault", f"Overheating ({n['temp']:.0f} °C)"
    if n["fans_failed"]:
        return "warn", f"{n['fans_failed']} fan(s) failed"
    if n["bcm"] not in (None, "UP"):
        return "warn", f"BCM says {n['bcm']}"
    return "ok", "Healthy"


class Cluster:
    def __init__(self, sim: dict, fetch=http_json, poster=scenario.post):
        self.sim = sim
        self.nodes = node_names(sim)
        self.gpus = sim.get("gpu", {}).get("gpus_per_node", 0)
        self.fetch, self.poster = fetch, poster

    def _try(self, *args):
        try:
            return self.fetch(*args)
        except (OSError, ValueError):
            return None

    def _node(self, name: str, kind: str) -> dict:
        system = self._try(f"{BMC}/{name}/redfish/v1/Systems/1")
        thermal = self._try(f"{BMC}/{name}/redfish/v1/Chassis/1/Thermal") or {}
        temps = [t.get("ReadingCelsius") for t in thermal.get("Temperatures", []) if t.get("Name") == "CPU"]
        fans = [f for f in thermal.get("Fans", []) if f.get("Status", {}).get("Health") == "Critical"]
        return {"name": name, "kind": kind, "gpus": self.gpus if kind == "gpu" else 0,
                "power": system.get("PowerState") if isinstance(system, dict) else None,
                "temp": temps[0] if temps else None, "fans_failed": len(fans)}

    def snapshot(self) -> dict:
        """{"services": {name: bool}, "nodes": [...]}: one pass over the mocks.

        Sequential on purpose: localhost calls take a few ms, and a thread pool pushed the UI over its 50 MB budget.
        """
        names = list(SERVICES)
        up = [self._try(SERVICES[n]) is not None for n in names]
        rows = [self._node(*nk) for nk in self.nodes]
        devices = self._try(f"{BCM}/json", {"service": "cmdevice", "call": "getDevices"})
        bcm = {d["hostname"]: d["status"] for d in (devices or {}).get("result", [])} if isinstance(devices, dict) else {}
        scrape = self._try(SERVICES["gpu-exporter"]) or ""
        scrape = scrape if isinstance(scrape, str) else ""
        xids, utils = xid_by_node(scrape), gpu_util_by_node(scrape)
        for row in rows:
            if row["name"] in utils:
                u = utils[row["name"]]
                row["gpu_util"], row["gpus_busy"] = round(sum(u) / len(u)), sum(1 for x in u if x >= 50)
            row["bcm"] = bcm.get(row["name"])
            row["xid"] = xids.get(row["name"], 0)
            row["state"], row["label"] = classify(row)
        return {"services": dict(zip(names, up, strict=True)), "nodes": rows,
                "actions": [{"id": a, "label": v[0], "gpu_only": v[1]} for a, v in ACTIONS.items()]}

    def act(self, node: str, action: str) -> list[dict]:
        """Apply a fault action to one node; returns each step's outcome. Raises ActionError for bad input."""
        kinds = dict(self.nodes)
        if node not in kinds:
            raise ActionError(f"unknown node {node!r}")
        if action not in ACTIONS:
            raise ActionError(f"unknown action {action!r}")
        _, gpu_only, steps = ACTIONS[action]
        if gpu_only and kinds[node] != "gpu":
            raise ActionError(f"{ACTIONS[action][0]} only applies to GPU nodes")
        out = []
        for target, do in steps:
            if action == "clear" and target == "gpu-exporter" and kinds[node] != "gpu":
                continue
            o = scenario.execute(scenario.Step(0, target, {"nodes": [node], **do}), poster=self.poster,
                                 host=False)
            out.append({"target": target, "status": o.status, "detail": o.detail})
        return out
