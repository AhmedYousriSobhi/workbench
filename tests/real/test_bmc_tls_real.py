"""bmc-farm's TLS surface in the live min stack: what a Redfish client sees of a BMC.

Real TLS with a self-signed certificate the client pins, a session login, the node picked by the Host header.
"""
import hashlib
import http.client
import json
import os
import ssl
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from benchctl import stack

ROOT = Path(__file__).resolve().parents[2]
PORT = 8443


def fingerprint(port: int = PORT) -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    import socket
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s, ctx.wrap_socket(s) as t:
        return hashlib.sha256(t.getpeercert(binary_form=True)).hexdigest()


def request(method: str, path: str, host: str, headers: dict | None = None, body: dict | None = None,
            port: int = PORT):
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    c = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=10)
    h = {"Host": host, "Accept": "application/json", **(headers or {})}
    c.request(method, path, body=None if body is None else json.dumps(body), headers=h)
    r = c.getresponse()
    data = r.read()
    out = r.status, dict(r.getheaders()), json.loads(data) if data else None
    c.close()
    return out


@pytest.fixture(scope="module")
def farm():
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    assert wait_for(lambda: _up(), timeout=40)
    user, _, pw = (ROOT / stack.BMC_CREDENTIALS).read_text().strip().partition(":")
    yield user, pw
    stack.down(say=lambda m: None)


def _up() -> bool:
    try:
        return request("GET", "/redfish/v1", "bmc-cn001")[0] == 200
    except OSError:
        return False


def test_certificate_is_stable_and_credentials_are_required(farm):
    assert fingerprint() == fingerprint()
    assert request("GET", "/redfish/v1/Systems", "bmc-cn001")[0] == 401


def test_session_login_then_reading_the_node_chosen_by_host(farm):
    user, pw = farm
    for node in ("cn001", "gpu002"):
        status, headers, _ = request("POST", "/redfish/v1/SessionService/Sessions", f"bmc-{node}:{PORT}",
                                     body={"UserName": user, "Password": pw})
        assert status == 201
        auth = {"X-Auth-Token": headers["X-Auth-Token"]}
        _, _, system = request("GET", "/redfish/v1/Systems/1", f"bmc-{node}:{PORT}", auth)
        assert system["HostName"] == node and system["PowerState"] == "On"
        assert request("DELETE", headers["Location"], f"bmc-{node}", auth)[0] == 204


def test_wrong_password_is_refused(farm):
    user, _ = farm
    assert request("POST", "/redfish/v1/SessionService/Sessions", "bmc-cn001",
                   body={"UserName": user, "Password": "nope"})[0] == 401


def test_every_node_resolves_by_its_network_alias(farm):
    import subprocess
    out = subprocess.run(["docker", "exec", "workbench-jira-mock-1", "python", "-c",
                          "import socket;print(socket.gethostbyname('bmc-cn001'))"],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0 and out.stdout.strip().startswith("172.")


def test_legacy_http_surface_still_answers_without_auth(farm):
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:8000/cn001/redfish/v1/Systems/1", timeout=5) as r:
        assert json.loads(r.read())["PowerState"] == "On"


def test_bmc_is_reachable_through_the_login_node_with_the_same_pinned_certificate(farm):
    """A client dials the BMC with an SSH forwardOut from the login node; the node is still picked by Host."""
    import socket
    import subprocess

    from benchctl import compose

    user, pw = farm
    key = ROOT / stack.SSH_DIR / "id_ed25519"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        local = sock.getsockname()[1]
    proc = subprocess.Popen(["ssh", "-N", "-i", str(key), "-p", str(compose.SSH_PORT), "-o", "IdentitiesOnly=yes",
                             "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                             "-o", "ExitOnForwardFailure=yes", "-L", f"{local}:bmc-cn002:{PORT}", "alice@127.0.0.1"])
    try:
        assert wait_for(lambda: _tunnel_up(local), timeout=20)
        assert fingerprint(local) == fingerprint()  # what a client pinned directly still matches via the tunnel
        status, headers, _ = request("POST", "/redfish/v1/SessionService/Sessions", f"bmc-cn002:{PORT}",
                                     body={"UserName": user, "Password": pw}, port=local)
        assert status == 201
        _, _, system = request("GET", "/redfish/v1/Systems/1", f"bmc-cn002:{PORT}",
                               {"X-Auth-Token": headers["X-Auth-Token"]}, port=local)
        assert system["HostName"] == "cn002"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _tunnel_up(port: int) -> bool:
    try:
        return request("GET", "/redfish/v1", "bmc-cn002", port=port)[0] == 200
    except OSError:
        return False
