"""SSH traffic on the architecture diagram (issue 213, ADR 0088): the in-container counter, the flow feed's use of it, the compose wiring."""
import importlib.util
import io
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ssh_counter", ROOT / "images/login/ssh_counter.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

ACCEPT = "Accepted publickey for alice from {ip} port 5{n} ssh2: ED25519 SHA256:abc"
CERT = "Accepted publickey for alice from 10.141.255.20 port 41000 ssh2: ED25519-CERT SHA256:def"


@pytest.fixture(autouse=True)
def fresh():
    sc.STATE.update({"requests": 0, "via_ca": 0, "by_source": {}})


def test_counts_accepted_key_logins_per_source_and_nothing_else():
    for line in (ACCEPT.format(ip="172.18.0.1", n=1), ACCEPT.format(ip="172.18.0.1", n=2), ACCEPT.format(ip="10.141.255.5", n=3),
                 "Connection closed by 172.18.0.1 port 5 [preauth]",  # a liveness probe: connects, never authenticates
                 "Failed publickey for alice from 172.18.0.1 port 9 ssh2: ED25519 SHA256:x",
                 "Invalid user bob from 172.18.0.1 port 10", "Server listening on 0.0.0.0 port 22."):
        sc.feed(line)
    s = sc.snapshot()
    assert s["requests"] == 3 and s["by_source"] == {"172.18.0.1": 2, "10.141.255.5": 1} and "errors" not in s


def test_a_teleport_login_is_kept_apart_and_no_user_or_command_is_kept():
    sc.feed(CERT)
    s = sc.snapshot()
    assert s["requests"] == 0 and s["via_ca"] == 1 and s["by_source"] == {}
    sc.feed(ACCEPT.format(ip="172.18.0.1", n=1))
    assert "alice" not in json.dumps(sc.snapshot())


def test_the_source_table_is_bounded():
    for i in range(sc.MAX_SOURCES + 20):
        sc.feed(ACCEPT.format(ip=f"10.0.{i // 250}.{i % 250}", n=1))
    s = sc.snapshot()
    assert len(s["by_source"]) == sc.MAX_SOURCES and s["requests"] == sc.MAX_SOURCES + 20


def test_stats_endpoint_serves_the_snapshot():
    srv = sc.ThreadingHTTPServer(("127.0.0.1", 0), sc.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        sc.feed(ACCEPT.format(ip="172.18.0.1", n=1))
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        got = json.load(urllib.request.urlopen(base + "/sim/stats"))
        assert got["requests"] == 1 and got["by_source"] == {"172.18.0.1": 1}
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/other")
    finally:
        srv.shutdown()


def test_follow_starts_at_the_end_counts_new_lines_and_repeats_them(tmp_path):
    log = tmp_path / "sshd.log"
    log.write_text(ACCEPT.format(ip="172.18.0.1", n=1) + "\n")  # from before the counter started: not counted
    out, n = io.StringIO(), [0]

    def stop():
        n[0] += 1
        if n[0] == 2:
            with log.open("a") as f:
                f.write(ACCEPT.format(ip="172.18.0.1", n=2) + "\nhalf a li")
        if n[0] == 3:
            with log.open("a") as f:
                f.write("ne\n")
        return n[0] > 5

    sc.follow(str(log), out=out, stop=stop, wait=0)
    assert sc.snapshot()["requests"] == 1
    assert out.getvalue().splitlines() == [ACCEPT.format(ip="172.18.0.1", n=2), "half a line"]


def test_follow_truncates_a_log_that_grew_too_large(tmp_path, monkeypatch):
    log = tmp_path / "sshd.log"
    log.write_text("")
    monkeypatch.setattr(sc, "MAX_LOG", 10)
    n = [0]

    def stop():
        n[0] += 1
        if n[0] == 1:
            log.write_text("x" * 50)
        return n[0] > 4

    sc.follow(str(log), out=io.StringIO(), stop=stop, wait=0)
    assert log.stat().st_size == 0


def test_the_file_made_after_start_is_read_from_its_top(tmp_path):
    log = tmp_path / "late.log"
    n = [0]

    def stop():
        n[0] += 1
        if n[0] == 2:
            log.write_text(ACCEPT.format(ip="172.18.0.1", n=1) + "\n")
        return n[0] > 4

    sc.follow(str(log), out=io.StringIO(), stop=stop, wait=0)
    assert sc.snapshot()["requests"] == 1


# --- the flow feed ---------------------------------------------------------------------------------------------------

JUMP = "10.141.255.5"


class Counters:
    """Stand-ins for the SSH counters: one `{requests, by_source}` document per URL."""

    def __init__(self):
        self.docs = {"http://c/login": {"requests": 0, "by_source": {}}, "http://c/jump": {"requests": 0, "by_source": {}}}
        self.down = set()

    def add(self, url, ip, n=1):
        d = self.docs[url]
        d["requests"] += n
        d["by_source"][ip] = d["by_source"].get(ip, 0) + n

    def __call__(self, url, *a):
        if url in self.down or url not in self.docs:
            raise OSError("down")
        return json.loads(json.dumps(self.docs[url]))


def make(c, **kw):
    import webui.flow as wf

    t = [0.0]
    f = wf.Flow(fetch=c, clock=lambda: t[0], min_interval=0, ssh=lambda: {"ssh:login": "http://c/login", "ssh:jump": "http://c/jump"}, jump_ip=JUMP, **kw)
    f.tick = lambda dt=1.0: t.__setitem__(0, t[0] + dt)
    return f


@pytest.fixture
def no_mocks(monkeypatch):
    import webui.flow as wf

    monkeypatch.setattr(wf, "STATS", {})
    monkeypatch.setattr(wf, "COUNTERS", {})
    monkeypatch.setattr(wf, "REACH_ONLY", {})


def test_a_client_login_is_a_dot_on_the_direct_line_and_the_first_poll_only_baselines(no_mocks):
    c = Counters()
    c.add("http://c/login", "172.18.0.1", 4)  # from before the page looked
    f = make(c)
    f.poll()
    assert f.since(0)["events"] == []
    f.tick()
    c.add("http://c/login", "172.18.0.1", 2)
    f.poll()
    snap = f.since(0)
    assert snap["events"] == [{"id": 1, "from": "clients", "to": "ssh:login", "n": 2, "label": "connections", "nodes": []}]
    assert snap["totals"]["ssh:login"] == 2 and snap["reachable"]["ssh:login"] is True and snap["reachable"]["ssh:jump"] is True
    assert "ssh:login" not in snap["errors"]  # not measured: the counter publishes no error count


def test_the_jump_hosts_hop_to_a_login_node_is_its_own_line_not_a_client(no_mocks):
    c = Counters()
    f = make(c)
    f.poll()
    f.tick()
    c.add("http://c/login", JUMP, 1)  # `ssh -J`: the login node sees the jump host
    c.add("http://c/jump", "172.18.0.1", 1)  # and the jump host saw the client
    c.add("http://c/login", "172.18.0.1", 1)  # a direct client at the same time
    f.poll()
    ev = {(e["from"], e["to"]): e["n"] for e in f.since(0)["events"]}
    assert ev == {("jump", "hop:login"): 1, ("clients", "ssh:login"): 1, ("clients", "ssh:jump"): 1}
    assert f.since(0)["totals"]["hop:login"] == 1


def test_no_counter_means_no_dots_and_a_restart_makes_none(no_mocks):
    c = Counters()
    f = make(c)
    f.poll()
    c.down.add("http://c/login")
    f.tick()
    f.poll()
    s = f.since(0)
    assert s["events"] == [] and s["reachable"]["ssh:login"] is False and s["reachable"]["ssh:jump"] is True
    c.down.clear()
    c.docs["http://c/login"] = {"requests": 3, "by_source": {"172.18.0.1": 3}}  # came back with another count: baseline, no dots
    f.tick(20)
    f.poll()
    assert f.since(0)["events"] == []
    f.tick()
    c.add("http://c/login", "172.18.0.1", 1)
    f.poll()
    assert [e["n"] for e in f.since(0)["events"]] == [1]


def test_a_login_that_leaves_the_selection_leaves_the_feed(no_mocks):
    import webui.flow as wf

    c = Counters()
    present = {"ssh:login": "http://c/login"}
    f = wf.Flow(fetch=c, clock=lambda: 0.0, min_interval=0, ssh=lambda: present, jump_ip=JUMP)
    f.poll()
    assert "ssh:login" in f.since(0)["totals"]
    present.clear()
    f.poll()
    assert not [k for k in f.since(0)["totals"] if k.startswith("ssh:")]


def test_no_ssh_source_changes_nothing_for_the_other_counters(no_mocks):
    import webui.flow as wf

    f = wf.Flow(fetch=lambda *a: {"requests": 0}, clock=lambda: 0.0, min_interval=0)
    f.poll()
    assert f.since(0)["totals"] == {} and f.since(0)["events"] == []


def test_ssh_sources_come_from_the_saved_selection(monkeypatch):
    import webui.cluster as cl
    import webui.flow as wf
    from benchctl import compose, controller

    monkeypatch.setattr(cl, "saved_logins", lambda sim=None: [{"name": "login", "port": 2222}, {"name": "login2", "port": None}, {"name": "login3", "port": 2224}])
    monkeypatch.setattr(cl, "saved_has_part", lambda part: part == "jump")
    got = wf.ssh_sources()
    assert got == {"ssh:login": "http://127.0.0.1:2322/sim/stats", "ssh:login3": "http://127.0.0.1:2324/sim/stats",
                   "ssh:jump": f"http://127.0.0.1:{controller.JUMP_PORT + compose.COUNTER_OFFSET}/sim/stats"}
    monkeypatch.setattr(cl, "saved_has_part", lambda part: False)
    assert "ssh:jump" not in wf.ssh_sources()


# --- compose and the port plan -----------------------------------------------------------------------------------------

def sim_logins(*modes):
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    sim["login"] = list(modes)
    return sim


def test_every_login_and_the_jump_host_publish_a_counter_port_on_localhost_only():
    from benchctl import compose, controller

    prof = {"groups": ["core", "access", "jump", "teleport"], "compute_nodes": 1}
    data = compose.generate(sim_logins("ssh", "teleport", "ssh"), prof)
    svc = data["services"]
    assert svc["login"]["ports"] == ["127.0.0.1:2222:22", "127.0.0.1:2322:9922"]
    assert svc["login3"]["ports"] == ["127.0.0.1:2224:22", "127.0.0.1:2324:9922"]  # login2 is Teleport only: no port at all
    assert not svc["login2"].get("ports")
    assert svc["jump"]["ports"] == [f"127.0.0.1:{controller.JUMP_PORT}:22", f"127.0.0.1:{controller.JUMP_PORT + 100}:9922"]
    for name in ("login", "login2", "login3", "jump"):
        assert compose.COUNTER_MOUNT in svc[name]["volumes"]
        assert "cap_add" not in svc[name] and svc[name]["mem_limit"]
    every = [p.split(":")[1] for s in svc.values() for p in s.get("ports", [])]
    assert len(every) == len(set(every))  # no host port twice


def test_the_counter_ports_sit_clear_of_every_other_published_port():
    from benchctl import compose, controller, logins

    block = {compose.SSH_PORT + compose.COUNTER_OFFSET + i for i in range(logins.MAX_LOGINS)} | {controller.JUMP_PORT + compose.COUNTER_OFFSET}
    assert min(block) == 2320 and max(block) == 2329
    assert not block & ({compose.SSH_PORT + i for i in range(logins.MAX_LOGINS)} | {controller.SSH_PORT, controller.JUMP_PORT})


def test_the_images_start_the_counter_and_log_sshd_to_the_file_it_reads():
    for f in ("images/login/login.sh", "images/jump/entrypoint.sh"):
        text = (ROOT / f).read_text()
        assert "ssh-counter 9922 /var/log/sshd.log" in text and "exec /usr/sbin/sshd -D -E /var/log/sshd.log" in text
    docker = (ROOT / "images/jump/Dockerfile").read_text()
    assert docker.count("apt-get install") == 1 and "python3" in docker  # still one apt layer
