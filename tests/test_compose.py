import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from benchctl import compose

SIM = yaml.safe_load(Path("sim.yaml").read_text())


def test_profile_extends_union_and_override():
    std = compose.load_profile("std")
    assert std["groups"] == ["core", "mocks", "access", "scenarios", "accounting", "load"]
    assert std["compute_nodes"] == 16  # own value beats the parent's
    assert compose.load_profile("full")["compute_nodes"] == 32
    assert compose.load_profile("mac-agent")["compute_nodes"] == 4  # inherited


def test_min_has_no_accounting_or_load():
    svcs = compose.generate(SIM, compose.load_profile("min"))["services"]
    assert "slurmctld" in svcs and "bmc-farm" in svcs
    assert not {"mariadb", "slurmdbd", "user-sim"} & set(svcs)


def test_mocks_publish_on_localhost_only():
    svcs = compose.generate(SIM, compose.load_profile("min"))["services"]
    assert svcs["bcm-mock"]["ports"] == ["127.0.0.1:8081:8081"]
    assert svcs["jira-mock"]["ports"] == ["127.0.0.1:8080:8080"]
    assert "./sim.yaml:/sim.yaml:ro" in svcs["bcm-mock"]["volumes"]
    assert svcs["bcm-mock"]["build"] == "../../components/bcm-mock"


def test_every_service_is_governable():
    for name in ("min", "std", "full"):
        for svc_name, svc in compose.generate(SIM, compose.load_profile(name))["services"].items():
            prio = svc["labels"]["workbench.class"]
            assert svc["mem_limit"].endswith("m"), svc_name
            assert svc["cgroup_parent"] == f"workbench-{prio.lower()}.slice", svc_name


def test_node_names_match_gpu_exporter():
    spec = importlib.util.spec_from_file_location("exporter", "components/gpu-exporter-farm/exporter.py")
    exporter = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(exporter)
    except ModuleNotFoundError:
        pytest.skip("pyyaml missing")
    expected = set(exporter.load_nodes(Path("sim.yaml")))
    generated = {n for n, _ in compose.node_names(SIM)}
    assert generated == expected


def test_slurmd_count_follows_profile():
    svcs = compose.generate(SIM, compose.load_profile("std"))["services"]
    kinds = [s["labels"].get("workbench.node") for s in svcs.values()]
    assert kinds.count("compute") == 16
    assert kinds.count("gpu") == SIM["gpu"]["nodes"]
    assert svcs["cn016"]["depends_on"] == ["slurmctld"]


def test_budget_matches_limits():
    data = compose.generate(SIM, compose.load_profile("min"))
    nodes = 4 + SIM["gpu"]["nodes"]  # every node also has its BMC forwarder
    assert compose.budget_mb(data) == 200 + 60 + 3 * 100 + 40 + 60 + nodes * (60 + compose.BMC_FORWARDER_MB)


def test_write_marks_generated(tmp_path):
    out = compose.write(compose.generate(SIM, compose.load_profile("min")), tmp_path / "x" / "c.yml")
    assert out.read_text().startswith("# GENERATED")
    assert yaml.safe_load(out.read_text())["include"] == ["../observability.yml"]


@pytest.mark.skipif(not shutil.which("docker"), reason="docker missing")
def test_docker_compose_accepts_generated_file():
    out = compose.write(compose.generate(SIM, compose.load_profile("std")),
                        Path("compose/generated/test-std.yml"))
    try:
        r = subprocess.run(["docker", "compose", "-f", str(out), "config", "-q"],
                           capture_output=True, text=True, timeout=60, check=False)
        assert r.returncode == 0, r.stderr
    finally:
        out.unlink()


def test_bmc_farm_publishes_tls_and_every_node_has_its_own_bmc_address():
    services = compose.generate(SIM, compose.load_profile("min"))["services"]
    farm = services["bmc-farm"]
    assert "127.0.0.1:8443:8443" in farm["ports"] and "127.0.0.1:8000:8000" in farm["ports"]
    assert any(v.endswith(":/run/workbench/bmc_credentials:ro") for v in farm["volumes"])
    one = services["bmc-gpu001"]  # a forwarder on the ipmi network, standard port 443, to this node's farm port
    assert one["networks"] == {"ipmi": {"ipv4_address": "10.148.100.1"}} and one["mem_limit"] == "32m"
    assert one["command"][-2:] == ["443:bmc-farm:9004", "8443:bmc-farm:9004"] and one["depends_on"] == ["bmc-farm"]
    assert set(farm["networks"]) == {"default", "ipmi"} and set(services["login"]["networks"]) == {"default", "ipmi"}


def test_a_bmc_selection_limits_the_forwarders_and_none_means_every_node():
    sim = {**SIM, "bmc": {"nodes": ["cn002", "gpu001"]}}
    names = sorted(n for n in compose.generate(sim, compose.load_profile("min"))["services"] if n.startswith("bmc-") and n != "bmc-farm")
    assert names == ["bmc-cn002", "bmc-gpu001"]
    assert compose.bmc_nodes({**SIM, "bmc": {"nodes": []}}) == [] and len(compose.bmc_nodes(SIM)) == 6
    off = compose.generate({**SIM, "bmc": {"nodes": []}}, compose.load_profile("min"))["services"]
    assert not [n for n in off if n.startswith("bmc-") and n != "bmc-farm"] and "bmc-farm" in off


def test_compute_nodes_run_sshd_with_the_shared_keys():
    svcs = compose.generate(SIM, compose.load_profile("min"))["services"]
    for name in ("cn001", "gpu001"):
        assert "./secrets/ssh:/run/workbench/ssh:ro" in svcs[name]["volumes"]
        assert svcs[name]["environment"]["WORKBENCH_SSHD"] == "1" and "ports" not in svcs[name]  # reached via login only


def test_login_node_is_local_only_with_keys_mounted():
    login = compose.generate(SIM, compose.load_profile("min"))["services"]["login"]
    assert login["ports"] == ["127.0.0.1:2222:22"]
    assert "./secrets/ssh:/run/workbench/ssh:ro" in login["volumes"] and "./slurm:/etc/slurm:ro" in login["volumes"]
    assert login["build"] == "../../images/login" and login["labels"]["workbench.class"] == "P2"
    assert "cap_add" not in login and login["environment"]["WORKBENCH_USERS"]
