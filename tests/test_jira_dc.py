"""The real-Jira part (ADR 0026, issue 284): the setup flow against a fake Jira that speaks the wizard and the REST calls it
uses, the compose wiring, the by-name-only rule, and the localhost-only guard. No Docker and no licence needed."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs

import pytest
import yaml

from benchctl import compose, jira_real, workbench


class FakeJira(BaseHTTPRequestHandler):
    """Enough of Jira for `provision`: /status, the five wizard pages (token checked), users, project, tickets, tokens."""
    state = "FIRST_RUN"
    log: ClassVar[list] = []
    users: ClassVar[set] = set()
    tickets = 0
    tokens = 0

    def log_message(self, *a):
        pass

    def _send(self, code, body="", headers=None):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        cls = type(self)
        if self.path == "/status":
            return self._send(200, {"state": cls.state})
        if self.path.startswith("/secure/Setup"):
            return self._send(200, '<input type="hidden" name="atl_token" value="tok-1">')
        if self.path == "/rest/api/2/myself":
            return self._send(200, {"name": "admin"}) if self.headers.get("Authorization") else self._send(401)
        if self.path == "/rest/pat/latest/tokens":
            return self._send(200, [])
        self._send(404)

    def do_POST(self):
        cls = type(self)
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if self.path.startswith("/secure/Setup"):
            form = {k: v[0] for k, v in parse_qs(raw).items()}
            if form.get("atl_token") != "tok-1":
                return self._send(400, "no token")
            cls.log.append((self.path, form))
            if self.path == "/secure/SetupMailNotifications.jspa":
                cls.state = "RUNNING"
            return self._send(302, "", {"Location": "/next"})
        body = json.loads(raw) if raw else {}
        if self.path == "/rest/api/2/group":
            return self._send(201, {})
        if self.path == "/rest/api/2/user":
            if body["name"] in cls.users:
                return self._send(400, "A user with that username already exists.")
            cls.users.add(body["name"])
            return self._send(201, {})
        if self.path.startswith("/rest/api/2/group/user"):
            return self._send(201, {})
        if self.path == "/rest/api/2/project":
            return self._send(201, {})
        if self.path == "/rest/api/2/search":
            return self._send(200, {"total": cls.tickets})
        if self.path == "/rest/api/2/issue":
            cls.tickets += 1
            return self._send(201, {})
        if self.path == "/rest/pat/latest/tokens":
            cls.tokens += 1
            return self._send(201, {"rawToken": f"raw-{cls.tokens}"})
        self._send(404)


@pytest.fixture
def jira(tmp_path):
    FakeJira.state, FakeJira.log, FakeJira.users, FakeJira.tickets, FakeJira.tokens = "FIRST_RUN", [], set(), 0, 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeJira)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env, db = tmp_path / "jira-real.env", tmp_path / "jira-db.env"
    jira_real.ensure_secrets(db, env)
    yield f"http://127.0.0.1:{srv.server_address[1]}", env, db
    srv.shutdown()


def run(jira, say=lambda m: None):
    base, env, db = jira
    return jira_real.provision(base, "LICENCE-TEXT", say, lambda s: None, env, db)


def test_provision_walks_the_wizard_in_order_then_seeds_and_writes_tokens(jira):
    tokens = run(jira)
    steps = [p for p, _ in FakeJira.log]
    assert steps == ["/secure/SetupDatabase.jspa", "/secure/SetupApplicationProperties.jspa", "/secure/SetupLicense.jspa",
                     "/secure/SetupAdminAccount.jspa", "/secure/SetupMailNotifications.jspa"]
    forms = dict(FakeJira.log)
    assert forms["/secure/SetupLicense.jspa"]["setupLicenseKey"] == "LICENCE-TEXT"
    assert forms["/secure/SetupDatabase.jspa"]["jdbcHostname"] == "jira-db" and forms["/secure/SetupDatabase.jspa"]["databaseType"] == "postgres72"
    assert FakeJira.users == {n for n, _ in jira_real.PEOPLE} and FakeJira.tickets == jira_real.TICKETS
    assert set(tokens) == {"admin", "alice"}
    env = jira[1].read_text()
    assert "PAT_ADMIN=raw-1" in env and "PAT_ALICE=raw-2" in env and f"JIRA_URL={jira[0]}" in env


def test_provision_twice_skips_the_wizard_and_adds_nothing(jira):
    run(jira)
    FakeJira.log.clear()
    run(jira)
    assert FakeJira.log == [] and FakeJira.tickets == jira_real.TICKETS and len(FakeJira.users) == len(jira_real.PEOPLE)
    assert jira[1].read_text().count("PAT_ADMIN=") == 1  # the tokens are replaced, not appended


def test_nothing_secret_is_printed(jira):
    said = []
    run(jira, said.append)
    text = "\n".join(said)
    env = jira_real.read_env(jira[1])
    assert not any(v in text for v in (*env.values(), "LICENCE-TEXT", jira_real.read_env(jira[2])["POSTGRES_PASSWORD"]))
    assert "tokens are in" in text


def test_a_jira_that_is_not_on_this_machine_is_refused():
    for bad in ("https://example.atlassian.net", "http://10.0.0.5:8080", "http://jira.example.com"):
        with pytest.raises(jira_real.JiraRealError, match="localhost"):
            jira_real.Session(bad)
    assert jira_real.check_local("http://127.0.0.1:8083/") == "http://127.0.0.1:8083"


def test_the_passwords_are_random_private_and_made_once(tmp_path):
    db, env = tmp_path / "d.env", tmp_path / "e.env"
    jira_real.ensure_secrets(db, env)
    first = (db.read_text(), env.read_text())
    jira_real.ensure_secrets(db, env)
    assert (db.read_text(), env.read_text()) == first
    assert oct(env.stat().st_mode & 0o777) == "0o600" and re.search(r"POSTGRES_PASSWORD=\S{20,}", first[0])
    assert first[0] != first[1]


def test_a_missing_licence_says_where_to_put_it(tmp_path, monkeypatch):
    monkeypatch.delenv(jira_real.LICENSE_VAR, raising=False)
    with pytest.raises(jira_real.JiraRealError, match="3-hour Data Center test licence"):
        jira_real.read_license(tmp_path / "none")
    (tmp_path / "k").write_text("  KEY \n")
    assert jira_real.read_license(tmp_path / "k") == "KEY"


def test_reset_removes_containers_and_volumes_and_forgets_tokens_but_keeps_passwords(tmp_path):
    env = tmp_path / "e.env"
    env.write_text("JIRA_ADMIN_PASSWORD=pw\nJIRA_URL=http://127.0.0.1:8083\nPAT_ALICE=t\n")
    calls = []
    jira_real.reset(lambda cmd, **k: calls.append(cmd), env)
    assert ["docker", "rm", "-f", "workbench-jira-real-1", "workbench-jira-db-1"] in calls
    assert any(c[:4] == ["docker", "volume", "rm", "-f"] and "workbench_jira-home" in c and "workbench_jira-db" in c for c in calls)
    assert env.read_text() == "JIRA_ADMIN_PASSWORD=pw\n"


def _services(parts):
    sim = yaml.safe_load(Path("sim.yaml").read_text())
    spec = workbench.defaults(sim)
    spec.parts, spec.cpu_nodes, spec.gpu_nodes = parts, 1, 0
    s, p = workbench.effective(sim, spec)
    return compose.generate(s, p), s, p


def test_the_part_adds_two_localhost_unprivileged_limited_containers():
    data, _, _ = _services(["jira", "jira-real"])
    real, db = data["services"]["jira-real"], data["services"]["jira-db"]
    assert real["ports"] == ["127.0.0.1:8083:8080"] and "ports" not in db
    assert real["image"].startswith("atlassian/jira-software:10.") and ":latest" not in real["image"]
    for svc in (real, db):
        assert svc["labels"]["workbench.class"] == "P6" and svc["mem_limit"].endswith("m")
        assert "cap_add" not in svc and "privileged" not in svc and "ipv4_address" in svc["networks"]["default"]
    assert "--lc-collate=C" in db["environment"]["POSTGRES_INITDB_ARGS"]
    assert set(data["volumes"]) == set(jira_real.VOLUMES)
    assert "jira-mock" in data["services"]  # next to the mock, not instead of it


def test_without_the_part_nothing_changes():
    data, _, _ = _services(["jira"])
    assert "jira-real" not in data["services"] and "jira-db" not in data["services"] and "volumes" not in data


def test_the_part_is_in_the_estimate_and_never_turned_on_by_all_or_addons():
    _, sim, prof = _services(["jira", "jira-real"])
    assert workbench.estimate(sim, prof)["services_mb"]["jira-real"] == jira_real.LIMIT_MB
    assert "jira-real" not in workbench.expand({}, addons=True)
    assert "jira-real" not in workbench.expand({"ticketing": ["all"]})
    assert workbench.expand({"ticketing": ["jira-real"]}) == ["jira-real"]


def test_refresh_licence_restarts_jira_and_reports_the_new_end(tmp_path, monkeypatch):
    """The test key counts from the start of Jira: the refresh is a restart, then wait until the licence reads again (no Docker)."""
    import subprocess
    import time

    from benchctl import jira_real as jr

    calls, lines = [], []
    seen = iter([{"valid": True, "expires": int(time.time()) + 600}, None, {"valid": True, "expires": int(time.time()) + 10800}])

    def runner(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "running", "")

    monkeypatch.setattr(jr, "licence_status", lambda *a, **k: next(seen))
    monkeypatch.setattr(jr, "wait_for", lambda *a, **k: "RUNNING")
    now = jr.refresh_licence(say=lines.append, runner=runner, sleep=lambda s: None, env=tmp_path / "e")
    assert ["docker", "restart", jr.CONTAINER] in calls and now["valid"] and now["expires"] > time.time() + 10000
    assert any("valid until" in m for m in lines) and any("restarting" in m for m in lines)


def test_refresh_licence_says_what_to_do_when_not_created_or_still_expired(tmp_path, monkeypatch):
    import subprocess
    import time

    from benchctl import jira_real as jr

    with pytest.raises(jr.JiraRealError, match="not created yet"):
        jr.refresh_licence(say=lambda m: None, runner=lambda c, **k: subprocess.CompletedProcess(c, 1, "", ""), env=tmp_path / "e")
    monkeypatch.setattr(jr, "licence_status", lambda *a, **k: {"valid": False, "expires": int(time.time()) - 5})
    monkeypatch.setattr(jr, "wait_for", lambda *a, **k: "RUNNING")
    with pytest.raises(jr.JiraRealError, match="fixed end date"):
        jr.refresh_licence(say=lambda m: None, runner=lambda c, **k: subprocess.CompletedProcess(c, 0, "", ""), sleep=lambda s: None, env=tmp_path / "e")
    with pytest.raises(jr.JiraRealError, match="restart"):
        jr.refresh_licence(say=lambda m: None, runner=lambda c, **k: subprocess.CompletedProcess(c, 0, "", "boom") if c[1] == "inspect" else subprocess.CompletedProcess(c, 1, "", "boom"),
                           sleep=lambda s: None, env=tmp_path / "e")


def test_licence_status_reads_the_expiry_and_hides_failures(tmp_path):
    from benchctl import jira_real as jr

    assert jr.licence_status(env=tmp_path / "missing") is None  # no password file: nothing to ask with


def test_licence_status_asks_the_plugin_manager_with_a_wildcard_accept(tmp_path, monkeypatch):
    from benchctl import jira_real as jr

    env = tmp_path / "e"
    env.write_text("JIRA_ADMIN_PASSWORD=pw\n")
    seen = {}

    def api(self, method, path, body=None, auth=None, timeout=60, accept="application/json"):
        seen.update(path=path, accept=accept, auth=auth)
        return 200, {"valid": True, "expired": False, "expiryDate": 1793893363000}, ""

    monkeypatch.setattr(jr.Session, "api", api)
    assert jr.licence_status(env=env) == {"valid": True, "expires": 1793893363}
    assert seen["accept"] == "*/*" and seen["path"] == jr.LICENSE_API and seen["auth"] == ("admin", "pw")
