"""Expiry and authentication faults (issue 549): the `auth` scenario target. The openssl test makes a real mini PKI and checks the dates."""
import importlib.util
import json
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml
from invhelp import inv

from benchctl import authfaults, scenario

SIM = yaml.safe_load(Path("sim.yaml").read_text())
NODES, USERS = {"cn001", "gpu001"}, {"alice", "bob"}


class CP:
    def __init__(self, out="", code=0, err=""):
        self.stdout, self.returncode, self.stderr = out, code, err


def recorder(out=""):
    calls = []

    def run(cmd):
        calls.append(cmd)
        return CP(out)
    return run, calls


def test_problems_name_the_bad_key_user_node_and_ttl():
    ok = {"slurm_token_ttl": {"user": "alice", "ttl": "30s"}, "munge_wrong_key": ["cn001"], "bmc_tls_expired": True}
    assert authfaults.problems(ok, NODES, USERS) == []
    assert authfaults.problems({}, NODES, USERS)
    bad = authfaults.problems({"nope": 1, "munge_restore": ["cn999"], "teleport_cert_ttl": {"user": "eve", "ttl": "soon"},
                               "k8s_cert_expired": "yes", "teleport_cert_restore": []}, NODES, USERS)
    text = "\n".join(bad)
    assert "unknown auth key 'nope'" in text and "unknown node 'cn999'" in text and "teleport_cert_ttl needs" in text
    assert "k8s_cert_expired is true" in text and "teleport_cert_restore needs a list" in text


def test_shipped_auth_scenarios_validate_and_use_the_auth_target():
    names = ("auth-slurm-token-expiry", "auth-teleport-cert-expiry", "auth-munge-wrong-key", "bmc-tls-expired", "k8s-cert-expired")
    for n in names:
        sc = scenario.load(n)
        assert scenario.validate(sc, SIM) == [] and {s["target"] for s in sc["steps"]} == {"auth"}
    assert scenario.validate({"steps": [{"at": "0s", "target": "auth", "do": {"munge_wrong_key": ["zz9"]}}]}, SIM)


def test_the_page_cannot_play_auth_steps():
    step = scenario.Step(0, "auth", {"bmc_tls_expired": True})
    assert "host CLI" in scenario.page_support(step)
    assert scenario.execute(step, host=False).status == "skipped"


def test_a_token_step_saves_a_short_lived_token_and_never_prints_it(tmp_path, monkeypatch):
    monkeypatch.setattr(authfaults, "TOKEN_FILE", tmp_path / "secrets" / "slurm_token")
    run, calls = recorder("SLURM_JWT=abc.def.ghi\n")
    assert authfaults.apply({"slurm_token_ttl": {"user": "alice", "ttl": "2m"}}, run, None, SIM) == ""
    assert calls[0][-3:] == ["token", "username=alice", "lifespan=120"]
    assert authfaults.TOKEN_FILE.read_text() == "abc.def.ghi\n" and oct(authfaults.TOKEN_FILE.stat().st_mode & 0o777) == "0o600"
    assert authfaults.apply({"slurm_token_ttl": {"user": "alice", "ttl": "2m"}}, lambda c: CP("", 1, "no jwt"), None, SIM).startswith("slurm token: no jwt")


def test_munge_steps_keep_the_nodes_own_key_and_restart_munged_as_root_exec():
    run, calls = recorder()
    assert authfaults.apply({"munge_wrong_key": ["cn001"], "munge_restore": ["gpu001"]}, run, None, SIM) == ""
    wrong, right = calls
    assert wrong[:5] == ["docker", "exec", "-u", "root", "workbench-cn001-1"] and "cp -n /etc/munge/munge.key /etc/munge/munge.key.good" in wrong[-1]
    assert "/dev/urandom" in wrong[-1] and "munged --force" in wrong[-1]
    assert right[4] == "workbench-gpu001-1" and "cp /etc/munge/munge.key.good /etc/munge/munge.key" in right[-1] and "/dev/urandom" not in right[-1]


def test_teleport_steps_resign_the_identity_with_the_ttl_and_the_normal_life():
    run, calls = recorder()
    do = {"teleport_cert_ttl": {"user": "alice", "ttl": "20s"}, "teleport_cert_restore": ["bob"]}
    assert authfaults.apply(do, run, None, SIM) == ""
    short, normal = calls
    assert "--ttl=20s" in short and "--user=alice" in short and short[-1] == "--overwrite" and "tctl" in short
    assert "--ttl=12h" in normal and "--user=bob" in normal


def test_bmc_step_posts_to_the_farm():
    seen = []
    assert authfaults.apply({"bmc_tls_expired": True}, None, lambda url, body: seen.append((url, body)), SIM) == ""
    assert seen == [("http://127.0.0.1:8000/sim/tls", {"expired": True})]


def test_the_farm_swaps_its_certificate_on_sim_tls():
    spec = importlib.util.spec_from_file_location("bmc", "components/bmc-farm/bmc.py")
    bmc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bmc)
    farm = bmc.Farm(bmc.load_nodes(inv(Path("sim.yaml"))))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bmc.make_handler(farm))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/sim/tls"

    def post(body):
        return urlopen(Request(url, data=json.dumps(body).encode(), method="POST"), timeout=5)
    try:
        with pytest.raises(HTTPError) as e:  # no TLS served: say so
            post({"expired": True})
        assert e.value.code == 400
        got = []
        farm.set_tls = got.append
        post({"expired": True}).close()
        post({"expired": False}).close()
        assert got == [True, False]
    finally:
        srv.shutdown()


@pytest.mark.skipif(not shutil.which("openssl"), reason="openssl missing")
def test_the_api_server_certificate_is_really_expired_then_the_good_one_returns(tmp_path):
    def ossl(*a):
        subprocess.run(["openssl", *a], cwd=tmp_path, check=True, capture_output=True)
    ossl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "ca.key")
    ossl("req", "-x509", "-new", "-key", "ca.key", "-subj", "/CN=ca", "-days", "30", "-out", "ca.crt")
    ossl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "apiserver.key")
    ossl("req", "-new", "-key", "apiserver.key", "-subj", "/CN=kube-apiserver", "-out", "a.csr")
    (tmp_path / "e.ext").write_text("subjectAltName=DNS:k8s-apiserver,IP:127.0.0.1\n")
    ossl("x509", "-req", "-in", "a.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-days", "30", "-extfile", "e.ext", "-out", "apiserver.crt")
    good = (tmp_path / "apiserver.crt").read_bytes()
    docker = []

    def run(cmd):
        if cmd[0] == "docker":
            docker.append(cmd)
            return CP()
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return r

    def expired():
        return subprocess.run(["openssl", "x509", "-in", str(tmp_path / "apiserver.crt"), "-noout", "-checkend", "0"], capture_output=True, check=False).returncode == 1
    assert authfaults.k8s_cert(True, run, tmp_path) == "" and expired()
    text = subprocess.run(["openssl", "x509", "-in", str(tmp_path / "apiserver.crt"), "-noout", "-ext", "subjectAltName"], capture_output=True, text=True, check=True).stdout
    assert "k8s-apiserver" in text and "127.0.0.1" in text  # the names stay, only the dates changed
    assert subprocess.run(["openssl", "verify", "-CAfile", str(tmp_path / "ca.crt"), "-no_check_time", str(tmp_path / "apiserver.crt")], capture_output=True, check=False).returncode == 0
    assert authfaults.k8s_cert(True, run, tmp_path) == "" and (tmp_path / "apiserver.crt.good").read_bytes() == good  # twice keeps the good one
    assert authfaults.k8s_cert(False, run, tmp_path) == "" and not expired() and (tmp_path / "apiserver.crt").read_bytes() == good
    assert authfaults.k8s_cert(False, run, tmp_path) == "" and len(docker) == 3  # nothing to put back: no restart
    assert docker[0][:2] == ["docker", "restart"] and docker[0][-1] == "workbench-k8s-apiserver-1"


def test_a_failing_command_is_reported_by_the_scenario_step():
    out = scenario.execute(scenario.Step(0, "auth", {"munge_wrong_key": ["cn001"]}), runner=lambda c: CP("", 1, "no such container"))
    assert out.status == "failed" and "munge on cn001: no such container" in out.detail
