"""The page plays a scenario's Kubernetes steps through the governor, which has the Docker the page lacks (issue 386, SPEC W-3)."""
import json
import threading
import time

import uvicorn
from test_webui import ROOT, STATUS, TOKEN, free_port, no_stack, req

from benchctl import scenario
from governor import control
from webui import app as webapp
from webui.app import create_app

SCENARIO = ("name: kube\ndescription: d\nsteps:\n"
            "  - {at: 0s, target: k8s, do: {down: [kcn001]}}\n"
            "  - {at: 0s, target: k8s, do: {pod: {namespace: team-a, name: crash-1, fault: crashloop}}}\n"
            "  - {at: 0s, target: k8s, do: {apply: [fixtures/x.yaml]}}\n")


def play(tmp_path, monkeypatch, built: bool, answer: dict):
    asked = []

    def handle(cmd, request):
        if cmd == "k8s-step":
            asked.append(request["do"])
            return answer
        return STATUS

    sock = str(tmp_path / "g.sock")
    gov = control.serve(sock, handle, lambda: {"compute"})  # the real socket server and its validation
    (tmp_path / "kube.yaml").write_text(SCENARIO)
    monkeypatch.setattr(webapp, "k8s_built", lambda: built)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(TOKEN, sock, tmp_path, ROOT / "sim.yaml", fetch=no_stack, poster=no_stack),
                                           host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    base, h = f"http://127.0.0.1:{port}", {"X-Requested-With": "workbench"}
    try:
        assert req(base, "/api/scenarios/kube/run", "POST", headers=h)[0] == 200
        state, deadline = {}, time.time() + 10
        while time.time() < deadline:
            state = json.loads(req(base, "/api/scenarios/run")[1])
            if not state["running"]:
                break
            time.sleep(0.05)
        return asked, state
    finally:
        server.should_exit = True
        gov.shutdown()


def test_the_page_plays_node_and_pod_steps_through_the_governor_and_marks_the_rest_cli_only(tmp_path, monkeypatch):
    asked, state = play(tmp_path, monkeypatch, True, {"ok": True, "started": True})
    assert sorted(map(json.dumps, asked)) == sorted(map(json.dumps, [{"down": ["kcn001"]}, {"pod": {"namespace": "team-a", "name": "crash-1", "fault": "crashloop"}}]))
    assert [s["status"] for s in state["steps"]] == ["ok", "ok", "skipped"]
    assert "host CLI" in state["steps"][2]["detail"]  # `apply` is not one of the governor's keys


def test_with_the_part_off_one_message_and_the_governor_is_not_asked(tmp_path, monkeypatch):
    asked, state = play(tmp_path, monkeypatch, False, {"ok": True, "started": True})
    assert asked == [] and [s["status"] for s in state["steps"]] == ["skipped"] * 3
    assert "Kubernetes part is not built" in state["steps"][0]["detail"]


def test_a_refusal_at_a_busy_level_is_skipped_with_the_governors_words(tmp_path, monkeypatch):
    asked, state = play(tmp_path, monkeypatch, True, {"ok": False, "error": "refused at L3: stack changes are paused (GV-8)"})
    assert len(asked) == 2 and state["steps"][0]["status"] == "skipped" and "refused at L3" in state["steps"][0]["detail"]


def test_the_page_supports_only_the_keys_the_governor_validates():
    from governor import control as c
    assert set(scenario.K8S_PAGE_KEYS) == {k for k in (*c.K8S_NODE_KEYS, "pod", "delete_pod", "scale")}
    assert scenario.page_support(scenario.Step(0, "k8s", {"down": ["kcn001"]})) == ""
    assert "host CLI" in scenario.page_support(scenario.Step(0, "k8s", {"drain": {"node": "kcn001"}}))
    assert "host CLI" in scenario.page_support(scenario.Step(0, "k8s", {"down": ["kcn001"], "apply": ["x"]}))
