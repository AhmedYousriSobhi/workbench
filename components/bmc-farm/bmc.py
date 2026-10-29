"""Fake Redfish BMCs: one process serving an endpoint per node under /<node>/redfish/v1/...

GET  /<node>/redfish/v1/{,Systems/1,Chassis/1,Chassis/1/Thermal,Chassis/1/Power,Managers/1,
                          Systems/1/LogServices/Log/Entries}
POST /<node>/redfish/v1/Systems/1/Actions/ComputerSystem.Reset   {"ResetType": "ForceOff|On|..."}
POST /sim/inject  {"node": n, "temp": 95} | {"node": n, "fan_fail": 2} | {"node": n, "power": "Off"}
                  | {"node": n, "event": "text"} | {"node": n, "clear": true}
"""
import json
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

FANS = 6
RESET_TO = {"On": "On", "ForceOn": "On", "ForceOff": "Off", "GracefulShutdown": "Off",
            "ForceRestart": "On", "GracefulRestart": "On", "PowerCycle": "On"}


def load_nodes(sim_yaml: Path) -> dict[str, dict]:
    """{node: {"gpus": n, "memory_mb": m, "cpus": c}} for compute then GPU nodes."""
    cfg = yaml.safe_load(sim_yaml.read_text())
    out: dict[str, dict] = {}
    c, g = cfg.get("compute", {}), cfg.get("gpu", {})
    for i in range(1, c.get("count", 0) + 1):
        out[f"{c['prefix']}{i:03d}"] = {"gpus": 0, "memory_mb": c.get("memory_mb", 4096), "cpus": c.get("cpus", 8)}
    for i in range(1, g.get("nodes", 0) + 1):
        out[f"{g['prefix']}{i:03d}"] = {"gpus": g["gpus_per_node"], "memory_mb": 2048 * g["gpus_per_node"],
                                        "cpus": 8 * g["gpus_per_node"]}
    return out


class Farm:
    def __init__(self, nodes: dict[str, dict], rng: random.Random | None = None):
        self.nodes, self.rng = nodes, rng or random.Random()
        self.power = {n: "On" for n in nodes}
        self.temp: dict[str, float] = {}
        self.fans_failed: dict[str, int] = {}
        self.log: dict[str, list[dict]] = {n: [] for n in nodes}

    def event(self, node: str, message: str, severity: str = "OK") -> None:
        entries = self.log[node]
        entries.append({"@odata.id": f"/{node}/redfish/v1/Systems/1/LogServices/Log/Entries/{len(entries) + 1}",
                        "Id": str(len(entries) + 1), "Created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "Severity": severity, "Message": message})

    def inject(self, req: dict) -> None:
        n = req["node"]
        if n not in self.nodes:
            raise KeyError(n)
        if req.get("clear"):
            self.temp.pop(n, None)
            self.fans_failed.pop(n, None)
            self.power[n] = "On"
            return
        if "temp" in req:
            self.temp[n] = float(req["temp"])
            self.event(n, f"CPU temperature {self.temp[n]:.0f}C", "Critical" if self.temp[n] >= 90 else "Warning")
        if "fan_fail" in req:
            self.fans_failed[n] = min(FANS, int(req["fan_fail"]))
            self.event(n, f"{self.fans_failed[n]} fan(s) failed", "Critical")
        if "power" in req:
            self.power[n] = "On" if req["power"] == "On" else "Off"
            self.event(n, f"Power {self.power[n]}")
        if "event" in req:
            self.event(n, str(req["event"]), req.get("severity", "Warning"))

    def reset(self, node: str, kind: str) -> None:
        if kind not in RESET_TO:
            raise ValueError(f"bad ResetType {kind!r}")
        self.power[node] = RESET_TO[kind]
        self.event(node, f"Reset {kind}")

    # --- Redfish resources -------------------------------------------------
    def resource(self, node: str, path: str) -> dict | None:
        base, info, r = f"/{node}/redfish/v1", self.nodes[node], self.rng
        on = self.power[node] == "On"
        cpu_t = self.temp.get(node, round(r.uniform(40, 65), 1)) if on else 18.0
        healthy = {"State": "Enabled" if on else "Disabled",
                   "Health": "Critical" if cpu_t >= 90 or self.fans_failed.get(node) else "OK"}
        table = {
            "": {"@odata.id": base + "/", "RedfishVersion": "1.15.0", "Name": f"{node} service root",
                 "Systems": {"@odata.id": base + "/Systems"}, "Chassis": {"@odata.id": base + "/Chassis"},
                 "Managers": {"@odata.id": base + "/Managers"}},
            "Systems/1": {"@odata.id": f"{base}/Systems/1", "Id": "1", "Name": node, "HostName": node,
                          "PowerState": self.power[node], "Status": healthy,
                          "ProcessorSummary": {"Count": info["cpus"], "Status": healthy},
                          "MemorySummary": {"TotalSystemMemoryGiB": info["memory_mb"] // 1024, "Status": healthy},
                          "Oem": {"GpuCount": info["gpus"]},
                          "Actions": {"#ComputerSystem.Reset": {
                              "target": f"{base}/Systems/1/Actions/ComputerSystem.Reset",
                              "ResetType@Redfish.AllowableValues": sorted(RESET_TO)}},
                          "LogServices": {"@odata.id": f"{base}/Systems/1/LogServices"}},
            "Chassis/1": {"@odata.id": f"{base}/Chassis/1", "Id": "1", "Name": f"{node} chassis",
                          "ChassisType": "RackMount", "PowerState": self.power[node], "Status": healthy,
                          "Thermal": {"@odata.id": f"{base}/Chassis/1/Thermal"},
                          "Power": {"@odata.id": f"{base}/Chassis/1/Power"}},
            "Chassis/1/Thermal": {
                "@odata.id": f"{base}/Chassis/1/Thermal", "Name": "Thermal",
                "Temperatures": [{"Name": "CPU", "ReadingCelsius": cpu_t, "UpperThresholdCritical": 90,
                                  "Status": {"Health": "Critical" if cpu_t >= 90 else "OK"}},
                                 {"Name": "Inlet", "ReadingCelsius": 24.0 if on else 22.0}],
                "Fans": [{"Name": f"Fan{i + 1}", "Reading": 0 if not on or i < self.fans_failed.get(node, 0)
                          else r.randint(5000, 9000), "ReadingUnits": "RPM",
                          "Status": {"Health": "Critical" if i < self.fans_failed.get(node, 0) else "OK"}}
                         for i in range(FANS)]},
            "Chassis/1/Power": {"@odata.id": f"{base}/Chassis/1/Power", "Name": "Power",
                                "PowerControl": [{"PowerConsumedWatts": r.randint(300, 900) if on else 0}]},
            "Managers/1": {"@odata.id": f"{base}/Managers/1", "Id": "1", "ManagerType": "BMC",
                           "Name": f"{node} BMC", "Status": {"State": "Enabled", "Health": "OK"}},
            "Systems/1/LogServices/Log/Entries": {
                "@odata.id": f"{base}/Systems/1/LogServices/Log/Entries", "Name": "Log Entries",
                "Members@odata.count": len(self.log[node]), "Members": self.log[node]},
        }
        return table.get(path.strip("/"))


def make_handler(farm: Farm):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj) -> None:
            data = (json.dumps(obj) if not isinstance(obj, str) else obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _split(self) -> tuple[str, str] | None:
            parts = self.path.split("?")[0].strip("/").split("/", 3)  # node, redfish, v1, rest
            if len(parts) >= 3 and parts[1:3] == ["redfish", "v1"] and parts[0] in farm.nodes:
                return parts[0], parts[3] if len(parts) > 3 else ""
            return None

        def do_GET(self):
            if self.path == "/healthz":
                return self._send(200, {"nodes": len(farm.nodes)})
            hit = self._split()
            body = farm.resource(*hit) if hit else None
            self._send(200, body) if body is not None else self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path == "/sim/inject":
                    farm.inject(req)
                    return self._send(200, {"ok": True})
                hit = self._split()
                if hit and hit[1] == "Systems/1/Actions/ComputerSystem.Reset":
                    farm.reset(hit[0], req.get("ResetType", ""))
                    return self._send(200, {"ok": True})
                self._send(404, {"error": "not found"})
            except (KeyError, ValueError, TypeError) as e:
                self._send(400, {"error": f"bad request: {e}"})

        def log_message(self, *args):
            pass

    return Handler


def main() -> None:
    sim = Path(sys.argv[1] if len(sys.argv) > 1 else "sim.yaml")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(Farm(load_nodes(sim)))).serve_forever()


if __name__ == "__main__":
    main()
