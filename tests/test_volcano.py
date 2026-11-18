"""Volcano fixtures without a controller (issue 765): objects, Queue status calls and the scenario step."""
import json

import yaml

from benchctl import k8s, volcano


def test_the_crds_are_the_two_pinned_v1beta1_definitions_and_only_queue_has_a_status_subresource():
    crds = {d["metadata"]["name"]: d for d in yaml.safe_load_all((volcano.SRC / "crds.yaml").read_text()) if d}
    assert set(crds) == set(volcano.CRDS)
    assert all([v["name"] for v in c["spec"]["versions"]] == ["v1beta1"] for c in crds.values())
    assert "status" in crds["queues.scheduling.volcano.sh"]["spec"]["versions"][0]["subresources"]
    assert not crds["podgroups.scheduling.volcano.sh"]["spec"]["versions"][0]["subresources"]


def test_the_objects_have_one_open_and_one_closed_queue_and_a_running_and_a_pending_podgroup_in_team_a():
    objs = volcano.objects()
    assert {o["metadata"]["name"] for o in objs if o["kind"] == "Queue"} == set(volcano.QUEUE_STATUS)
    phases = {o["metadata"]["name"]: o["status"]["phase"] for o in objs if o["kind"] == "PodGroup"}
    assert phases == {"train-run": "Running", "train-wait": "Pending"} and {o["spec"]["queue"] for o in objs if o["kind"] == "PodGroup"} == {"gpu-batch"}
    assert [json.loads(c[-1])["status"]["state"] for c in volcano.status_calls()] == ["Open", "Closed"]
    assert all("--subresource=status" in c for c in volcano.status_calls())


def test_a_step_closes_a_queue_through_the_status_subresource_and_moves_a_podgroup_with_a_plain_patch():
    q = volcano.calls({"queue": "gpu-batch", "state": "Closed"})[0]
    assert "--subresource=status" in q and json.loads(q[-1]) == {"status": {"state": "Closed"}}
    p = volcano.calls({"podgroup": "train-run", "namespace": "team-a", "phase": "Pending"})[0]
    assert "--subresource=status" not in p and p[:2] == ["-n", "team-a"] and json.loads(p[-1]) == {"status": {"phase": "Pending"}}
    assert k8s.step_args({"volcano": {"queue": "gpu-batch", "state": "Open"}})[0][:3] == ["patch", volcano.QUEUE, "gpu-batch"]


def test_a_bad_volcano_step_says_what_is_allowed():
    assert k8s.step_problems({"volcano": {"queue": "nope", "state": "Open"}}, set())[0].startswith("k8s volcano queue is one of: gpu-batch")
    assert "state is Open or Closed" in k8s.step_problems({"volcano": {"queue": "gpu-batch", "state": "x"}}, set())[0]
    assert "podgroup is one of: team-a/train-run" in k8s.step_problems({"volcano": {"podgroup": "x", "namespace": "team-a", "phase": "Pending"}}, set())[0]
    assert "phase is Pending or Running" in k8s.step_problems({"volcano": {"podgroup": "train-run", "namespace": "team-a", "phase": "x"}}, set())[0]
    assert k8s.step_problems({"volcano": {"queue": "gpu-batch", "podgroup": "x"}}, set())[0].startswith("k8s volcano is {queue")
    assert k8s.step_problems({"volcano": {"queue": "gpu-batch", "state": "Closed"}}, set()) == []
