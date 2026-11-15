"""`get faults` reads back the service and credential faults (benchctl/faultstate.py). Hermetic: files are in tmp_path, the
components and Docker are faked."""
import base64
import json
import struct
import subprocess

from benchctl import faultstate


def jwt(exp):
    part = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{part}.s"


def fetcher(table):
    def fetch(url, body=None, timeout=0.5):
        got = table[url]
        if isinstance(got, Exception):
            raise got
        return got
    return fetch


def test_jira_failure_only_while_requests_are_left():
    f = faultstate.JIRA_FAILURE
    assert faultstate.jira_failure(fetcher({f: {"mode": "503", "count": 2, "delay": 2}}))[0]["label"].startswith("Failure mode 503 for the next 2")
    assert faultstate.jira_failure(fetcher({f: {"mode": "503", "count": 0, "delay": 2}})) == []  # budget spent
    assert faultstate.jira_failure(fetcher({f: {"mode": None, "count": 1, "delay": 2}})) == []
    assert faultstate.jira_failure(fetcher({f: OSError("down")})) == []  # not answering is not a fault
    assert faultstate.jira_failure(fetcher({f: "old mock"})) == []


def test_bmc_tls_expired():
    assert faultstate.bmc_tls(fetcher({faultstate.BMC_TLS: {"served": True, "expired": True}}))[0]["target"] == "bmc-farm"
    assert faultstate.bmc_tls(fetcher({faultstate.BMC_TLS: {"served": True, "expired": False}})) == []
    assert faultstate.bmc_tls(fetcher({faultstate.BMC_TLS: ValueError("old image")})) == []


def test_k8s_cert_expired_when_the_serving_cert_differs_from_the_kept_good_one(tmp_path):
    assert faultstate.k8s_cert(tmp_path) == []  # nothing ever expired
    (tmp_path / "apiserver.crt").write_bytes(b"good")
    (tmp_path / "apiserver.crt.good").write_bytes(b"good")
    assert faultstate.k8s_cert(tmp_path) == []
    (tmp_path / "apiserver.crt").write_bytes(b"dated")
    assert faultstate.k8s_cert(tmp_path)[0]["target"] == "k8s-api"


def test_slurm_token_expired_by_its_own_exp_and_the_value_is_never_returned(tmp_path):
    f = tmp_path / "slurm_token"
    assert faultstate.slurm_token(f, now=100) == []  # no file
    f.write_text(jwt(50) + "\n")
    rows = faultstate.slurm_token(f, now=100)
    assert rows[0]["target"] == "slurmrestd" and jwt(50) not in json.dumps(rows)
    f.write_text(jwt(500) + "\n")
    assert faultstate.slurm_token(f, now=100) == []
    f.write_text("not a token")
    assert faultstate.slurm_token(f, now=100) == []


def test_munge_wrong_key_when_the_node_keeps_its_good_key():
    seen = []

    def run(cmd):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if "workbench-cn002-1" in cmd else 1, "", "")
    rows = faultstate.munge(["cn001", "cn002"], run)
    assert [r["target"] for r in rows] == ["cn002"] and rows[0]["kind"] == "auth"
    assert seen[0][:4] == ["docker", "exec", "workbench-cn001-1", "test"]


def test_collect_joins_every_source(tmp_path, monkeypatch):
    monkeypatch.setattr(faultstate, "k8s_cert", list)
    monkeypatch.setattr(faultstate, "slurm_token", list)
    monkeypatch.setattr(faultstate, "teleport_cert", list)
    got = faultstate.collect(fetcher({faultstate.JIRA_FAILURE: {"mode": "429", "count": 1}, faultstate.BMC_TLS: {"expired": True}}),
                             lambda cmd: subprocess.CompletedProcess(cmd, 1, "", ""), ["cn001"])
    assert [r["target"] for r in got] == ["jira-mock", "bmc-farm"]


def ssh_cert_line(before, kind="ecdsa-sha2-nistp256-cert-v01@openssh.com", key_fields=2):
    def s(b):
        return struct.pack(">I", len(b)) + b
    blob = s(kind.encode()) + s(b"nonce") + b"".join(s(b"k") for _ in range(key_fields))
    blob += struct.pack(">QI", 1, 1) + s(b"id") + s(b"alice") + struct.pack(">QQ", 0, before)
    return f"{kind} {base64.b64encode(blob).decode()} extra"


def test_teleport_certificate_read_from_the_identity_file_without_returning_key_material(tmp_path):
    (tmp_path / "alice").write_text("-----BEGIN " + "PRIVATE KEY-----\nSECRETKEY\n-----END " + "PRIVATE KEY-----\n" + ssh_cert_line(1000) + "\n")
    (tmp_path / "bob").write_text(ssh_cert_line(5000) + "\n")
    (tmp_path / "carol").write_text(ssh_cert_line(1000, "ssh-ed25519-cert-v01@openssh.com", 1) + "\n")
    (tmp_path / "dave").write_text("no certificate here\n")
    rows = faultstate.teleport_cert(tmp_path, now=2000)
    assert [r["label"] for r in rows] == ["certificate of alice expired", "certificate of carol expired"]
    assert all(r["target"] == "teleport" and r["kind"] == "auth" for r in rows)
    assert "SECRETKEY" not in json.dumps(rows)
    assert faultstate.teleport_cert(tmp_path, now=10) == []  # nothing has ended yet
    assert faultstate.teleport_cert(tmp_path / "missing") == []  # Teleport not built
