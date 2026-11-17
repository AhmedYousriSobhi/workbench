"""GPU shapes and kubelet skew as scenario steps (issue 766)."""
import json

from benchctl import k8s, k8sgpu

NODES = {"kgpu001", "kgpu002"}


def body(call):
    return json.loads(call[call.index("-p") + 1])["status"]


def test_unadvertised_sets_capacity_and_allocatable_to_zero_and_back_to_the_declared_count():
    off = k8sgpu.gpu_calls({"node": "kgpu001", "count": 8, "advertised": False})[0]
    assert "--subresource=status" in off and body(off) == {"capacity": {"nvidia.com/gpu": "0"}, "allocatable": {"nvidia.com/gpu": "0"}}
    assert body(k8sgpu.gpu_calls({"node": "kgpu001", "count": 8, "advertised": True})[0])["allocatable"] == {"nvidia.com/gpu": "8"}


def test_mig_single_overrides_product_and_count_and_serves_the_devices_as_gpus():
    label, status = k8sgpu.gpu_calls({"node": "kgpu001", "count": 8, "mig": {"strategy": "single", "profile": "1g.18gb", "devices": 7}})
    assert label[:4] == ["label", "node", "kgpu001", "nvidia.com/mig.strategy=single"] and "nvidia.com/gpu.product=NVIDIA-H200-MIG-1g.18gb" in label
    assert "nvidia.com/gpu.count=7" in label and body(status)["allocatable"]["nvidia.com/gpu"] == "7"


def test_mig_mixed_keeps_one_fewer_whole_gpu_and_adds_the_profile_resource_and_count_label():
    label, status = k8sgpu.gpu_calls({"node": "kgpu001", "count": 8, "mig": {"strategy": "mixed", "profile": "1g.18gb", "devices": 7}})
    assert "nvidia.com/mig.strategy=mixed" in label and "nvidia.com/mig-1g.18gb.count=7" in label
    assert body(status)["allocatable"] == {"nvidia.com/gpu": "7", "nvidia.com/mig-1g.18gb": "7"}


def test_mig_none_restores_labels_drops_the_profile_label_and_resource():
    label, drop, status = k8sgpu.gpu_calls({"node": "kgpu001", "count": 8, "mig": {"strategy": "none", "profile": "1g.18gb"}})
    assert "nvidia.com/mig.strategy=none" in label and "nvidia.com/gpu.product=NVIDIA-H200" in label and "nvidia.com/gpu.count=8" in label
    assert drop[-1] == "nvidia.com/mig-1g.18gb.count-" and body(status)["allocatable"] == {"nvidia.com/gpu": "8", "nvidia.com/mig-1g.18gb": None}


def test_the_skewed_kubelet_is_one_minor_newer_or_four_minors_older_and_same_is_unchanged():
    assert k8sgpu.skewed("v1.36.1", "newer") == "v1.37.0" and k8sgpu.skewed("v1.36.1", "older") == "v1.32.0" and k8sgpu.skewed("v1.36.1", "same") == "v1.36.1"
    assert k8sgpu.skewed("kwok", "newer") == "kwok" and k8sgpu.skewed("v1.2.0", "older") == "v1.0.0"
    assert body(k8sgpu.kubelet_calls({"node": "kgpu001", "skew": "older"}, "v1.36.1")[0]) == {"nodeInfo": {"kubeletVersion": "v1.32.0"}}


def test_bad_gpu_and_kubelet_steps_say_what_is_allowed():
    p = lambda do: k8s.step_problems(do, NODES)
    assert p({"gpu": {"node": "nope", "advertised": True}})[0].startswith("k8s gpu needs a known Kubernetes node")
    assert p({"gpu": {"node": "kgpu001"}})[0].startswith("k8s gpu is {node, advertised")
    assert p({"gpu": {"node": "kgpu001", "advertised": True, "mig": {"strategy": "none"}}})[0].startswith("k8s gpu is {node, advertised")
    assert "strategy is one of single, mixed, none" in p({"gpu": {"node": "kgpu001", "mig": {"strategy": "x"}}})[0]
    assert any("profile looks like" in x for x in p({"gpu": {"node": "kgpu001", "mig": {"strategy": "single", "profile": "big"}}}))
    assert any("devices is a whole number" in x for x in p({"gpu": {"node": "kgpu001", "mig": {"strategy": "single", "devices": 0}}}))
    assert any("count is the node's whole GPUs" in x for x in p({"gpu": {"node": "kgpu001", "advertised": True, "count": 40}}))
    assert any("gpu_type is one of" in x for x in p({"gpu": {"node": "kgpu001", "advertised": True, "gpu_type": "x"}}))
    assert p({"gpu": {"node": "kgpu001", "mig": {"strategy": "mixed", "devices": 7}}}) == [] and p({"gpu": {"node": "kgpu001", "advertised": False}}) == []
    assert p({"kubelet": {"node": "nope", "skew": "newer"}})[0].startswith("k8s kubelet needs a known Kubernetes node")
    assert p({"kubelet": {"node": "kgpu001", "skew": "x"}}) == ["k8s kubelet skew is one of newer, older, same"] and p({"kubelet": {"node": "kgpu001", "skew": "same"}}) == []


def test_the_expectations_read_the_node_through_kubectl():
    from benchctl import scenario
    node = json.dumps({"status": {"allocatable": {"nvidia.com/gpu": "7"}, "nodeInfo": {"kubeletVersion": "v1.32.0"}}})
    run = lambda c, **kw: type("R", (), {"returncode": 0, "stdout": "v1.32.0" if "jsonpath" in " ".join(c) else node, "stderr": ""})()
    assert scenario.expectation({"k8s.node_gpus": {"node": "kgpu001", "allocatable": 7}}, run)[1]() is True
    assert scenario.expectation({"k8s.node_gpus": {"node": "kgpu001", "allocatable": 8}}, run)[1]() is False
    assert scenario.expectation({"k8s.node_gpus": {"node": "kgpu001", "resource": "nvidia.com/mig-1g.18gb", "allocatable": 0}}, run)[1]() is True
    assert scenario.expectation({"k8s.node_kubelet": {"node": "kgpu001", "skew": "older"}}, run)[1]() is True
    bad = lambda c, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": "x"})()
    assert scenario.expectation({"k8s.node_gpus": {"node": "kgpu001", "allocatable": 0}}, bad)[1]() is False
