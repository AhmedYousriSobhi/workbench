"""The catalogue of Kubernetes reads a monitoring client makes (issue 762): read-only, complete, and in step with the guide."""
import json
from pathlib import Path

import yaml

from benchctl import k8s, k8sreads

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "docs/guides/client-testing.md").read_text()


def test_every_read_is_read_only_and_keys_are_unique():
    assert len({r.key for r in k8sreads.READS}) == len(k8sreads.READS)
    for r in k8sreads.READS:
        assert any(tuple(r.args[: len(v)]) == v for v in k8sreads.READ_VERBS), r
        assert not {"create", "delete", "apply", "patch", "edit", "exec", "port-forward", "proxy", "replace", "scale", "drain", "cordon"} & set(r.args), r
        assert r.viewer and r.limited and all(o in {"yes", "no", "ok", "forbidden", "not-found", "served", "not-served"} for o in r.viewer + r.limited), r


def test_the_issue_762_reads_are_all_there():
    keys = set(k8sreads.BY_KEY)
    assert {"can-i-list-nodes", "can-i-list-pods-all", "can-i-list-events-all", "can-i-list-pods-ns", "can-i-list-pods-kubeconfig-ns", "config-view", "readyz",
            "version", "nodes", "metrics-probe", "pods-all", "pods-count", "events-warning", "api-group-kueue", "api-group-volcano", "api-group-kubeflow",
            "can-i-list-deployments", "can-i-get-deployments", "get-jobs", "get-pvcs", "get-daemonsets", "get-resourcequotas", "get-hpas",
            "get-clusterqueues", "can-i-get-pod-logs", "can-i-list-namespaces"} <= keys
    assert k8sreads.BY_KEY["can-i-list-nodes"].limited == ("no",) and k8sreads.BY_KEY["can-i-list-pods-ns"].limited == ("yes",)
    assert k8sreads.BY_KEY["metrics-probe"].viewer == k8sreads.BY_KEY["node-usage"].viewer == ("ok",)  # metrics-server (#382)
    for key in ("pod-logs", "pod-logs-previous"):  # #760: the viewer reads logs; the limited user has no pods/log, even in team-a
        assert k8sreads.BY_KEY[key].viewer == ("ok",) and k8sreads.BY_KEY[key].limited == ("forbidden",)
        assert {"--tail=500", "--timestamps"} <= set(k8sreads.BY_KEY[key].args) and any(a.startswith("--limit-bytes=") for a in k8sreads.BY_KEY[key].args)
    assert "--previous" in k8sreads.BY_KEY["pod-logs-previous"].args and k8sreads.BY_KEY["pod-logs"].args[3] == k8sreads.LIMITED_NS
    logs = "2026-11-17T09:35:47.552910347Z failing\n"
    assert k8sreads.shape_problems(k8sreads.BY_KEY["pod-logs-previous"], "viewer", logs, "v1") == []
    assert k8sreads.shape_problems(k8sreads.BY_KEY["pod-logs"], "viewer", "no timestamp\n", "v1") == ["not log lines that each start with a timestamp"]
    assert k8sreads.shape_problems(k8sreads.BY_KEY["pod-logs-previous"], "viewer", "2026-11-17T09:41:02Z other\n", "v1") == ["not the previous run's output"]
    assert k8sreads.BY_KEY["metrics-probe"].limited == k8sreads.BY_KEY["node-usage"].limited == ("forbidden",)  # no metrics.k8s.io rule
    ok_usage = '{"items": [{"metadata": {"name": "kcn001"}, "usage": {"cpu": "350m", "memory": "200Mi"}}]}'
    assert k8sreads.shape_problems(k8sreads.BY_KEY["node-usage"], "viewer", ok_usage, "v1") == []
    assert k8sreads.shape_problems(k8sreads.BY_KEY["node-usage"], "viewer", '{"items": []}', "v1")
    assert k8sreads.BY_KEY["api-group-volcano"].viewer == k8sreads.BY_KEY["api-group-kubeflow"].viewer == ("not-served",)


def test_the_viewer_role_has_no_kind_without_a_read_or_a_stated_reason():
    rules = yaml.safe_load((ROOT / "k8s/fixtures/40-rbac-cluster-viewer.yaml").read_text().split("\n---")[0])["rules"]
    kinds = {res for r in rules for res in r["resources"] if "/" not in res}
    listed = " ".join(" ".join(r.args) for r in k8sreads.READS)
    unread = {k for k in kinds if k not in listed and k not in k8sreads.NOT_READ_BY_CLIENT}
    assert not unread, f"add a read for {sorted(unread)} or name it in k8sreads.NOT_READ_BY_CLIENT"


def test_the_limited_users_yes_answers_are_things_its_role_grants():
    role = next(d for d in yaml.safe_load_all((ROOT / "k8s/fixtures/30-rbac-viewer.yaml").read_text()) if d["kind"] == "Role")
    assert set(role["rules"][0]["resources"]) == {"pods", "events"} and k8sreads.LIMITED_NS == "team-a"
    for r in k8sreads.READS:
        if r.limited == ("yes",) and r.args[:2] == ("auth", "can-i"):
            assert any(x in r.args for x in ("pods", "events", "localqueues.kueue.x-k8s.io")), r


def test_classify_reads_the_outcome_from_the_exit_code_and_the_text():
    by = k8sreads.BY_KEY
    assert k8sreads.classify(by["can-i-list-nodes"], 1, "no\n", "") == "no"
    assert k8sreads.classify(by["can-i-list-nodes"], 0, "yes\n", "") == "yes"
    assert k8sreads.classify(by["can-i-list-nodes"], 1, "", "boom") == "error"
    assert k8sreads.classify(by["nodes"], 0, "{}", "") == "ok"
    assert k8sreads.classify(by["nodes"], 1, "", 'Error from server (Forbidden): nodes is forbidden: User "limited" cannot list resource "nodes"') == "forbidden"
    assert k8sreads.classify(by["metrics-probe"], 1, "", "Error from server (NotFound): the server could not find the requested resource") == "not-found"
    assert k8sreads.classify(by["nodes"], 1, "", "Unable to connect to the server") == "error"
    assert k8sreads.classify(by["api-group-kueue"], 0, "clusterqueues\nlocalqueues\n", "") == "served"
    assert k8sreads.classify(by["api-group-volcano"], 0, "\n", "No resources found") == "not-served"
    assert k8sreads.classify(by["api-group-volcano"], 1, "", "x") == "not-served"


def _node(name, kv, gpus=None, pool="gpu"):
    lab = {"pool": pool}
    cap = {"cpu": "8"}
    if gpus:
        lab.update({"nvidia.com/gpu.present": "true", "nvidia.com/gpu.count": str(gpus), "nvidia.com/gpu.product": "NVIDIA-H200",
                    "nvidia.com/mig.strategy": "none", "nvidia.com/cuda.driver.major": "550"})
        cap["nvidia.com/gpu"] = str(gpus)
    return {"metadata": {"name": name, "labels": lab}, "status": {"capacity": cap, "allocatable": dict(cap), "conditions": [{"type": "Ready", "status": "True"}],
                                                                  "nodeInfo": {"kubeletVersion": kv}}}


def test_the_node_shape_check_names_a_kubelet_version_a_client_cannot_parse_and_a_gpu_mismatch():
    nodes = k8sreads.BY_KEY["nodes"]
    good = json.dumps({"items": [_node("kgpu001", "v1.36.1", 8), _node("kcn001", "v1.36.1", pool="cpu")]})
    assert k8sreads.shape_problems(nodes, "viewer", good, "v1.36.1") == []
    bad = json.dumps({"items": [_node("kgpu001", "kwok-v0.8.0", 8)]})
    assert "kwok-v0.8.0" in k8sreads.shape_problems(nodes, "viewer", bad, "v1.36.1")[0]
    odd = _node("kgpu001", "v1.36.1", 8)
    odd["status"]["allocatable"]["nvidia.com/gpu"] = "0"
    assert "do not agree" in k8sreads.shape_problems(nodes, "viewer", json.dumps({"items": [odd]}), "v1.36.1")[0]
    assert k8sreads.shape_problems(nodes, "viewer", "{}", "v1.36.1") == ["no nodes"]


def test_the_other_shape_checks():
    by, sp = k8sreads.BY_KEY, k8sreads.shape_problems
    assert sp(by["readyz"], "viewer", "[+]ping ok\nreadyz check passed\n", "v") == [] and sp(by["readyz"], "viewer", "x", "v")
    assert sp(by["version"], "viewer", '{"gitVersion":"v1.36.1"}', "v1.36.1") == [] and sp(by["version"], "viewer", '{"gitVersion":"v1.35.0"}', "v1.36.1")
    assert sp(by["pods-count"], "viewer", '{"metadata":{"remainingItemCount":14},"items":[{}]}', "v") == []
    assert sp(by["pods-count"], "viewer", '{"metadata":{},"items":[{}]}', "v")
    assert sp(by["pods-all"], "viewer", '{"items":[]}', "v") and sp(by["pods-all"], "viewer", '{"items":[{"a":1}]}', "v") == []
    assert sp(by["events-warning"], "viewer", '{"items":[{"type":"Normal"}]}', "v") and sp(by["events-warning"], "viewer", '{"items":[]}', "v") == []
    cfg = {"current-context": "c", "contexts": [{"context": {"cluster": "k", "namespace": "team-a"}}], "users": [{"user": {"token": "REDACTED"}}]}
    assert sp(by["config-view"], "limited", json.dumps(cfg), "v") == []
    cfg["contexts"][0]["context"]["namespace"] = "default"
    assert "limited namespace" in sp(by["config-view"], "limited", json.dumps(cfg), "v")[0]
    cfg["users"][0]["user"]["token"] = "abc"
    assert any("token" in p for p in sp(by["config-view"], "viewer", json.dumps(cfg), "v"))
    assert sp(by["get-jobs"], "viewer", '{"items":[]}', "v") == [] and sp(by["get-jobs"], "viewer", "x", "v")
    assert sp(by["describe-pod"], "viewer", "Name: sized\n", "v") == [] and sp(by["describe-pod"], "viewer", "x", "v")


def test_the_guide_lists_every_read_and_its_table_is_the_generated_one():
    assert k8sreads.render_table() in GUIDE, "regenerate the table: python -c 'from benchctl import k8sreads; print(k8sreads.render_table())'"
    for r in k8sreads.READS:
        assert f"`{r.key}`" in GUIDE


def test_the_nodes_report_the_api_servers_version_in_the_applied_manifest():
    sim = {"kubernetes": {"version": "v1.35.2"}}
    assert k8s.node_manifest("kcn001", {}, k8s.version(sim))["status"]["nodeInfo"]["kubeletVersion"] == "v1.35.2"
