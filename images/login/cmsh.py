#!/usr/bin/env python3
"""Fake read-only `cmsh` for the login node, which doubles as the BCM head node (ADR 0013, issue 66).

Answers the fixed one-shot scripts a monitoring client runs (`cmsh -c "device; list"`, `device; status`,
`category; list`, `jobqueue; list`, `monitoring; healthchecks; list`) from bcm-mock's node state, so a node a scenario
took down shows DOWN. Anything that would change the cluster is refused. The table layout follows real cmsh closely
enough for a parser; it is an assumed subset until a real capture exists. No interactive console (use -c).
"""
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request

BCM_URL = os.environ.get("HPCSIM_BCM_URL", "http://bcm-mock:8081/json")
HEAD_IP = "10.141.255.254"
HEALTHCHECKS = [("DeviceIsUp", "Checks whether the device is up"), ("ManagedServicesOk", "Checks that the managed services run"),
                ("failedprejob", "Checks for jobs that failed their prejob health check")]
MUTATING = {"add", "append", "clear", "clone", "commit", "foreach", "power", "reboot", "remove", "rename", "run", "set",
            "shutdown", "swap", "unset", "use", "refresh", "removefrom", "reset", "services", "ssh"}


class Unreachable(Exception):
    pass


def call(service: str, name: str, args: list | None = None):
    req = urllib.request.Request(BCM_URL, data=json.dumps({"service": service, "call": name, "args": args or []}).encode(),
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Unreachable(str(e)) from e
    if body.get("error"):
        raise Unreachable(body["error"])
    return body["result"]


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Columns padded to their widest cell, a dashed rule under the header, as cmsh prints."""
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    fmt = lambda cells: " ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()
    return "\n".join([fmt(headers), " ".join("-" * w for w in widths), *(fmt(r) for r in rows)])


def state(s: str) -> str:
    return "[" + s.center(8) + "]"


def devices() -> list[dict]:
    head = {"hostname": socket.gethostname(), "childType": "HeadNode", "category": "", "ip": HEAD_IP, "status": "UP",
            "mac": "FA:16:3E:00:00:FE"}
    nodes = [{**d, "mac": f"FA:16:3E:00:00:{i:02X}"} for i, d in enumerate(call("cmdevice", "getDevices"), 1)]
    return [head, *nodes]


def device_list() -> str:
    rows = [[d["childType"], d["hostname"], d["mac"], d["category"], d["ip"], "internalnet", state(d["status"])] for d in devices()]
    return table(["Type", "Hostname (key)", "MAC (key)", "Category", "IP", "Network", "Status"], rows)


def device_status() -> str:
    ds = devices()
    width = max(len(d["hostname"]) for d in ds) + 4
    return "\n".join(f"{d['hostname']} {'.' * (width - len(d['hostname']))} {state(d['status'])}" for d in ds)


def category_list() -> str:
    ds = devices()
    rows = [[c, f"default-image-{c}", str(sum(1 for d in ds if d['category'] == c))] for c in sorted({d["category"] for d in ds[1:]})]
    return table(["Name (key)", "Software image", "Nodes"], rows)


def jobqueue_list() -> str:
    r = subprocess.run(["sinfo", "-h", "-o", "%R %D"], capture_output=True, text=True, check=False)
    rows = [["slurm", *line.split()] for line in r.stdout.splitlines() if line.strip()]
    return table(["Type", "Name (key)", "Nodes"], rows)


def healthchecks_list() -> str:
    return table(["Name (key)", "Description"], [list(h) for h in HEALTHCHECKS])


SCRIPTS = {("device", "list"): device_list, ("device", "status"): device_status, ("category", "list"): category_list,
           ("jobqueue", "list"): jobqueue_list, ("monitoring", "healthchecks", "list"): healthchecks_list}


def run(script: str) -> tuple[int, str, str]:
    """(exit status, stdout, stderr) for one `-c` script."""
    parts = tuple(p.strip() for p in script.split(";") if p.strip())
    if not parts:
        return 0, "", ""
    for p in parts:
        verb = p.split()[0]
        if verb in MUTATING:
            return 1, "", f"Error: this cmsh is read-only (opti-sim); '{p}' would change the cluster\n"
    fn = SCRIPTS.get(parts)
    if fn is None:
        return 1, "", f"Error: unknown command: {'; '.join(parts)}\n"
    try:
        return 0, fn() + "\n", ""
    except Unreachable as e:
        return 1, "", f"Unable to connect to CMDaemon: {e}\n"


def main(argv: list[str]) -> int:
    script, args = None, [a for a in argv if a != "--hide-events"]
    if args[:1] in (["-v"], ["--version"]):
        print("cmsh (opti-sim fake, read-only)")
        return 0
    if args[:1] in (["-c"], ["--command"]) and len(args) >= 2:
        script = args[1]
    if script is None:
        sys.stderr.write("cmsh: the interactive console is not available in this simulation; use cmsh -c \"device; list\"\n")
        return 1
    code, out, err = run(script)
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
