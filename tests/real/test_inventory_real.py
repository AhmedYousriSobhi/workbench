"""One node inventory, live (issue 462): the mocks serve the shape that `benchctl up` wrote to inventory.json, and Slurm shows the same shape."""
import json
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, release, wait_for

ROOT = Path(__file__).resolve().parents[2]
BCM = "http://127.0.0.1:8081"
INVENTORY = ROOT / "compose/generated/inventory.json"


def rpc(call_: str, *args) -> dict:
    req = urllib.request.Request(f"{BCM}/json", data=json.dumps({"service": "cmdevice", "call": call_, "args": list(args)}).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


@pytest.fixture(scope="module")
def stack():
    import os
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    assert wait_for(lambda: INVENTORY.is_file(), timeout=30)
    assert wait_for(lambda: rpc("getDevices").get("result"), timeout=30)
    yield
    release()


def sinfo() -> dict[str, tuple[int, int]]:
    """{node: (CPUs, memory MB)} as Slurm reports it."""
    out = subprocess.run(["docker", "exec", "-u", "alice", "workbench-login-1", "sinfo", "-h", "-N", "-o", "%N %c %m"],
                         capture_output=True, text=True, timeout=30, check=False).stdout
    return {f[0]: (int(f[1]), int(f[2])) for f in (line.split() for line in out.splitlines()) if len(f) == 3}


def test_slurm_and_bcm_show_the_shape_in_the_inventory(stack):
    inventory = {n["name"]: n for n in json.loads(INVENTORY.read_text())["nodes"]}
    assert wait_for(lambda: len(sinfo()) >= 6, timeout=60)
    slurm = sinfo()
    for name, node in inventory.items():
        assert slurm[name] == (node["cpus"], node["memory_mb"]), name
        dev = rpc("getDevice", name)["result"]
        assert (dev["cpus"], dev["memory_mb"], dev["gpus"]) == (node["cpus"], node["memory_mb"], node["gpus"]), name


def test_the_mounted_inventory_is_the_generated_file(stack):
    """The BCM container reads the same bytes `benchctl up` wrote, not a copy of sim.yaml."""
    mounted = subprocess.run(["docker", "exec", "workbench-bcm-mock-1", "python", "-c", "print(open('/inventory.json').read(), end='')"],
                             capture_output=True, text=True, timeout=30, check=False).stdout
    assert mounted == INVENTORY.read_text()
