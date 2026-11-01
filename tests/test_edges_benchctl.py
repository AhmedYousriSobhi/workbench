"""Remaining behaviour of benchctl helpers: scenario expectations and jira checks, progress --check, checks.run, diag, UI launcher."""
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from benchctl import checks, diag, progress, scenario


class CP:
    def __init__(self, out="", code=0, err=""):
        self.stdout, self.returncode, self.stderr = out, code, err


def serve_json(body, port_holder):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            port_holder["path"] = self.path
            data = json.dumps(body).encode() if not isinstance(body, bytes) else body
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_jira_labels_builds_a_jql_and_reads_the_total(monkeypatch):
    seen = {}
    srv = serve_json({"total": 2}, seen)
    real_open = scenario.urllib.request.urlopen
    monkeypatch.setattr(scenario.urllib.request, "urlopen",
                        lambda url, timeout: real_open(url.replace("127.0.0.1:8080", f"127.0.0.1:{srv.server_port}"), timeout=timeout))
    try:
        assert scenario.jira_labels(["gpu", "xid"]) is True
    finally:
        srv.shutdown()
    assert "labels%20%3D%20gpu%20AND%20labels%20%3D%20xid" in seen["path"]


def test_jira_labels_is_false_when_nothing_matches_or_jira_is_down(monkeypatch):
    seen = {}
    srv = serve_json({"total": 0}, seen)
    real_open = scenario.urllib.request.urlopen
    monkeypatch.setattr(scenario.urllib.request, "urlopen",
                        lambda url, timeout: real_open(url.replace("127.0.0.1:8080", f"127.0.0.1:{srv.server_port}"), timeout=timeout))
    assert scenario.jira_labels(["x"]) is False
    srv.shutdown()
    assert scenario.jira_labels(["x"]) is False  # connection refused
    srv2 = serve_json(b"not json", seen)
    monkeypatch.setattr(scenario.urllib.request, "urlopen",
                        lambda url, timeout: real_open(url.replace("127.0.0.1:8080", f"127.0.0.1:{srv2.server_port}"), timeout=timeout))
    assert scenario.jira_labels(["x"]) is False  # garbage body
    srv2.shutdown()


def test_expectations_are_kind_specific():
    label, holds, within = scenario.expectation({"slurm.node_state": {"node": "cn001", "state": "drain", "within": "2m"}},
                                                runner=lambda c: CP("drained"))
    assert holds() is True and within == 120 and "cn001" in label
    _, no_host, _ = scenario.expectation({"slurm.node_state": {"node": "cn001", "state": "x"}}, host=False)
    assert no_host is None  # the UI cannot evaluate Slurm expectations
    assert scenario.expectation({"k8s.pod": {}})[1] is None


def test_node_state_is_empty_when_slurm_cannot_be_asked():
    assert scenario.node_state("cn001", runner=lambda c: CP(code=1, err="no container")) == ""
    assert scenario.node_state("cn001", runner=lambda c: CP("idle\nidle\n")) == "idle"


def test_resume_failure_is_reported():
    out = scenario.execute(scenario.Step(0, "slurm", {"resume": ["cn001"]}), runner=lambda c: CP(code=1, err="not found"))
    assert out.status == "failed" and out.detail == "not found"


def test_post_sends_json_to_the_endpoint():
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        scenario.post(f"http://127.0.0.1:{srv.server_port}/x", {"node": "cn001"})
    finally:
        srv.shutdown()
    assert got["body"] == {"node": "cn001"}


def test_progress_check_flags_stale_pages_and_write_fixes_them(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(progress, "MD", tmp_path / "PROGRESS.md")
    monkeypatch.setattr(progress, "HTML", tmp_path / "progress.html")
    assert progress.main(["--check"]) == 1 and "STALE" in capsys.readouterr().out  # nothing written yet
    assert progress.main([]) == 0
    assert (tmp_path / "PROGRESS.md").read_text().startswith("#")
    assert progress.main(["--check"]) == 0
    (tmp_path / "PROGRESS.md").write_text("edited by hand")
    assert progress.stale() == [tmp_path / "PROGRESS.md"]


def test_run_wraps_missing_binaries_and_timeouts():
    r = checks.run(["definitely-not-a-binary-xyz"])
    assert r.returncode == 127 and r.stderr
    t = checks.run(["sleep", "5"], timeout=0.1)
    assert t.returncode == 127


def test_cgroup_and_psi_failures_have_a_fix(tmp_path):
    assert checks.check_cgroup_v2(tmp_path).status == checks.FAIL  # no cgroup.controllers file
    missing = checks.check_psi(tmp_path / "none")
    assert missing.status == checks.FAIL and "psi=1" in missing.fix


def test_libvirt_warns_when_virsh_is_missing_or_fails():
    bad = checks.check_libvirt(lambda cmd: CP(code=127, err="virsh: not found"))
    assert bad.status == checks.WARN and "virsh" in bad.detail
    assert checks.check_libvirt(lambda cmd: CP(code=1, err="")).status == checks.WARN


def test_memlock_check_fails_on_a_low_hard_limit(monkeypatch):
    import resource

    monkeypatch.setattr(resource, "getrlimit", lambda r: (100, 1000))
    c = checks.check_memlock()
    assert c.status == checks.FAIL and "LimitMEMLOCK" in c.fix


def test_mem_available_reads_the_given_meminfo():
    assert checks.mem_available("MemTotal: 10 kB\nMemAvailable: 2048 kB\n") == 2048 * 1024
    assert checks.mem_available("MemTotal: 10 kB\n") == 0  # no MemAvailable line: treated as none free


def test_diag_mask_hides_tokens_and_urls_credentials():
    fake = "gh" + "p_" + "abcdefghijklmnopqrstuvwx1234"  # assembled so the repo's secrets scan does not flag this file
    text = f"origin https://user:{fake}@github.com/x/y token=abc123def password: hunter2"
    out = diag.mask(text)
    assert fake not in out and "hunter2" not in out and "abc123def" not in out
    assert "https://***@" in out


def test_diag_bundle_notes_an_unreachable_governor(tmp_path, monkeypatch):
    def down(req, *a, **k):
        raise OSError("no socket")

    monkeypatch.setattr(diag.checks, "governor_request", down)
    monkeypatch.setattr(diag, "run", lambda *a, **k: CP(""), raising=False)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", ""))
    path = diag.write_bundle(tmp_path / "b.tar.gz")
    import tarfile
    with tarfile.open(path) as t:
        names = t.getnames()
        status = t.extractfile(next(n for n in names if n.endswith("governor_status.json"))).read().decode()
    assert "unreachable: no socket" in status
