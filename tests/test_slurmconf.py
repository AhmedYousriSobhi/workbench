from pathlib import Path

import yaml

from simctl import compose, slurmconf

SIM = yaml.safe_load(Path("sim.yaml").read_text())


def test_nodes_and_partitions_follow_sim_yaml():
    conf = slurmconf.slurm_conf(SIM, 16)
    assert "NodeName=cn[001-016] CPUs=8 RealMemory=4096" in conf
    assert "NodeName=gpu[001-002]" in conf and "Gres=gpu:h200:8" in conf
    assert "PartitionName=cpu Nodes=cn[001-016] Default=YES" in conf
    assert "PartitionName=gpu Nodes=gpu[001-002]" in conf


def test_single_node_range_and_no_gpu():
    sim = {**SIM, "gpu": {"nodes": 0}, "compute": {**SIM["compute"], "count": 1}}
    conf = slurmconf.slurm_conf(sim)
    assert "NodeName=cn001 " in conf and "gpu" not in conf.replace("GresTypes=gpu", "")
    assert slurmconf.gres_conf(sim).count("NodeName") == 0


def test_gres_only_names_gpu_nodes():
    assert "NodeName=gpu[001-002] Name=gpu Type=h200 Count=8" in slurmconf.gres_conf(SIM)
    assert "NodeName=ALL" not in slurmconf.gres_conf(SIM)


def test_conf_names_equal_compose_services():
    conf_nodes = {n for n, _ in compose.node_names(SIM, 4)}
    svcs = compose.generate(SIM, compose.load_profile("min"))["services"]
    assert conf_nodes <= set(svcs) and "slurmctld" in svcs
    assert svcs["slurmctld"]["hostname"] == "slurmctld" == slurmconf.CTLD


def test_write_creates_config_next_to_compose(tmp_path):
    compose.write(compose.generate(SIM, compose.load_profile("min")), tmp_path / "c.yml", SIM, 4)
    assert (tmp_path / "slurm" / "slurm.conf").read_text().startswith("# GENERATED")
    assert (tmp_path / "slurm" / "gres.conf").exists()
