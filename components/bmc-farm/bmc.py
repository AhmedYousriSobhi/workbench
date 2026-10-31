"""Fake Redfish BMCs: one process serving an endpoint per node under /<node>/redfish/v1/...

GET  /<node>/redfish/v1/{,Systems/1,Chassis/1,Chassis/1/Thermal,Chassis/1/Power,Managers/1,
                          Systems/1/LogServices/Log/Entries}
POST /<node>/redfish/v1/Systems/1/Actions/ComputerSystem.Reset   {"ResetType": "ForceOff|On|..."}
Over TLS (:8443, how a real client sees a BMC) each node answers at the standard /redfish/v1 and is chosen by the
Host header: bmc-<node> or <node>, e.g. the compose-network alias bmc-cn001; any other Host gets the first node.
Everything but the service root needs a session (POST /redfish/v1/SessionService/Sessions, X-Auth-Token) or Basic
auth with the account in the credentials file; with no file, auth is off.

POST /sim/inject  {"node": n, "temp": 95} | {"node": n, "fan_fail": 2} | {"node": n, "power": "Off"}
                  | {"node": n, "event": "text"} | {"node": n, "clear": true}
"""
import base64
import json
import random
import secrets
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

FANS = 6
FIRMWARE = {"BMC": ("BMC firmware", "2.4.0"), "BIOS": ("BIOS", "1.0.0")}
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
        self.account = "admin"  # the read-only account the AccountService lists; main() sets it from the credentials

    def event(self, node: str, message: str, severity: str = "OK") -> None:
        entries = self.log[node]
        entries.append({"Id": str(len(entries) + 1), "Created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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
    def resource(self, node: str, path: str, root: str | None = None) -> dict | None:
        """One Redfish resource; `root` is the service root in links (legacy /<node>/redfish/v1 when None)."""
        base, info, r = root or f"/{node}/redfish/v1", self.nodes[node], self.rng
        on = self.power[node] == "On"
        cpu_t = self.temp.get(node, round(r.uniform(40, 65), 1)) if on else 18.0
        healthy = {"State": "Enabled" if on else "Disabled",
                   "Health": "Critical" if cpu_t >= 90 or self.fans_failed.get(node) else "OK"}
        table = {
            "": {"@odata.id": base + "/", "RedfishVersion": "1.15.0", "Name": f"{node} service root",
                 "Systems": {"@odata.id": base + "/Systems"}, "Chassis": {"@odata.id": base + "/Chassis"},
                 "Managers": {"@odata.id": base + "/Managers"},
                 "UpdateService": {"@odata.id": base + "/UpdateService"},
                 "AccountService": {"@odata.id": base + "/AccountService"},
                 "SessionService": {"@odata.id": base + "/SessionService"}},
            "Systems/1": {"@odata.id": f"{base}/Systems/1", "Id": "1", "Name": node, "HostName": node, "Manufacturer": "workbench", "Model": "SimServer",
                          "SerialNumber": f"SIM-{node.upper()}", "BiosVersion": "1.0.0",
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
                           "Name": f"{node} BMC", "Model": "SimBMC", "FirmwareVersion": "2.4.0",
                           "Status": {"State": "Enabled", "Health": "OK"}},
            "Systems/1/LogServices/Log": {"@odata.id": f"{base}/Systems/1/LogServices/Log", "Id": "Log",
                                          "Name": "Hardware event log",
                                          "Entries": {"@odata.id": f"{base}/Systems/1/LogServices/Log/Entries"}},
            "Systems/1/LogServices/Log/Entries": {
                "@odata.id": f"{base}/Systems/1/LogServices/Log/Entries", "Name": "Log Entries",
                "Members@odata.count": len(self.log[node]),
                "Members": [{**e, "@odata.id": f"{base}/Systems/1/LogServices/Log/Entries/{e['Id']}"}
                            for e in self.log[node]]},
        }
        for fw, (name, version) in FIRMWARE.items():
            table[f"UpdateService/FirmwareInventory/{fw}"] = {
                "@odata.id": f"{base}/UpdateService/FirmwareInventory/{fw}", "Id": fw, "Name": name, "Version": version}
        links = {"Systems": ["Systems/1"], "Chassis": ["Chassis/1"], "Managers": ["Managers/1"],
                 "Systems/1/LogServices": ["Systems/1/LogServices/Log"], "AccountService/Accounts": ["AccountService/Accounts/1"],
                 "UpdateService/FirmwareInventory": [f"UpdateService/FirmwareInventory/{fw}" for fw in FIRMWARE],
                 "SessionService/Sessions": []}
        for coll, members in links.items():
            table[coll] = {"@odata.id": f"{base}/{coll}", "Name": coll.rsplit("/", 1)[-1],
                           "Members@odata.count": len(members), "Members": [{"@odata.id": f"{base}/{m}"} for m in members]}
        table["UpdateService"] = {"@odata.id": f"{base}/UpdateService", "Name": "Update service",
                                  "FirmwareInventory": {"@odata.id": f"{base}/UpdateService/FirmwareInventory"}}
        table["AccountService"] = {"@odata.id": f"{base}/AccountService", "Name": "Account service",
                                   "Accounts": {"@odata.id": f"{base}/AccountService/Accounts"}}
        table["AccountService/Accounts/1"] = {"@odata.id": f"{base}/AccountService/Accounts/1", "Id": "1",
                                              "UserName": self.account, "RoleId": "ReadOnly", "Enabled": True}
        table["SessionService"] = {"@odata.id": f"{base}/SessionService", "Name": "Session service", "ServiceEnabled": True,
                                   "Sessions": {"@odata.id": f"{base}/SessionService/Sessions"}}
        return table.get(path.strip("/"))


PROBE = "x-workbench-probe"  # the web UI sets this on its own polls so they are not drawn as client traffic


def counted(path: str, headers) -> bool:
    """A real client request: not a health check, not /sim control, not a UI probe."""
    return not (path.startswith(("/healthz", "/sim/")) or headers.get(PROBE))


class Sessions:
    """Redfish sessions for the TLS surface: login gives a token, a token or Basic auth reads. Capped like a real BMC."""
    MAX = 32

    def __init__(self, creds: tuple[str, str] | None):
        self.creds, self.tokens, self.lock = creds, {}, threading.Lock()

    def login(self, user: str, password: str) -> tuple[str, str] | None:
        if self.creds and (user, password) != self.creds:
            return None
        token, sid = secrets.token_hex(16), secrets.token_hex(4)
        with self.lock:
            if len(self.tokens) >= self.MAX:
                self.tokens.pop(next(iter(self.tokens)))
            self.tokens[token] = sid
        return token, sid

    def logout(self, sid: str) -> bool:
        with self.lock:
            hit = next((t for t, i in self.tokens.items() if i == sid), None)
            return self.tokens.pop(hit, None) is not None

    def allows(self, headers) -> bool:
        if not self.creds:
            return True
        if headers.get("X-Auth-Token") in self.tokens:
            return True
        auth = headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
            except ValueError:
                return False
            return (user, pw) == self.creds
        return False


SESSIONS = "/redfish/v1/SessionService/Sessions"
ROOTS = ("/redfish/v1", "/redfish/v1/")


def node_for(farm: Farm, host_header: str) -> str:
    """The node a Host header names (bmc-<node> or <node>, port ignored); anything else gets the first node."""
    host = host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header
    name = host.removeprefix("bmc-").split(".")[0]
    return name if name in farm.nodes else next(iter(farm.nodes))


def make_handler(farm: Farm, sessions: Sessions | None = None, stats: dict | None = None,
                 lock=None):
    """Legacy HTTP surface (/<node>/redfish/v1, no auth) when `sessions` is None, else the TLS surface."""
    if stats is None:
        stats, lock = {"requests": 0}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj, headers: dict | None = None) -> None:
            if counted(self.path, self.headers):
                with lock:
                    stats["requests"] += 1
            data = b"" if obj is None else (json.dumps(obj) if not isinstance(obj, str) else obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _split(self) -> tuple[str, str, str | None] | None:
            """(node, resource path, link root) for this request, or None when it is not a Redfish path."""
            path = self.path.split("?")[0]
            if sessions is not None:
                if path.rstrip("/") == "/redfish/v1" or path.startswith("/redfish/v1/"):
                    return node_for(farm, self.headers.get("Host", "")), path[len("/redfish/v1"):].strip("/"), "/redfish/v1"
                return None
            parts = path.strip("/").split("/", 3)  # node, redfish, v1, rest
            if len(parts) >= 3 and parts[1:3] == ["redfish", "v1"] and parts[0] in farm.nodes:
                return parts[0], parts[3] if len(parts) > 3 else "", None
            return None

        def _unauthorized(self) -> bool:
            if sessions is None or sessions.allows(self.headers):
                return False
            self._send(401, {"error": "authentication required"}, {"WWW-Authenticate": 'Basic realm="workbench"'})
            return True

        def do_GET(self):
            if self.path == "/healthz":
                return self._send(200, {"nodes": len(farm.nodes)})
            if self.path == "/sim/stats":
                return self._send(200, dict(stats))
            hit = self._split()
            if hit and self.path.split("?")[0] not in ROOTS and self._unauthorized():
                return
            body = farm.resource(*hit) if hit else None
            self._send(200, body) if body is not None else self._send(404, {"error": "not found"})

        def do_DELETE(self):
            path = self.path.split("?")[0]
            if sessions is None or not path.startswith(SESSIONS + "/"):
                return self._send(404, {"error": "not found"})
            if self._unauthorized():
                return
            self._send(204, None) if sessions.logout(path.rsplit("/", 1)[1]) else self._send(404, {"error": "no such session"})

        def do_POST(self):
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                path = self.path.split("?")[0]
                if path == "/sim/inject":
                    farm.inject(req)
                    return self._send(200, {"ok": True})
                if sessions is not None and path.rstrip("/") == SESSIONS:
                    made = sessions.login(str(req.get("UserName", "")), str(req.get("Password", "")))
                    if made is None:
                        return self._send(401, {"error": "invalid credentials"})
                    token, sid = made
                    return self._send(201, {"@odata.id": f"{SESSIONS}/{sid}", "Id": sid, "UserName": req["UserName"]},
                                      {"X-Auth-Token": token, "Location": f"{SESSIONS}/{sid}"})
                hit = self._split()
                if hit and hit[1] == "Systems/1/Actions/ComputerSystem.Reset":
                    if self._unauthorized():
                        return
                    farm.reset(hit[0], req.get("ResetType", ""))
                    return self._send(200, {"ok": True})
                self._send(404, {"error": "not found"})
            except (KeyError, ValueError, TypeError) as e:
                self._send(400, {"error": f"bad request: {e}"})

        def log_message(self, *args):
            pass

    return Handler


def read_credentials(path: Path) -> tuple[str, str] | None:
    """'user:password' from a file the host generates; None (auth off) when there is none."""
    try:
        user, _, pw = path.read_text().strip().partition(":")
    except OSError:
        return None
    return (user, pw) if user and pw else None


STANDARD_TLS_PORT = 443  # not published on the host (binding it there needs root); reached as bmc-<node> inside the cluster


def main() -> None:
    sim = Path(sys.argv[1] if len(sys.argv) > 1 else "sim.yaml")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    tls_port = int(sys.argv[3]) if len(sys.argv) > 3 else 8443
    farm, stats, lock = Farm(load_nodes(sim)), {"requests": 0}, threading.Lock()
    creds = read_credentials(Path("/run/workbench/bmc_credentials"))
    if creds:
        farm.account = creds[0]
    cert, key = Path("/app/certs/cert.pem"), Path("/app/certs/key.pem")
    if tls_port and cert.is_file():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        sessions = Sessions(creds)
        for p in dict.fromkeys((tls_port, STANDARD_TLS_PORT)):  # 443 is where a client looks when it is given no port
            try:
                secure = ThreadingHTTPServer(("0.0.0.0", p), make_handler(farm, sessions, stats, lock))
            except OSError:  # a runtime that does not allow low ports for this user: 8443 still serves
                continue
            secure.socket = ctx.wrap_socket(secure.socket, server_side=True)
            threading.Thread(target=secure.serve_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(farm, None, stats, lock)).serve_forever()


if __name__ == "__main__":
    main()
