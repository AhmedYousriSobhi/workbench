#!/usr/bin/env python3
"""A demo client for the mocks, so the Traffic tab has real counted requests (issue 650). Runs on the login node:
`benchctl load on --mocks`. It sends the calls a monitoring client makes to jira-mock, bcm-mock and bmc-farm, with Poisson
gaps (mean 60/rate s) and a User-Agent of its own, so the page names it. Nothing is faked: the mocks count what arrives."""
import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

UA = "workbench-demo-client/1.0"
JIRA, BCM, BMC = "http://jira-mock:8080", "http://bcm-mock:8081", "http://bmc-farm:8000"
PAUSED = "/run/workbench-flags/arrivals.paused"  # the governor's L1 flag, as for the user simulator


def calls(nodes: list[str]) -> list[tuple[str, str, bytes | None]]:
    """(method, url, body) of every call the client rotates through."""
    jql = json.dumps({"jql": "project = OPS ORDER BY created DESC", "maxResults": 20}).encode()
    devices = json.dumps({"service": "cmdevice", "call": "getDevices"}).encode()
    return [("GET", f"{JIRA}/rest/api/2/serverInfo", None), ("GET", f"{JIRA}/rest/api/2/myself", None), ("POST", f"{JIRA}/rest/api/2/search", jql),
            ("POST", f"{BCM}/json", devices), *[("GET", f"{BMC}/{n}/redfish/v1/", None) for n in nodes]]


def send(method: str, url: str, body: bytes | None) -> int:
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": UA, **({"Content-Type": "application/json"} if body else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0       # the mock is down or restarting: try again at the next arrival


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=30, help="requests per minute")
    ap.add_argument("--nodes", default="cn001", help="nodes whose Redfish root is read, comma separated")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    todo = calls(a.nodes.split(","))
    while True:
        time.sleep(rng.expovariate(a.rate / 60))
        if os.path.exists(PAUSED):
            continue
        send(*rng.choice(todo))


if __name__ == "__main__":
    sys.exit(main())
