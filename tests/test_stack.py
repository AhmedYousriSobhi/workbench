import subprocess
from pathlib import Path

import pytest
import yaml

from simctl import stack


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def test_stages_ordered():
    got = stack.stage_services(["cn001", "slurmrestd", "slurmctld", "grafana", "bmc-farm", "weird"])
    assert [g[0] for g in got] == ["observability", "slurm core", "slurm services", "mocks", "nodes", "other"]


def test_governor_admission_refusal_and_absent_governor():
    msgs = []
    assert not stack.governor_admits(500, msgs.append, ask=lambda r: {"ok": False, "error": "refused: L2"})
    assert "refused: L2" in msgs[0]
    assert stack.governor_admits(500, msgs.append, ask=lambda r: {"ok": True})

    def down(r):
        raise FileNotFoundError

    assert stack.governor_admits(500, msgs.append, ask=down) and msgs[-1].startswith("WARN")


def test_refused_when_it_does_not_fit():
    calls, msgs = [], []
    sim = yaml.safe_load(Path("sim.yaml").read_text())
    assert not stack.up(sim, "min", runner=lambda c: calls.append(c) or cp(), say=msgs.append, available=1 * 1024**3)
    assert not any("up" in c for c in calls) and msgs[0].startswith("REFUSED")  # only read-only `ps`


def test_up_starts_in_order_and_waits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import shutil
    root = Path(__file__).resolve().parents[1]
    for d in ("profiles", "components", "images"):
        shutil.copytree(root / d, tmp_path / d, dirs_exist_ok=True)
    sim = yaml.safe_load((root / "sim.yaml").read_text())
    started: list[str] = []
    built: list[str] = []

    def runner(cmd):
        if "build" in cmd:
            built.append(cmd[-1])
            started.append("<build>")
        if "up" in cmd:
            started.extend(cmd[cmd.index("--no-deps") + 1:])
        if "ps" in cmd:
            return cp(out="\n".join(started))
        return cp()

    monkeypatch.setattr(stack, "governor_admits", lambda n, say: True)
    monkeypatch.setattr(stack, "provision_token", lambda **kw: kw.get("path", tmp_path / "t"))
    assert stack.up(sim, "min", runner=runner, say=lambda m: None, available=12 * 1024**3, wait=1)
    assert started.index("prometheus") < started.index("slurmctld") < started.index("cn001")
    assert built == ["cn001"] and started.index("<build>") < started.index("prometheus")  # node-base before its dependants


def test_down_reports_failure():
    assert not stack.down(runner=lambda c: cp(1, err="boom"), say=lambda m: None)


def test_token_created_reused_and_replaced_when_stale(tmp_path):
    calls = []

    def http(method, path, body, url, auth):
        calls.append(path)
        return {"id": 7} if path.endswith("serviceaccounts") else {"key": f"glsa_{len(calls)}"}

    f = tmp_path / "s" / "tok"
    assert stack.provision_token(path=f, http=http, valid=lambda t, u: True) == f
    assert f.read_text() == "glsa_2" and oct(f.stat().st_mode)[-3:] == "600"
    stack.provision_token(path=f, http=http, valid=lambda t, u: True)
    assert len(calls) == 2  # valid token is reused
    stack.provision_token(path=f, http=http, valid=lambda t, u: False)  # new Grafana: stale token
    assert f.read_text() == "glsa_4" and oct(f.stat().st_mode)[-3:] == "600"


def test_token_waits_for_grafana(tmp_path):
    n = {"i": 0}

    def http(method, path, body, url, auth):
        n["i"] += 1
        if n["i"] < 3:
            raise ConnectionRefusedError
        return {"id": 1} if path.endswith("serviceaccounts") else {"key": "k"}

    stack.provision_token(path=tmp_path / "t", http=http, valid=lambda t, u: False, sleep=lambda s: None)
    assert (tmp_path / "t").read_text() == "k"


def test_unbuildable_services_are_skipped(tmp_path):
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "Dockerfile").write_text("FROM scratch")
    (tmp_path / "empty").mkdir()
    svcs = {"a": {"build": "ok"}, "b": {"build": "empty"}, "c": {"image": "x"}}
    assert stack.unbuildable(svcs, tmp_path) == ["b"]


def test_scale_down_removes_containers_by_label_not_compose_file(tmp_path, monkeypatch):
    import shutil
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    for d in ("profiles", "components", "images"):
        shutil.copytree(root / d, tmp_path / d, dirs_exist_ok=True)
    sim = yaml.safe_load((root / "sim.yaml").read_text())
    monkeypatch.setattr(stack, "governor_admits", lambda n, say: True)
    cmds: list[list[str]] = []

    def runner(cmd):
        cmds.append(cmd)
        if cmd[:2] == ["docker", "ps"] and "--format" in cmd:
            return cp(out="cn001\ncn002\ncn003\ncn004")
        if cmd[:2] == ["docker", "ps"]:  # -aq lookup by service label
            return cp(out="abc123")
        if "ps" in cmd:
            return cp(out="slurmctld\ncn001\ncn002")
        return cp()

    assert stack.scale(sim, "min", 2, runner=runner, say=lambda m: None)
    removed = [c for c in cmds if c[:3] == ["docker", "rm", "-f"]]
    assert len(removed) == 2  # cn003 and cn004


def test_up_on_a_running_stack_is_not_refused_for_low_memory(tmp_path, monkeypatch):
    """The running stack is already in "used" memory; counting its budget again refused a no-op."""
    monkeypatch.chdir(tmp_path)
    import shutil
    root = Path(__file__).resolve().parents[1]
    for d in ("profiles", "components", "images"):
        shutil.copytree(root / d, tmp_path / d, dirs_exist_ok=True)
    sim = yaml.safe_load((root / "sim.yaml").read_text())
    from simctl import compose
    every = "\n".join([*compose.generate(sim, compose.load_profile("min"))["services"],
                       "prometheus", "grafana", "gpu-exporter-farm"])
    said: list[str] = []

    def runner(cmd):
        return cp(out=every) if "ps" in cmd else cp()

    monkeypatch.setattr(stack, "governor_admits", lambda n, say: pytest.fail("nothing to admit"))
    monkeypatch.setattr(stack, "provision_token", lambda **kw: kw.get("path", tmp_path / "t"))
    assert stack.up(sim, "min", runner=runner, say=said.append, available=1 * 1024**3, wait=1)
    assert any("already running" in m for m in said)


def test_token_is_handed_back_to_the_checkout_owner_when_run_as_root(tmp_path, monkeypatch):
    """`sudo simctl up` must not leave a root-owned secret that breaks the next normal run."""
    import os
    gen = tmp_path / "generated"
    (gen / "secrets").mkdir(parents=True)
    chowned = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda p, u, g: chowned.append((Path(p).name, u)))
    http = lambda m, p, b, u, a: {"id": 1, "key": "k"}
    f = gen / "secrets" / "grafana_token"
    stack.provision_token(path=f, http=http, valid=lambda t, u: False)
    assert chowned == [("secrets", os.stat(gen).st_uid), ("grafana_token", os.stat(gen).st_uid)]


def _workdir(tmp_path, monkeypatch):
    import shutil
    monkeypatch.chdir(tmp_path)
    root = Path(__file__).resolve().parents[1]
    for d in ("profiles", "components", "images"):
        shutil.copytree(root / d, tmp_path / d, dirs_exist_ok=True)
    return yaml.safe_load((root / "sim.yaml").read_text())


def test_up_stops_at_the_first_stage_that_fails_to_start(tmp_path, monkeypatch):
    sim = _workdir(tmp_path, monkeypatch)
    msgs, ups = [], []

    def runner(cmd):
        if "up" in cmd:
            ups.append(cmd[cmd.index("--no-deps") + 1:])
            return cp(rc=1, err="no such image")
        return cp()

    monkeypatch.setattr(stack, "governor_admits", lambda n, say: True)
    assert not stack.up(sim, "min", runner=runner, say=msgs.append, available=12 * 1024**3, wait=1)
    assert len(ups) == 1  # later stages are never attempted
    assert any(m.startswith("FAILED observability") and "no such image" in m for m in msgs)


def test_up_fails_when_a_started_service_never_runs(tmp_path, monkeypatch):
    sim = _workdir(tmp_path, monkeypatch)
    msgs = []
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(stack.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(stack.time, "sleep", lambda s: None)
    monkeypatch.setattr(stack, "governor_admits", lambda n, say: True)
    runner = lambda cmd: cp(out="") if "ps" in cmd else cp()
    assert not stack.up(sim, "min", runner=runner, say=msgs.append, available=12 * 1024**3, wait=1)
    assert any("not running" in m for m in msgs)


def test_up_warns_but_still_succeeds_when_the_token_cannot_be_made(tmp_path, monkeypatch):
    sim = _workdir(tmp_path, monkeypatch)
    started, msgs = [], []

    def runner(cmd):
        if "up" in cmd:
            started.extend(cmd[cmd.index("--no-deps") + 1:])
        return cp(out="\n".join(started)) if "ps" in cmd else cp()

    def no_token(**kw):
        raise OSError("grafana down")

    monkeypatch.setattr(stack, "governor_admits", lambda n, say: True)
    monkeypatch.setattr(stack, "provision_token", no_token)
    assert stack.up(sim, "min", runner=runner, say=msgs.append, available=12 * 1024**3, wait=1)
    assert any(m.startswith("WARN no grafana token") for m in msgs) and msgs[-1] == "stack is up"


def test_current_profile_reads_the_file_or_falls_back(tmp_path, monkeypatch):
    f = tmp_path / "profile"
    monkeypatch.setattr(stack, "PROFILE_FILE", f)
    assert stack.current_profile() == "std" and stack.current_profile("min") == "min"
    f.write_text("full\n")
    assert stack.current_profile() == "full"


def test_scale_up_is_refused_when_the_governor_says_no(tmp_path, monkeypatch):
    sim = _workdir(tmp_path, monkeypatch)
    msgs, runs = [], []
    monkeypatch.setattr(stack, "governor_admits", lambda n, say: say("refused: L3") or False)
    monkeypatch.setattr(stack, "running_nodes", lambda runner=None: ["cn001"])
    assert not stack.scale(sim, "min", 4, runner=lambda c: runs.append(c) or cp(), say=msgs.append)
    assert "refused: L3" in msgs and not any("up" in c for c in runs)


def test_grafana_http_helpers_against_a_local_server():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(("GET", self.path, self.headers.get("Authorization")))
            self.send_response(200 if self.headers.get("Authorization") == "Bearer good" else 401)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(("POST", self.path, self.headers.get("Authorization"), body))
            data = json.dumps({"id": 7}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    try:
        assert stack.token_valid("good", url) is True
        assert stack.token_valid("bad", url) is False  # 401 is a refusal, not a crash
        out = stack._grafana("POST", "/api/serviceaccounts", {"name": "n"}, url, "admin:admin")
        assert out == {"id": 7}
        import base64
        assert seen[-1][2] == "Basic " + base64.b64encode(b"admin:admin").decode() and seen[-1][3] == {"name": "n"}
    finally:
        srv.shutdown()
    assert stack.token_valid("good", url) is False  # server gone: unreachable counts as invalid


def test_up_also_makes_a_read_only_grafana_token_for_clients(tmp_path, monkeypatch):
    sim = yaml.safe_load((Path(__file__).resolve().parent.parent / "sim.yaml").read_text())
    msgs, made = [], []

    def runner(cmd):
        return cp(out="") if "ps" in cmd else cp()

    monkeypatch.setattr(stack, "governor_admits", lambda n, say: True)
    monkeypatch.setattr(stack, "provision_token", lambda **kw: made.append(kw) or tmp_path / "t")
    monkeypatch.setattr(stack, "wait_running", lambda *a, **k: [])
    assert stack.up(sim, "min", runner=runner, say=msgs.append, available=12 * 1024**3, wait=1)
    viewer = [k for k in made if k.get("role") == "Viewer"]
    assert viewer and viewer[0]["path"] == stack.VIEWER_TOKEN_FILE and viewer[0]["account"] == "hpcsim-viewer"
    assert any("viewer token" in m and "uid: prometheus" in m for m in msgs)


def test_viewer_token_failure_only_warns(tmp_path, monkeypatch):
    sim = yaml.safe_load((Path(__file__).resolve().parent.parent / "sim.yaml").read_text())
    msgs = []

    def tokens(**kw):
        if kw.get("role") == "Viewer":
            raise OSError("nope")
        return tmp_path / "t"

    monkeypatch.setattr(stack, "governor_admits", lambda n, say: True)
    monkeypatch.setattr(stack, "provision_token", tokens)
    monkeypatch.setattr(stack, "wait_running", lambda *a, **k: [])
    assert stack.up(sim, "min", runner=lambda c: cp(out="") if "ps" in c else cp(), say=msgs.append,
                    available=12 * 1024**3, wait=1)
    assert any(m.startswith("WARN no grafana viewer token") for m in msgs)


def test_token_reuses_an_existing_service_account_after_a_lost_token_file(tmp_path):
    import urllib.error

    def http(method, path, body, url, auth):
        if method == "POST" and path.endswith("serviceaccounts"):
            raise urllib.error.HTTPError(url, 400, "already exists", {}, None)
        if method == "GET":
            return {"serviceAccounts": [{"id": 3, "name": "other"}, {"id": 9, "name": "hpcsim-viewer"}]}
        assert path == "/api/serviceaccounts/9/tokens"
        return {"key": "k"}

    stack.provision_token(path=tmp_path / "t", http=http, valid=lambda t, u: False, account="hpcsim-viewer", role="Viewer")
    assert (tmp_path / "t").read_text() == "k"


def test_token_gives_up_on_other_http_errors_and_unknown_accounts(tmp_path):
    import urllib.error

    def refused(method, path, body, url, auth):
        raise urllib.error.HTTPError(url, 401, "no", {}, None)

    with pytest.raises(urllib.error.HTTPError):
        stack.provision_token(path=tmp_path / "t", http=refused, valid=lambda t, u: False)

    def odd(method, path, body, url, auth):
        if method == "POST":
            raise urllib.error.HTTPError(url, 400, "bad", {}, None)
        return {"serviceAccounts": []}

    with pytest.raises(urllib.error.HTTPError):
        stack.provision_token(path=tmp_path / "t", http=odd, valid=lambda t, u: False)


def test_bmc_credentials_are_made_once(tmp_path):
    f = stack.ensure_bmc_credentials(tmp_path / "s" / "bmc_credentials")
    user, _, pw = f.read_text().strip().partition(":")
    assert user == "admin" and len(pw) >= 20 and (f.stat().st_mode & 0o777) == 0o644
    assert (f.parent.stat().st_mode & 0o777) == 0o700
    before = f.read_text()
    stack.ensure_bmc_credentials(f)
    assert f.read_text() == before


def test_ssh_keys_are_made_once_and_authorize_the_client_key(tmp_path):
    from subprocess import CompletedProcess

    calls = []

    def fake(cmd):
        calls.append(cmd)
        f = Path(cmd[-1])
        f.write_text("private")
        Path(str(f) + ".pub").write_text("ssh-ed25519 AAAA pub\n")
        return CompletedProcess(cmd, 0, "", "")

    d = stack.ensure_ssh_keys(tmp_path / "ssh", fake)
    # client and cluster key are both authorized (the cluster key is what a login-node user hops to a node with)
    assert len(calls) == 4 and (d / "authorized_keys").read_text() == "ssh-ed25519 AAAA pub\n" * 2
    stack.ensure_ssh_keys(d, fake)
    assert len(calls) == 4  # the host keys are stable across runs


def test_ssh_keygen_failure_is_reported(tmp_path):
    from subprocess import CompletedProcess

    with pytest.raises(OSError, match="ssh-keygen failed"):
        stack.ensure_ssh_keys(tmp_path / "s", lambda c: CompletedProcess(c, 1, "", "boom"))


def test_login_node_starts_after_the_nodes():
    labels = [label for label, _ in stack.stage_services(["login", "cn001", "slurmctld"])]
    assert labels.index("nodes") < labels.index("login node")


def test_client_key_install_remove(tmp_path):
    src = tmp_path / "ssh"
    src.mkdir()
    (src / "id_ed25519").write_text("KEY")
    dest = tmp_path / "home" / ".ssh" / "opti-sim_ed25519"
    assert stack.install_client_key(dest, src) == dest
    assert dest.read_text() == "KEY" and oct(dest.stat().st_mode & 0o777) == "0o600"
    assert stack.install_client_key(dest, src) == dest  # idempotent
    assert stack.remove_client_key(dest, src) and not dest.exists()
    assert not stack.remove_client_key(dest, src)


def test_client_key_refuses_foreign_file_and_missing_key(tmp_path):
    src = tmp_path / "ssh"
    src.mkdir()
    dest = tmp_path / "other"
    dest.write_text("mine")
    with pytest.raises(FileNotFoundError):
        stack.install_client_key(dest, src)
    (src / "id_ed25519").write_text("KEY")
    with pytest.raises(FileExistsError):
        stack.install_client_key(dest, src)
    assert not stack.remove_client_key(dest, src) and dest.read_text() == "mine"
