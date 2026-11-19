"""Teleport's Kubernetes counters and recent kube.request events on the page (issue 755, ADR 0084 W3)."""
import json

from webui import telekube

SCRAPE = """# HELP teleport_kubernetes_server_api_requests_total x
# TYPE teleport_kubernetes_server_api_requests_total counter
teleport_kubernetes_server_api_requests_total{kind="pods"} 7
teleport_kubernetes_server_api_requests_total{kind="nodes"} 5
teleport_kubernetes_server_exec_sessions_total 0
teleport_connect_to_node_attempts_total 9
"""


def line(user, verb, code, name="kcn001", t="2026-11-19T10:00:00Z", **extra):
    return json.dumps({"event": "kube.request", "time": t, "user": user, "verb": verb, "response_code": code, "resource_kind": "nodes",
                       "resource_name": name, **extra})


def log(tmp_path, *lines, name="2026-11-19.10:00:00.log"):
    d = tmp_path / "log"
    d.mkdir(exist_ok=True)
    (d / name).write_text("\n".join(lines) + "\n")
    return d


def test_a_counter_is_summed_and_absent_means_not_measured_not_zero():
    assert telekube.counter(SCRAPE, telekube.COUNTERS["requests"]) == 12
    assert telekube.counter(SCRAPE, telekube.COUNTERS["exec"]) == 0       # published, and zero
    assert telekube.counter(SCRAPE, telekube.COUNTERS["portforward"]) is None  # never published: not measured


def test_recent_events_are_newest_first_and_carry_user_verb_resource_and_code(tmp_path):
    d = log(tmp_path, line("alice", "GET", 200, t="2026-11-19T10:00:01Z"), json.dumps({"event": "session.start", "user": "x"}),
            line("bob", "DELETE", 403, name="p1", resource_namespace="team-a", t="2026-11-19T10:00:02Z"), "{broken")
    got = telekube.recent_events(d)
    assert [e["user"] for e in got] == ["bob", "alice"]
    assert got[0] == {"time": "2026-11-19T10:00:02Z", "user": "bob", "verb": "DELETE", "resource": "nodes/p1 in team-a", "code": 403}


def test_only_the_last_n_events_and_the_newest_files_are_read(tmp_path):
    d = log(tmp_path, *[line("u", "GET", 200, name=f"n{i}") for i in range(30)])
    got = telekube.recent_events(d, n=10)
    assert len(got) == 10 and got[0]["resource"] == "nodes/n29"
    assert telekube.recent_events(tmp_path / "nothing") == []   # no folder: no events, no error


def test_a_big_file_is_read_from_its_end_without_a_half_line(tmp_path, monkeypatch):
    monkeypatch.setattr(telekube, "TAIL_BYTES", 700)
    d = log(tmp_path, *[line("u", "GET", 200, name=f"n{i}") for i in range(30)])
    got = telekube.recent_events(d, n=3)
    assert [e["resource"] for e in got] == ["nodes/n29", "nodes/n28", "nodes/n27"]


def test_hidden_without_both_parts_one_notice_when_teleport_is_down_data_otherwise(tmp_path):
    d = log(tmp_path, line("alice", "GET", 200))
    assert telekube.collect(False, lambda u: SCRAPE, "http://x", d) == {"shown": False}

    def down(url):
        raise OSError("refused")
    off = telekube.collect(True, down, "http://x", d)
    assert off["shown"] and "not answering" in off["notice"] and telekube.RECOVERY in off["notice"] and off["events"] == [] and "sudo" not in off["notice"]
    on = telekube.collect(True, lambda u: SCRAPE, "http://x", d)
    assert on["notice"] is None and on["counters"]["requests"] == 12 and on["counters"]["portforward"] is None and on["events"][0]["user"] == "alice"


def test_the_real_layout_day_files_under_a_cluster_folder_and_a_dangling_events_link(tmp_path):
    """Teleport writes log/<cluster id>/DAY.log and log/events.log, a link to a container path that does not exist on the host (found live)."""
    sub = tmp_path / "349e9ecb-123c"
    sub.mkdir()
    (sub / "2026-11-19.00:00:00.log").write_text(line("alice", "GET", 200, name="n1") + "\n")
    (tmp_path / "events.log").symlink_to("/var/lib/teleport/log/349e9ecb-123c/2026-11-19.00:00:00.log")
    assert [e["resource"] for e in telekube.recent_events(tmp_path)] == ["nodes/n1"]
