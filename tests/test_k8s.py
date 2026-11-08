"""The Kubernetes part (ADR 0034, issue 232): nodes from sim.yaml, the compose wiring, the PKI, the bootstrap order, the scenario steps."""
import re
import ssl
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from benchctl import addressing, compose, k8s, scenario, stack, workbench

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def ok(out=""):
    return SimpleNamespace(returncode=0, stdout=out, stderr="")


def bad(err="boom"):
    return SimpleNamespace(returncode=1, stdout="", stderr=err)


def test_nodes_come_from_sim_yaml_and_never_take_a_slurm_name():
    names = [n for n, _ in k8s.node_list(SIM)]
    assert names == ["kcn001", "kcn002", "kgpu001", "kgpu002"] and k8s.problems(SIM) == []
    assert [n for n, _ in k8s.node_list(SIM, 5)][-1] == "kgpu005"  # --k8s-nodes counts the GPU nodes only
    clash = {**SIM, "kubernetes": {"nodes": [{"prefix": "cn", "count": 1}]}}
    assert "name of a Slurm node" in k8s.problems(clash)[0]
    odd = {**SIM, "kubernetes": {"nodes": [{"prefix": "kx", "count": 1, "gpus": 1, "gpu_type": "nope"}]}}
    assert "unknown gpu_type" in k8s.problems(odd)[0]


def test_a_gpu_node_advertises_what_gpu_feature_discovery_and_the_device_plugin_do():
    gpu = k8s.node_manifest("kgpu001", k8s.node_groups(SIM)[1])
    lab, st = gpu["metadata"]["labels"], gpu["status"]
    assert lab["nvidia.com/gpu.product"] == "NVIDIA-H200" and lab["nvidia.com/gpu.count"] == "8" and lab["nvidia.com/gpu.present"] == "true"
    assert st["capacity"]["nvidia.com/gpu"] == st["allocatable"]["nvidia.com/gpu"] == "8"
    assert gpu["metadata"]["annotations"]["kwok.x-k8s.io/node"] == "fake" and "taints" not in gpu.get("spec", {})
    cpu = k8s.node_manifest("kcn001", k8s.node_groups(SIM)[0])
    assert "nvidia.com/gpu" not in cpu["status"]["capacity"] and "nvidia.com/gpu.present" not in cpu["metadata"]["labels"]
    assert len(list(yaml.safe_load_all(k8s.nodes_yaml(SIM)))) == 4


def test_the_part_is_off_by_default_and_the_five_services_are_limited_unprivileged_and_addressed():
    assert "k8s" not in workbench.DEFAULT_PARTS and workbench.PARTS["k8s"] == ("k8s", None)
    assert "k8s-apiserver" not in compose.generate(SIM, {"groups": ["core"]})["services"]
    svcs = compose.generate(SIM, {"groups": ["core", "k8s"]})["services"]
    ips = addressing.plan(SIM)["services"]
    for name in k8s.SERVICES:
        s = svcs[name]
        assert s["mem_limit"] == f"{k8s.LIMITS[name]}m" and s["labels"]["workbench.class"] == "P3" and s["cgroup_parent"] == "workbench-p3.slice"
        assert s["networks"]["default"]["ipv4_address"] == ips[name] and not s.get("privileged") and "cap_add" not in s
        assert re.fullmatch(r"\d+:\d+", s["user"]) and s["image"].startswith("registry.k8s.io/") and "build" not in s
    assert svcs["k8s-apiserver"]["ports"] == ["127.0.0.1:6443:6443"]  # localhost only
    assert svcs["k8s-etcd"]["image"] == k8s.ETCD_IMAGE and svcs["kwok-controller"]["entrypoint"] == ["kwok"]
    assert "--enable-crds=Stage" in svcs["kwok-controller"]["command"]  # without it KWOK exits ("no stages found")
    assert "--manage-nodes-with-annotation-selector=kwok.x-k8s.io/node=fake" in svcs["kwok-controller"]["command"]  # a real node may join later
    assert any("--token-auth-file" in a for a in svcs["k8s-apiserver"]["command"])
    assert len(set(ips.values())) == len(ips)  # no two services share an address


def test_the_version_comes_from_sim_yaml_and_the_login_image_uses_the_same_kubectl():
    sim = {**SIM, "kubernetes": {"version": "v1.35.2"}}
    assert k8s.images(sim)["k8s-apiserver"].endswith(":v1.35.2") and k8s.kubectl_image(sim).endswith(":v1.35.2")
    dockerfile = (ROOT / "images/login/Dockerfile").read_text()
    assert f"FROM {k8s.kubectl_image(SIM)} AS kubectl" in dockerfile  # keep the tag equal to k8s.VERSION


def test_login_nodes_get_the_read_only_kubeconfigs_only_when_the_part_is_on():
    on = compose.generate(SIM, {"groups": ["core", "access", "k8s"]})["services"]["login"]["volumes"]
    assert any("viewer.kubeconfig" in v for v in on) and any("limited.kubeconfig" in v for v in on)
    assert not any("kubeconfig" in v for v in compose.generate(SIM, {"groups": ["core", "access"]})["services"]["login"]["volumes"])
    assert "admin" not in " ".join(on)  # the administrator's credentials never reach a login node
    sh = (ROOT / "images/login/login.sh").read_text()
    assert "/home/${u%%:*}/.kube/config" in sh and "install -m 600" in sh  # mode 600, owned by the user, as a client checks


def test_prepare_writes_a_pki_that_python_verifies_kubeconfigs_and_keeps_them(tmp_path):
    ips = addressing.plan(SIM)
    k8s.prepare(SIM, ips, tmp_path / "s", tmp_path / "st")
    d = tmp_path / "s"
    assert all((d / f).stat().st_mode & 0o777 == 0o600 for f in ("ca.key", "admin.key", "viewer.kubeconfig", "tokens.csv"))
    ctx = ssl.create_default_context(cafile=str(d / "ca.crt"))  # Python 3.13+ verifies strictly: the CA needs key usage extensions
    ctx.load_cert_chain(d / "admin.crt", d / "admin.key")
    viewer = yaml.safe_load((d / "viewer.kubeconfig").read_text())
    assert viewer["clusters"][0]["cluster"]["server"] == f"https://{k8s.api_ip(ips)}:6443" and "certificate-authority-data" in viewer["clusters"][0]["cluster"]
    assert "client-key" not in viewer["users"][0]["user"] and viewer["users"][0]["user"]["token"] in (d / "tokens.csv").read_text()
    limited = yaml.safe_load((d / "limited.kubeconfig").read_text())
    assert limited["contexts"][0]["context"]["namespace"] == "team-a"
    assert yaml.safe_load((d / "admin.kubeconfig").read_text())["clusters"][0]["cluster"]["server"] == "https://127.0.0.1:6443"
    cert = subprocess.run(["openssl", "x509", "-in", d / "apiserver.crt", "-noout", "-ext", "subjectAltName"], capture_output=True, text=True, check=True).stdout
    assert k8s.api_ip(ips) in cert and "127.0.0.1" in cert and "k8s-apiserver" in cert
    assert (tmp_path / "st" / "etcd").is_dir() and (tmp_path / "st" / "nodes.yaml").read_text().count("kind: Node") == 4
    token = (d / "tokens.csv").read_text()
    k8s.prepare(SIM, ips, d, tmp_path / "st")
    assert (d / "tokens.csv").read_text() == token  # kept: a rebuild does not invalidate the client's kubeconfig
    k8s.prepare(SIM, {**ips, "services": {**ips["services"], k8s.API: "10.141.255.99"}}, d, tmp_path / "st")
    assert (d / "tokens.csv").read_text() != token  # a new address needs a new certificate


def test_prepare_says_why_openssl_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(k8s.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="no such option"))
    with pytest.raises(k8s.K8sError, match="openssl ecparam failed: no such option"):
        k8s.prepare(SIM, addressing.plan(SIM), tmp_path / "s", tmp_path / "st")


def test_bootstrap_applies_definitions_then_stages_then_nodes_then_fixtures_and_waits_for_service_accounts():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[cmd.index(f"--kubeconfig={k8s.CONTAINER_PKI}/component.kubeconfig") + 1:])
        return ok()

    assert k8s.bootstrap(SIM, addressing.plan(SIM), lambda m: None, runner=run, ready=lambda ips: True, sleep=lambda s: None)
    files = [c[-1] for c in calls if c[0] == "apply"]
    assert files == ["/k8s/kwok/crds.yaml", "/k8s/kwok/stage-fast.yaml", "/k8s/kwok/stage-faults.yaml", "/state/nodes.yaml",
                     "/k8s/fixtures/00-namespaces.yaml", "/k8s/fixtures"]
    assert any(c[0] == "wait" and "crd/stages.kwok.x-k8s.io" in c for c in calls)  # stages cannot be applied before their definition exists
    first_sa = next(i for i, c in enumerate(calls) if "serviceaccount" in c)
    assert first_sa < len(calls) - 1 and calls[-1][-1] == "/k8s/fixtures"  # pods are refused until the default service account exists


def test_bootstrap_stops_with_one_message_where_it_failed():
    said = []
    assert not k8s.bootstrap(SIM, addressing.plan(SIM), said.append, runner=lambda c, **k: ok(), ready=lambda ips: False)
    assert said == ["FAILED kubernetes: the API server did not become ready (docker logs workbench-k8s-apiserver-1)"]
    said.clear()
    n = {"i": 0}

    def run(cmd, **kw):
        n["i"] += 1
        return bad("nope") if "stage-faults.yaml" in cmd[-1] else ok()

    assert not k8s.bootstrap(SIM, addressing.plan(SIM), said.append, runner=run, ready=lambda ips: True, sleep=lambda s: None)
    assert said[0].startswith("FAILED kubernetes fault stages: nope")
    said.clear()
    assert not k8s.bootstrap(SIM, addressing.plan(SIM), said.append, ready=lambda ips: True, sleep=lambda s: None,
                             runner=lambda c, **k: bad("not established") if "wait" in c else ok())
    assert "stage definition did not become ready" in said[0]
    said.clear()
    assert not k8s.bootstrap(SIM, addressing.plan(SIM), said.append, ready=lambda ips: True, sleep=lambda s: None,
                             runner=lambda c, **k: bad("denied") if c[-1] == "/k8s/fixtures" else ok())
    assert said[0].startswith("FAILED kubernetes fixtures: denied")


def test_api_ready_gives_up_when_nothing_answers(tmp_path, monkeypatch):
    ips = addressing.plan(SIM)
    k8s.prepare(SIM, ips, tmp_path / "s", tmp_path / "st")
    monkeypatch.setattr(k8s, "SECRETS_DIR", tmp_path / "s")
    monkeypatch.setattr(k8s.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    assert k8s.api_ready(ips, timeout=0.05, sleep=lambda s: None) is False

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(k8s.urllib.request, "urlopen", lambda *a, **k: Resp())
    assert k8s.api_ready(ips, timeout=5) is True


def test_kubectl_runs_from_the_official_image_on_the_cluster_network():
    seen = []
    k8s.kubectl(SIM, "get", "nodes", runner=lambda c, **kw: seen.append(c) or ok(), mounts=[(Path("k8s"), "/k8s")])
    cmd = seen[0]
    assert cmd[:4] == ["docker", "run", "--rm", "--network"] and cmd[4] == "workbench_default" and k8s.kubectl_image(SIM) in cmd
    assert "--memory" in cmd and cmd[-2:] == ["get", "nodes"] and any(a.endswith(":/k8s:ro") for a in cmd)
    seen.clear()
    k8s.kubectl(SIM, "apply", "-f", "-", runner=lambda c, **kw: seen.append((c, kw)) or ok(), stdin="x: 1")
    assert "-i" in seen[0][0] and seen[0][1] == {"input": "x: 1"}


def test_faults_are_stages_a_kubelet_would_report_and_the_fixtures_ask_for_them():
    stages = list(yaml.safe_load_all((ROOT / "k8s/kwok/stage-faults.yaml").read_text()))
    assert {s["metadata"]["name"] for s in stages} == {*(f"fault-{f}" for f in k8s.FAULTS), "fault-node-down"}
    assert all(s["spec"]["selector"]["matchLabels"]["workbench_fault"] in (*k8s.FAULTS, "down") and s["spec"]["weight"] > 0 for s in stages)
    fast = yaml.safe_load_all((ROOT / "k8s/kwok/stage-fast.yaml").read_text())
    skips = {s["metadata"]["name"] for s in fast if any(e["key"] == ".metadata.labels.workbench_fault" for e in s["spec"]["selector"].get("matchExpressions", []))}
    assert skips == {"node-initialize", "pod-ready"}  # the defaults must not undo a fault
    fixtures = (ROOT / "k8s/fixtures/10-workloads.yaml").read_text() + (ROOT / "k8s/fixtures/20-signals.yaml").read_text()
    for fault in ("crashloop", "oom", "imagepull", "jobfail"):
        assert f"workbench_fault: {fault}" in fixtures
    assert next(yaml.safe_load_all((ROOT / "k8s/fixtures/40-rbac-cluster-viewer.yaml").read_text()))["rules"][0]["verbs"] == ["get", "list", "watch"]


def test_step_problems_name_each_mistake():
    nodes = {n for n, _ in k8s.node_list(SIM)}
    assert k8s.step_problems({"down": ["kgpu001"], "cordon": ["kcn001"]}, nodes) == []
    assert k8s.step_problems({}, nodes) == ["k8s step does nothing"]
    text = " | ".join(k8s.step_problems({"down": ["gpu001"], "bogus": 1, "pod": {"namespace": "Bad_NS", "name": "x", "fault": "melt", "gpus": -1},
                                         "scale": {"namespace": "a", "deploy": "web", "replicas": "2"}, "delete_pod": {"namespace": "a"},
                                         "apply": ["/etc/passwd", "../x"]}, nodes))
    for part in ("unknown Kubernetes node 'gpu001'", "unknown k8s key 'bogus'", "namespace and a name", "fault must be one of", "gpus must be",
                 "replicas: a whole number", "delete_pod needs", "must be relative"):
        assert part in text


def test_steps_become_kubectl_calls_and_a_pod_step_writes_its_manifest(tmp_path):
    assert k8s.step_args({"down": ["kgpu001"]}) == [["label", "node", "kgpu001", "workbench_fault=down", "--overwrite"]]  # the fault-node-down stage
    assert k8s.step_args({"up": ["kgpu001"]}) == [["label", "node", "kgpu001", "workbench_fault-"]]
    assert k8s.step_args({"cordon": ["kcn001"], "uncordon": ["kcn002"]}) == [["cordon", "kcn001"], ["uncordon", "kcn002"]]
    assert k8s.step_args({"scale": {"namespace": "team-a", "deploy": "web", "replicas": 0}}) == [["-n", "team-a", "scale", "deployment/web", "--replicas=0"]]
    assert k8s.step_args({"delete_pod": {"namespace": "team-b", "name": "oom"}})[0][-3:] == ["pod", "oom", "--wait=false"]
    assert k8s.step_args({"apply": ["fixtures/10-workloads.yaml"]}) == [["apply", "--server-side", "--force-conflicts", "-f", "/k8s/fixtures/10-workloads.yaml"]]
    do = {"pod": {"namespace": "team-b", "name": "demo", "fault": "oom", "gpus": 2}}
    assert k8s.step_args(do) == [["apply", "-f", "/state/scenario-pod.yaml"]]
    k8s.write_pod(do, tmp_path)
    pod = yaml.safe_load((tmp_path / "scenario-pod.yaml").read_text())
    assert pod["metadata"]["labels"]["workbench_fault"] == "oom" and pod["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"] == "2"
    assert yaml.safe_load(k8s.pod_manifest({"namespace": "a", "name": "j", "fault": "jobfail"}))["spec"]["restartPolicy"] == "Never"
    assert "workbench_fault" not in yaml.safe_load(k8s.pod_manifest({"namespace": "a", "name": "p"}))["metadata"]["labels"]
    k8s.write_pod({"down": ["x"]}, tmp_path / "none")
    assert not (tmp_path / "none").exists()


def test_scenario_validates_runs_and_judges_k8s_steps():
    sc = {"steps": [{"at": "0s", "target": "k8s", "do": {"down": ["kgpu001"]}}, {"at": "1s", "target": "k8s", "do": {"down": ["nope"]}}]}
    assert scenario.validate(sc, SIM) == ["step 2: unknown Kubernetes node 'nope'"]
    assert all(not scenario.validate(s, SIM) for s in scenario.list_all(ROOT / "scenarios") if s["name"] in ("k8s-node-notready", "pod-crashloop"))
    seen = []
    out = scenario.execute(scenario.Step(0, "k8s", {"cordon": ["kcn001"]}), runner=lambda c: seen.append(c) or ok())
    assert out.status == "ok" and seen[0][-2:] == ["cordon", "kcn001"] and seen[0][0] == "docker"
    assert scenario.execute(scenario.Step(0, "k8s", {"cordon": ["kcn001"]}), runner=lambda c: bad("denied")).status == "failed"
    assert scenario.execute(scenario.Step(0, "k8s", {"cordon": ["kcn001"]}), host=False).status == "skipped"  # the page has no Docker

    label, holds, _ = scenario.expectation({"k8s.node_ready": {"node": "kgpu001", "ready": False}}, lambda c: ok("Unknown"))
    assert label.startswith("k8s.node_ready") and holds() is True
    assert scenario.expectation({"k8s.node_ready": {"node": "kgpu001"}}, lambda c: ok("True"))[1]() is True
    assert scenario.expectation({"k8s.node_ready": {"node": "kgpu001"}}, lambda c: bad())[1]() is False  # unreadable is not ready
    _, holds, _ = scenario.expectation({"k8s.pod_status": {"namespace": "team-b", "name": "oom", "reason": "OOMKilled"}},
                                       lambda c: ok("Running  CrashLoopBackOff Error OOMKilled"))
    assert holds() is True
    assert scenario.expectation({"k8s.pod_status": {"namespace": "a", "name": "b", "reason": "OOMKilled"}}, lambda c: bad())[1]() is False
    assert scenario.expectation({"k8s.node_ready": {"node": "x"}}, lambda c: ok(), host=False)[1] is None


def test_kubernetes_starts_after_the_services_it_needs_and_kwok_starts_last_of_its_own():
    stages = dict(stack.stage_services(["slurmctld", "k8s-etcd", "k8s-apiserver", "kwok-controller", "cn001", "login"]))
    order = [label for label, _ in stack.stage_services(["slurmctld", "k8s-etcd", "k8s-apiserver", "kwok-controller", "cn001", "login"])]
    assert stages["kubernetes"] == ["k8s-etcd", "k8s-apiserver"] and stages["kubernetes nodes"] == ["kwok-controller"]
    assert order.index("kubernetes") < order.index("kubernetes nodes") < order.index("nodes")


def test_the_selection_turns_the_part_on_and_counts_the_gpu_nodes():
    assert workbench.expand({"workload": ["kubernetes"]}) == ["k8s"]
    spec = workbench.Spec(parts=[*workbench.DEFAULT_PARTS, "k8s"], k8s_nodes=5)
    assert spec.problems() == []
    sim, prof = workbench.effective(SIM, spec)
    assert "k8s" in prof["groups"] and [n for n, _ in k8s.node_list(sim)][-1] == "kgpu005"
    assert any("needs the Kubernetes part" in p for p in workbench.Spec(k8s_nodes=2).problems())
    on = workbench.estimate(*workbench.effective(SIM, workbench.Spec(parts=[*workbench.DEFAULT_PARTS, "k8s"])), app_mb=0)
    off = workbench.estimate(*workbench.effective(SIM, workbench.Spec()), app_mb=0)
    assert on["total_mb"] - off["total_mb"] == sum(k8s.LIMITS.values())  # the five services' limits, nothing else


def test_k8s_cpu_and_gpu_node_counts_are_separate_and_none_keeps_sim_yaml():
    assert [n for n, _ in k8s.node_list(SIM, None, 3)][:4] == ["kcn001", "kcn002", "kcn003", "kgpu001"]
    s = workbench.Spec(parts=[*workbench.DEFAULT_PARTS, "k8s"], k8s_nodes=0, k8s_cpu_nodes=5)
    sim, _ = workbench.effective(SIM, s)
    names = [n for n, _ in k8s.node_list(sim)]
    assert names == [f"kcn00{i}" for i in range(1, 6)]                      # 0 GPU nodes is a real choice now, not "default"
    assert [n for n, _ in k8s.node_list(workbench.effective(SIM, workbench.Spec(parts=["k8s"]))[0])] == ["kcn001", "kcn002", "kgpu001", "kgpu002"]
    assert any("--k8s-cpu-nodes needs" in p for p in workbench.Spec(k8s_cpu_nodes=2).problems())
    assert any("k8s_nodes must be 0 to 16" in p for p in workbench.Spec(parts=["k8s"], k8s_nodes=99).problems())
    old = workbench.from_dict({"k8s_nodes": 0}, workbench.Spec())      # a file saved before the CPU count existed: 0 meant "sim.yaml's count"
    assert old.k8s_nodes is None and workbench.from_dict({"k8s_nodes": 0, "k8s_cpu_nodes": 1}, workbench.Spec()).k8s_nodes == 0


def test_the_governor_and_the_plan_carry_the_kubernetes_counts():
    from governor import control

    base = {"cpu_nodes": 2, "gpu_nodes": 0, "gpus_per_node": 8, "parts": ["k8s"]}
    assert control.plan_error({**base, "k8s_nodes": 3, "k8s_cpu_nodes": 0}) is None
    for bad in ({"k8s_nodes": 99}, {"k8s_cpu_nodes": -1}, {"k8s_nodes": "2"}, {"k8s_nodes": True}):
        assert "k8s_" in control.plan_error({**base, **bad}), bad
