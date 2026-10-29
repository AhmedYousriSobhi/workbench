import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from simctl import compose

SIM = yaml.safe_load(Path("sim.yaml").read_text())


def test_profile_extends_union_and_override():
    std = compose.load_profile("std")
    assert std["groups"] == ["core", "mocks", "scenarios", "accounting", "load"]
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
    assert "../../sim.yaml:/sim.yaml:ro" in svcs["bcm-mock"]["volumes"]
    assert svcs["bcm-mock"]["build"] == "../../components/bcm-mock"


def test_every_service_is_governable():
    for name in ("min", "std", "full"):
        for svc_name, svc in compose.generate(SIM, compose.load_profile(name))["services"].items():
            prio = svc["labels"]["hpcsim.class"]
            assert svc["mem_limit"].endswith("m"), svc_name
            assert svc["cgroup_parent"] == f"hpcsim-{prio.lower()}.slice", svc_name


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
    kinds = [s["labels"].get("hpcsim.node") for s in svcs.values()]
    assert kinds.count("compute") == 16
    assert kinds.count("gpu") == SIM["gpu"]["nodes"]
    assert svcs["cn016"]["depends_on"] == ["slurmctld"]


def test_budget_matches_limits():
    data = compose.generate(SIM, compose.load_profile("min"))
    assert compose.budget_mb(data) == 200 + 60 + 3 * 100 + 60 + (4 + SIM["gpu"]["nodes"]) * 60


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
