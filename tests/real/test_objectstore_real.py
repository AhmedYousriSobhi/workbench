"""The optional object store (issue 672, tier 3 of ADR 0078): Garage on the cluster network, one bucket every node reaches.
Not run by the agent: `./scripts/verify.sh --unit objectstore --clean`. No monitoring app is connected."""
import os
import subprocess
from pathlib import Path

import pytest
from helpers import prom_up, wait_for

from benchctl import objectstore, stack, workbench

ROOT = Path(__file__).resolve().parents[2]
SECRET_FILES = ("access_key", "secret_key", "rpc_secret", "metrics_token")


def benchctl(*args: str, timeout: int = 1200) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / "scripts/benchctl"), *args], capture_output=True, text=True, timeout=timeout, check=False)


def on(node: str, script: str, user: str = "alice") -> subprocess.CompletedProcess:
    """Run a login shell on a node as a user, so /etc/profile.d loads the credentials."""
    return subprocess.run(["docker", "exec", "-u", user, f"workbench-{node}-1", "bash", "-lc", script], capture_output=True, text=True, timeout=60, check=False)


@pytest.fixture(scope="module")
def built():
    os.chdir(ROOT)
    out = benchctl("up", "--cpu-nodes", "2", "--gpu-nodes", "0", "--with", "objectstore")
    assert out.returncode == 0, out.stdout + out.stderr
    assert wait_for(lambda: on("cn001", "s5cmd ls").returncode == 0, timeout=90), "the object store never answered: " + on("cn001", "s5cmd ls").stderr
    yield
    stack.down(say=lambda m: None)
    workbench.forget()


def test_the_container_is_unprivileged_limited_and_not_root(built):
    info = subprocess.run(["docker", "inspect", "workbench-objectstore-1", "--format", "{{.HostConfig.Privileged}} {{.HostConfig.Memory}} {{.Config.User}}"],
                          capture_output=True, text=True, check=True).stdout.split()
    assert info[0] == "false" and int(info[1]) == objectstore.LIMIT_MB * 1024 * 1024 and not info[2].startswith("0:")


def test_a_file_put_on_one_node_is_read_on_another(built):
    put = on("cn001", "head -c 3000000 /dev/urandom > /tmp/obj.bin && sha256sum /tmp/obj.bin > /tmp/obj.sum && s5cmd cp /tmp/obj.bin s3://$WORKBENCH_S3_BUCKET/obj.bin")
    assert put.returncode == 0, put.stderr
    want = on("cn001", "cut -d' ' -f1 /tmp/obj.sum").stdout.strip()
    got = on("cn002", "s5cmd cp s3://$WORKBENCH_S3_BUCKET/obj.bin /tmp/back.bin && sha256sum /tmp/back.bin | cut -d' ' -f1")  # s5cmd prints its own `cp ...` line first
    assert got.returncode == 0 and got.stdout.strip().splitlines()[-1] == want, got.stdout + got.stderr
    listing = on("cn002", "s5cmd ls s3://$WORKBENCH_S3_BUCKET/")
    assert "obj.bin" in listing.stdout


def test_a_wrong_key_is_refused(built):
    r = on("cn001", "AWS_SECRET_ACCESS_KEY=wrong s5cmd ls s3://$WORKBENCH_S3_BUCKET/")
    assert r.returncode != 0


def test_no_server_secret_reaches_a_node(built):
    vals = {n: (ROOT / "compose/generated/secrets/objectstore" / n).read_text().strip() for n in SECRET_FILES}
    text = on("cn001", "cat /run/workbench/objectstore/s3.env; ls /run/workbench/objectstore", user="root").stdout
    assert "s3.env" in text and vals["rpc_secret"] not in text and vals["metrics_token"] not in text
    assert vals["access_key"] in text  # the key pair is the node-facing credential


def test_prometheus_scrapes_the_disk_series_and_a_stopped_service_reads_down(built):
    import urllib.parse
    import urllib.request

    def series() -> bool:
        q = urllib.parse.quote("garage_local_disk_avail")
        try:
            return bool(__import__("json").loads(urllib.request.urlopen(f"http://127.0.0.1:9090/api/v1/query?query={q}", timeout=5).read())["data"]["result"])
        except (OSError, ValueError, KeyError):
            return False

    assert wait_for(lambda: prom_up("objectstore").get("objectstore:3903") == "1", timeout=90), prom_up("objectstore")
    assert wait_for(series, timeout=60)
    assert subprocess.run(["docker", "stop", "workbench-objectstore-1"], capture_output=True, timeout=60, check=False).returncode == 0
    assert wait_for(lambda: prom_up("objectstore").get("objectstore:3903") == "0", timeout=90), prom_up("objectstore")
    subprocess.run(["docker", "start", "workbench-objectstore-1"], capture_output=True, timeout=60, check=False)
