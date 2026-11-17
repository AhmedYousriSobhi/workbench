"""GPU shapes and kubelet skew as scenario steps on KWOK nodes (issue 766, ADR 0084 addendum).

`gpu: {node, count, advertised}` makes a node advertise no GPUs (allocatable and capacity 0 while the labels still say it has them) and back.
`gpu: {node, count, mig: {strategy: single|mixed|none, profile, devices}}` turns a node into a MIG node as NVIDIA's device plugin and GPU Feature
Discovery publish it (README of v0.20.1): `single` overrides `gpu.product` (`<model>-MIG-<profile>`) and `gpu.count` and serves the MIG devices as
`nvidia.com/gpu`; `mixed` keeps the whole GPUs as `nvidia.com/gpu` (one fewer) and serves `nvidia.com/mig-<profile>` plus the label
`nvidia.com/mig-<profile>.count`; `none` puts the node back. `kubelet: {node, skew}` sets the node's kubeletVersion newer than the API server
or four minors older (kubelets may be at most three behind). Labels use `kubectl label`; resources and the version use the node's status
subresource (a normal patch ignores `status`), as apply.sh does for the fake GPUs.
"""
import json
import re

GPU_PRODUCT = {"h200": "NVIDIA-H200", "h100": "NVIDIA-H100-80GB-HBM3", "a100": "NVIDIA-A100-SXM4-80GB", "l40s": "NVIDIA-L40S", "v100": "Tesla-V100-SXM2-32GB"}
PROFILES = {"h200": ("1g.18gb", "1g.35gb", "2g.35gb", "3g.71gb", "4g.71gb", "7g.141gb"),
            "h100": ("1g.10gb", "1g.20gb", "2g.20gb", "3g.40gb", "4g.40gb", "7g.80gb")}  # NVIDIA MIG user guide, supported profiles
STRATEGIES = ("single", "mixed", "none")
SKEWS = ("newer", "older", "same")


def _status(node: str, patch: dict) -> list[str]:
    return ["patch", "node", node, "--subresource=status", "--type=merge", "-p", json.dumps({"status": patch})]


def gpu_problems(g, nodes: set[str]) -> list[str]:
    if not isinstance(g, dict) or g.get("node") not in nodes:
        return [f"k8s gpu needs a known Kubernetes node, not {g.get('node') if isinstance(g, dict) else g!r}"]
    out = []
    if not isinstance(g.get("count", 8), int) or isinstance(g.get("count", 8), bool) or not 1 <= g.get("count", 8) <= 16:
        out.append("k8s gpu count is the node's whole GPUs, 1 to 16")
    if ("advertised" in g) == ("mig" in g):
        return out + ["k8s gpu is {node, advertised: true|false} or {node, mig: {strategy, profile, devices}}"]
    if "advertised" in g and not isinstance(g["advertised"], bool):
        out.append("k8s gpu advertised is true or false")
    if "mig" in g:
        m = g["mig"] if isinstance(g["mig"], dict) else {}
        if m.get("strategy") not in STRATEGIES:
            out.append(f"k8s gpu mig strategy is one of {', '.join(STRATEGIES)}")
        if not re.fullmatch(r"[0-9]g\.[0-9]+gb", str(m.get("profile", "1g.18gb"))):
            out.append("k8s gpu mig profile looks like 1g.18gb (NVIDIA's MIG profile names)")
        if not isinstance(m.get("devices", 7), int) or isinstance(m.get("devices", 7), bool) or not 1 <= m.get("devices", 7) <= 56:
            out.append("k8s gpu mig devices is a whole number, 1 to 56")
    if g.get("gpu_type", "h200") not in GPU_PRODUCT:
        out.append(f"k8s gpu gpu_type is one of {', '.join(GPU_PRODUCT)}")
    return out


def gpu_calls(g: dict) -> list[list[str]]:
    node, count, gtype = g["node"], int(g.get("count", 8)), str(g.get("gpu_type", "h200"))
    if "advertised" in g:
        n = str(count if g["advertised"] else 0)
        return [_status(node, {"capacity": {"nvidia.com/gpu": n}, "allocatable": {"nvidia.com/gpu": n}})]
    m = g["mig"]
    strategy, profile, devices = m["strategy"], m.get("profile", "1g.18gb"), int(m.get("devices", 7))
    product, res = GPU_PRODUCT[gtype], f"nvidia.com/mig-{profile}"
    label = lambda *kv: ["label", "node", node, *kv, "--overwrite"]
    if strategy == "single":
        return [label("nvidia.com/mig.strategy=single", f"nvidia.com/gpu.product={product}-MIG-{profile}", f"nvidia.com/gpu.count={devices}"),
                _status(node, {"capacity": {"nvidia.com/gpu": str(devices), res: None}, "allocatable": {"nvidia.com/gpu": str(devices), res: None}})]
    if strategy == "mixed":
        return [label("nvidia.com/mig.strategy=mixed", f"{res}.count={devices}"),
                _status(node, {"capacity": {"nvidia.com/gpu": str(count - 1), res: str(devices)}, "allocatable": {"nvidia.com/gpu": str(count - 1), res: str(devices)}})]
    return [label("nvidia.com/mig.strategy=none", f"nvidia.com/gpu.product={product}", f"nvidia.com/gpu.count={count}"),
            ["label", "node", node, f"{res}.count-"],
            _status(node, {"capacity": {"nvidia.com/gpu": str(count), res: None}, "allocatable": {"nvidia.com/gpu": str(count), res: None}})]


def skewed(base: str, skew: str) -> str:
    """The API server's version `base` (vX.Y.Z) as a kubelet's: newer = next minor, older = four minors behind (outside the supported three)."""
    m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", base)
    if skew == "same" or not m:
        return base
    major, minor, _patch = (int(x) for x in m.groups())
    return f"v{major}.{minor + 1}.0" if skew == "newer" else f"v{major}.{max(minor - 4, 0)}.0"


def kubelet_problems(k, nodes: set[str]) -> list[str]:
    if not isinstance(k, dict) or k.get("node") not in nodes:
        return [f"k8s kubelet needs a known Kubernetes node, not {k.get('node') if isinstance(k, dict) else k!r}"]
    return [] if k.get("skew") in SKEWS else [f"k8s kubelet skew is one of {', '.join(SKEWS)}"]


def kubelet_calls(k: dict, api_version: str) -> list[list[str]]:
    return [_status(k["node"], {"nodeInfo": {"kubeletVersion": skewed(api_version, k["skew"])}})]
