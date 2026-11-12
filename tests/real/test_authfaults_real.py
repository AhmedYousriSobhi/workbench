"""Expiry and authentication faults on the live stack (issue 549): each `auth` step, applied by the scenario engine's own executor,
and the real error it causes. Not covered here: the Teleport step (needs --with teleport, see test_teleport_real), the Kubernetes step (needs
--with k8s, see test_k8s_real) and slurmrestd's 401 for an expired token (slurmrestd runs only with the accounting group)."""
import base64
import json
import os
import socket
import ssl
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, release, wait_for

from benchctl import authfaults, scenario

ROOT = Path(__file__).resolve().parents[2]


def step(**do):
    out = scenario.execute(scenario.Step(0, "auth", do))
    assert out.status == "ok", out.detail


def sh(*cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=60, check=False)


@pytest.fixture(scope="module")
def stack_up():
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    yield
    step(munge_restore=["cn001"], bmc_tls_expired=False)  # leave the cluster as found
    release()


def peer_pem(port=8443) -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s, ctx.wrap_socket(s) as t:
        return ssl.DER_cert_to_PEM_cert(t.getpeercert(binary_form=True))


def verifies(port=8443) -> str:
    """"" when a client that checks dates accepts the certificate the farm presents (the pinned one), else the error."""
    pem = peer_pem(port)
    ctx = ssl.create_default_context(cadata=pem)
    ctx.check_hostname = False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s, ctx.wrap_socket(s):
            return ""
    except ssl.SSLCertVerificationError as e:
        return e.verify_message


def test_the_bmc_farm_presents_an_expired_certificate_then_the_good_one(stack_up):
    assert verifies() == ""
    step(bmc_tls_expired=True)
    assert wait_for(lambda: "expired" in verifies(), timeout=10), "the farm still presents a valid certificate"
    step(bmc_tls_expired=False)
    assert wait_for(lambda: verifies() == "", timeout=10)


def test_a_wrong_munge_key_is_rejected_by_the_controller_and_the_node_recovers(stack_up):
    def accepted() -> bool:
        cred = sh("docker", "exec", "workbench-cn001-1", "munge", "-n").stdout
        return sh("docker", "exec", "-i", "workbench-slurmctld-1", "unmunge", stdin=cred).returncode == 0
    assert accepted()
    step(munge_wrong_key=["cn001"])
    assert wait_for(lambda: not accepted(), timeout=15)
    bad = sh("docker", "exec", "-i", "workbench-slurmctld-1", "unmunge", stdin=sh("docker", "exec", "workbench-cn001-1", "munge", "-n").stdout)
    assert "Invalid credential" in bad.stderr
    step(munge_wrong_key=["cn001"])  # a second one must not lose the original key
    step(munge_restore=["cn001"])
    assert wait_for(accepted, timeout=15)


def test_a_slurm_token_step_issues_a_token_that_ends_when_asked(stack_up):
    step(slurm_token_ttl={"user": "alice", "ttl": "20s"})
    token = (ROOT / authfaults.TOKEN_FILE).read_text().strip()
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    assert claims["sun"] == "alice" and claims["exp"] - claims["iat"] == 20 and claims["exp"] > time.time()
