"""The Kubernetes nodes are machines (issue 380): BMC, BCM and GPU metrics list them, and a rule per node group says which get a BMC."""
import importlib.util
from pathlib import Path

import pytest
import yaml

from benchctl import addressing, compose, export, nodes, workbench
from governor import control

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def on(**kw):
    """The simulation with the Kubernetes part on, as `effective()` writes it."""
    return workbench.effective(SIM, workbench.Spec(parts=[*workbench.DEFAULT_PARTS, "k8s"], **kw))[0]


def test_kubernetes_nodes_are_hardware_only_when_the_part_is_on():
    assert [k for _, k in nodes.hardware_nodes(SIM)] == ["compute"] * 4 + ["gpu"] * 2
    kinds = [k for _, k in nodes.hardware_nodes(on())]
    assert kinds == ["compute"] * 4 + ["gpu"] * 2 + ["k8s-cpu"] * 2 + ["k8s-gpu"] * 2
    assert [n for n, _ in nodes.node_names(on())] == [n for n, _ in nodes.node_names(SIM)]  # Slurm, Teleport and ssh keep only the Slurm nodes


def test_every_machine_gets_its_own_bmc_address_and_port_and_the_slurm_ones_do_not_move():
    p0, p = addressing.plan(SIM), addressing.plan(on())
    assert all(p["bmc"][n] == ip and p["bmc_port"][n] == p0["bmc_port"][n] for n, ip in p0["bmc"].items())
    assert p["bmc"]["kcn001"] == "10.148.200.1" and p["bmc"]["kgpu001"] == "10.148.230.1"
    assert len(set(p["bmc"].values())) == 10 and sorted(p["bmc_port"].values()) == list(range(9000, 9010))
    assert not {"kcn001", "kgpu001"} & set(p["nodes"])  # no ssh address: the nodes are KWOK fakes


def test_bmc_groups_pick_the_machines_and_a_node_list_leaves_single_ones_out():
    assert compose.bmc_nodes(on()) == [n for n, _ in nodes.hardware_nodes(on())]                  # every group by default
    only_k = compose.bmc_nodes(on(bmc_groups=["k8s-cpu", "k8s-gpu"]))
    assert only_k == ["kcn001", "kcn002", "kgpu001", "kgpu002"]
    assert compose.bmc_nodes(on(bmc_groups=["slurm-gpu"])) == ["gpu001", "gpu002"]
    assert compose.bmc_nodes(on(bmc_groups=["k8s-gpu"], bmc_nodes=["kgpu001"])) == ["kgpu001"]     # an exception list narrows the groups
    assert compose.bmc_nodes(on(bmc_groups=[])) == []
    svcs = compose.generate(on(bmc_groups=["k8s-gpu"]), {**compose.load_profile("std"), "drop": []})["services"]
    assert {n for n in svcs if n.startswith("bmc-") and n != "bmc-farm"} == {"bmc-kgpu001", "bmc-kgpu002"}
    assert workbench.Spec(bmc_groups=["nope"]).problems() and workbench.Spec(bmc_groups=["k8s-gpu"]).problems() == []


def test_bmc_bcm_and_gpu_exporter_list_the_kubernetes_nodes(tmp_path):
    f = tmp_path / "sim.yaml"
    f.write_text(yaml.safe_dump(on()))
    bmc, bcm, gpu = (load(n, f"components/{d}/{m}.py") for n, d, m in (("bmc", "bmc-farm", "bmc"), ("bcm", "bcm-mock", "bcm"), ("exp", "gpu-exporter-farm", "exporter")))
    b, m, g = bmc.load_nodes(f), bcm.load_nodes(f), gpu.load_nodes(f)
    assert list(b)[-4:] == list(m)[-4:] == list(g)[-4:] == ["kcn001", "kcn002", "kgpu001", "kgpu002"]
    assert b["kgpu001"]["gpus"] == 8 and b["kcn001"]["gpus"] == 0 and b["kcn001"]["cpus"] == 64 and b["kcn001"]["memory_mb"] == 256 * 1024
    assert m["kgpu001"]["category"] == "k8s-gpu" and m["kcn001"]["category"] == "k8s-cpu"
    assert g["kgpu002"] == 8 and g["kcn001"] == 0
    state = bcm.Cluster(m)  # BCM shows the address the plan gives the BMC network's machine in the internal subnet of its block
    assert state.ip("kcn001") == "10.141.200.1" and state.ip("kgpu001") == "10.141.230.1"
    off = tmp_path / "off.yaml"
    off.write_text(yaml.safe_dump(SIM))
    assert "kcn001" not in bmc.load_nodes(off) and "kcn001" not in bcm.load_nodes(off) and "kcn001" not in gpu.load_nodes(off)


def test_the_client_export_lists_a_bmc_for_each_kubernetes_node():
    s = on()
    svcs = {"login", "slurmctld", "bmc-farm", *(f"bmc-{n}" for n, _ in nodes.hardware_nodes(s))}
    out = export.build(s, svcs, "w", include_bmc=True, user=None)
    assert [b["node"] for b in out["bmc"]][-2:] == ["kgpu001", "kgpu002"] and len(out["bmc"]) == 10


def test_the_governor_checks_bmc_groups():
    base = {"cpu_nodes": 2, "gpu_nodes": 0, "gpus_per_node": 8, "parts": ["bmc", "k8s"]}
    assert control.plan_error({**base, "bmc_groups": ["k8s-gpu", "slurm-cpu"]}) is None and control.plan_error({**base, "bmc_groups": []}) is None
    for bad in (["root"], "k8s-gpu", [1]):
        assert "bmc_groups" in control.plan_error({**base, "bmc_groups": bad}), bad


@pytest.mark.parametrize("flag", ["--bmc-groups"])
def test_the_cli_takes_bmc_groups(flag):
    from typer.testing import CliRunner

    from benchctl import cli

    r = CliRunner().invoke(cli.app, ["plan", "--with", "k8s", flag, "k8s-gpu"])
    assert r.exit_code == 0, r.output


def test_the_governor_checks_bcm_k8s():
    base = {"cpu_nodes": 2, "gpu_nodes": 0, "gpus_per_node": 8, "parts": ["bcm", "k8s"]}
    assert control.plan_error({**base, "bcm_k8s": False}) is None and control.plan_error(base) is None
    for bad in ("no", 0, [True]):
        assert "bcm_k8s" in control.plan_error({**base, "bcm_k8s": bad}), bad


def test_bcm_k8s_reaches_the_effective_sim_and_the_page_command():
    from benchctl import workbench

    sim = {"compute": {"prefix": "cn", "count": 1}, "gpu": {"prefix": "gpu", "nodes": 0, "gpus_per_node": 8}, "partitions": []}
    spec = workbench.Spec(cpu_nodes=1, gpu_nodes=0, parts=["bcm", "k8s"])
    assert "bcm" not in workbench.effective(sim, spec)[0]  # unset keeps sim.yaml
    spec.bcm_k8s = False
    assert workbench.effective(sim, spec)[0]["bcm"]["manage_kubernetes"] is False
    assert workbench.from_dict({"bcm_k8s": False}, workbench.Spec()).bcm_k8s is False


def test_the_cli_takes_no_bcm_k8s():
    from typer.testing import CliRunner

    from benchctl import cli

    r = CliRunner().invoke(cli.app, ["plan", "--with", "k8s", "--with", "bcm", "--no-bcm-k8s"])
    assert r.exit_code == 0, r.output
