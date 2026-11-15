"""Dynamic Resource Allocation objects for the KWOK GPU nodes (issue 547, ADR 0076).

One `DeviceClass` and, per GPU node, one `ResourceSlice` that lists the node's GPUs the way a vendor's DRA driver would (driver
`gpu.sim.test`). No driver runs: the objects are written straight to the API server, so the real kube-scheduler allocates a pod's
`ResourceClaim` against them. The device UUIDs come from the machine inventory (`hwinventory.gpu_uuid`), so they are the
`UUID` label of the same node's DCGM series. Generated at `up` into `compose/generated/k8s/50-dra.yaml`, never edited.
Not used: partitionable devices (MIG slices), which still sit behind a feature gate.
"""
import yaml

from . import hwinventory

DRIVER = "gpu.sim.test"
CLASS = "gpu.sim.test"
MEMORY = {"h200": "143771Mi", "h100": "81559Mi", "a100": "81920Mi", "l40s": "46068Mi", "v100": "32768Mi"}  # as the GPU exporter's FB_TOTAL_MIB
MODEL = {"h200": "NVIDIA H200", "h100": "NVIDIA H100 80GB HBM3", "a100": "NVIDIA A100-SXM4-80GB", "l40s": "NVIDIA L40S",
         "v100": "Tesla V100-SXM2-32GB"}
NVLINK_GROUP = 8  # GPUs that share an NVLink domain (one HGX baseboard)


def device_class() -> dict:
    return {"apiVersion": "resource.k8s.io/v1", "kind": "DeviceClass", "metadata": {"name": CLASS},
            "spec": {"selectors": [{"cel": {"expression": f'device.driver == "{DRIVER}"'}}]}}


def device(node: str, index: int, gpu_type: str) -> dict:
    t = gpu_type.lower()
    return {"name": f"gpu-{index}",
            "attributes": {"uuid": {"string": hwinventory.gpu_uuid(node, index)}, "model": {"string": MODEL.get(t, t.upper())},
                           "index": {"int": index}, "nvlinkDomain": {"string": f"{node}-nvl{index // NVLINK_GROUP}"}},
            "capacity": {"memory": {"value": MEMORY.get(t, "81920Mi")}}}


def resource_slice(node: str, gpus: int, gpu_type: str) -> dict:
    return {"apiVersion": "resource.k8s.io/v1", "kind": "ResourceSlice", "metadata": {"name": f"{node}-{DRIVER}"},
            "spec": {"driver": DRIVER, "nodeName": node, "pool": {"name": node, "generation": 1, "resourceSliceCount": 1},
                     "devices": [device(node, i, gpu_type) for i in range(gpus)]}}


def slices_yaml(node_groups: list[tuple[str, dict]]) -> str:
    """The class and a slice per GPU node of `node_groups` ([(name, sim.yaml group)]); empty when there is no GPU node."""
    docs = [resource_slice(n, int(g["gpus"]), str(g.get("gpu_type", "h200"))) for n, g in node_groups if g.get("gpus")]
    return "".join("---\n" + yaml.safe_dump(d, sort_keys=False) for d in ([device_class(), *docs] if docs else []))
