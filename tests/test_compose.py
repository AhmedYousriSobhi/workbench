import importlib.util
from pathlib import Path

import pytest
import yaml
from invhelp import inv

from benchctl import compose

SIM = yaml.safe_load(Path("sim.yaml").read_text())


def test_profile_extends_union_and_override():
    std = compose.load_profile("std")
    assert std["groups"] == ["core", "mocks", "access", "accounting"]
    assert std["compute_nodes"] == 16  # own value beats the parent's
    assert compose.load_profile("full")["compute_nodes"] == 32
    assert compose.load_profile("mac-agent")["compute_nodes"] == 4  # inherited


def test_min_has_no_accounting_or_load():
    svcs = compose.generate(SIM, compose.load_profile("min"))["services"]
    assert "slurmctld" in svcs and "bmc-farm" in svcs
    assert not {"mariadb", "slurmdbd", "user-sim", "scenario-engine", "ticket-bot"} & set(svcs)


def test_mocks_publish_on_localhost_only():
    svcs = compose.generate(SIM, compose.load_profile("min"))["services"]
    assert svcs["bcm-mock"]["ports"] == ["127.0.0.1:8081:8081"]
    assert svcs["jira-mock"]["ports"] == ["127.0.0.1:8080:8080"]
    assert "./inventory.json:/inventory.json:ro" in svcs["bcm-mock"]["volumes"]  # the mocks read the generated inventory, not sim.yaml (issue 462)
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
    expected = set(exporter.load_nodes(inv(Path("sim.yaml"))))
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
    assert compose.budget_mb(data) == 200 + 60 + 3 * 100 + 40 + nodes * (60 + compose.BMC_FORWARDER_MB)


def test_write_marks_generated(tmp_path):
    out = compose.write(compose.generate(SIM, compose.load_profile("min")), tmp_path / "x" / "c.yml")
    assert out.read_text().startswith("# GENERATED")
    assert yaml.safe_load(out.read_text())["include"] == ["../observability.yml"]


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


def test_the_jira_mock_starts_with_a_team_working_the_project():
    svc = compose.generate(SIM, compose.load_profile("std"))["services"]["jira-mock"]
    assert svc["environment"]["JIRA_ACTIVITY_EVERY"] == "45"


def test_exporter_image_has_its_dependency_baked_in_and_pinned():
    """Issue 235: no `pip install` at container start; the exporter (the one image that still reads YAML) pins pyyaml. The BMC and BCM mocks read the
    generated inventory.json and need none (issue 462)."""
    root = Path(__file__).resolve().parents[1]
    obs = yaml.safe_load((root / "compose/observability.yml").read_text())["services"]["gpu-exporter-farm"]
    assert "build" in obs and "pip" not in str(obs.get("command", ""))
    assert (root / "components/gpu-exporter-farm/Dockerfile").read_text().count("pyyaml==6.0.3") == 1
    assert all("pyyaml" not in (root / f"components/{c}/Dockerfile").read_text() for c in ("bmc-farm", "bcm-mock"))


def _sim():
    from pathlib import Path

    import yaml
    return yaml.safe_load((Path(__file__).resolve().parent.parent / "sim.yaml").read_text())


def test_the_controller_runs_sshd_for_whitelisted_users_and_is_the_bcm_head():
    from benchctl import compose, controller
    sim = _sim()
    sim["users"].append({"name": "carol", "persona": "x", "qos": "normal"})
    sim["controller"] = {"enabled": True, "allow": {"users": ["carol"], "groups": ["admins"]}}
    assert controller.allowed_users(sim) == ["alice", "carol"]  # bob is in no whitelisted group
    out = compose.generate(sim, {"groups": ["core", "access", "mocks"]})["services"]
    ctl = out["slurmctld"]
    assert ctl["environment"]["WORKBENCH_SSH_ALLOW"] == "alice carol" and ctl["environment"]["WORKBENCH_SSHD"] == "1"
    assert ctl["environment"]["WORKBENCH_HEAD_IP"] == "10.141.255.1" and ctl["ports"] == ["127.0.0.1:2221:22"]
    assert any(v.endswith("/usr/local/bin/cmsh:ro") for v in ctl["volumes"]) and ctl["networks"]["default"]["aliases"] == ["controller"]
    assert ctl["mem_limit"] == f"{200 + controller.EXTRA_MB}m"
    assert out["login"]["environment"]["WORKBENCH_HEAD"] == "0"  # the login node is no longer the head node


def test_without_a_controller_the_login_node_acts_as_one():
    from benchctl import compose
    out = compose.generate(_sim(), {"groups": ["core", "access"]})["services"]
    assert "ports" not in out["slurmctld"] and "WORKBENCH_HEAD" not in out["login"]["environment"] and "WORKBENCH_SSHD" not in out["slurmctld"].get("environment", {})


def test_a_controller_nobody_may_enter_is_refused():
    import pytest

    from benchctl import compose, controller
    sim = _sim()
    sim["controller"] = {"enabled": True, "allow": {"users": ["nobody"], "groups": []}}
    assert len(controller.problems(sim)) == 2
    with pytest.raises(controller.ControllerError):
        compose.generate(sim, {"groups": ["core", "access"]})


def test_the_jump_host_is_a_bare_sshd_on_its_own_address_and_port():
    from benchctl import addressing, compose
    sim = _sim()
    out = compose.generate(sim, {"groups": ["core", "access", "jump"]})["services"]["jump"]
    assert out["ports"] == ["127.0.0.1:2220:22"] and out["networks"]["default"]["ipv4_address"] == addressing.service_ip("jump") == "10.141.255.5"
    assert out["build"].endswith("images/jump") and "slurm" not in " ".join(out["volumes"])


def test_the_l1_pause_flag_directory_is_mounted_read_only_on_the_login_and_the_jira_mock():
    """#476: the directory, not the file (Docker would turn a missing file into a directory); never writable from inside."""
    svcs = compose.generate(SIM, compose.load_profile("std"))["services"]
    assert "./flags:/run/workbench-flags:ro" in svcs["login"]["volumes"]
    assert "./flags:/run/workbench-flags:ro" in svcs["jira-mock"]["volumes"]


def test_prometheus_loads_the_default_alert_rules():
    """Issue 633: the rule file is mounted, listed in rule_files, and every rule has a name, an expression and a severity."""
    root = Path(__file__).resolve().parent.parent
    prom = yaml.safe_load((root / "observability/prometheus.yml").read_text())
    assert prom["rule_files"] == ["/etc/prometheus/rules.yml"]
    mounts = yaml.safe_load((root / "compose/observability.yml").read_text())["services"]["prometheus"]["volumes"]
    assert "../observability/rules.yml:/etc/prometheus/rules.yml:ro" in mounts
    rules = [r for g in yaml.safe_load((root / "observability/rules.yml").read_text())["groups"] for r in g["rules"]]
    assert {r["alert"] for r in rules} == {"GpuXidCritical", "GpuTempHigh", "InfinibandPortDown", "NodeDown", "ExporterDown", "JiraErrors"}
    assert all(r["expr"] and r["labels"]["severity"] for r in rules)


def test_alertmanager_is_a_core_service_wired_to_prometheus_and_the_jira_mock():
    """Issue 460, ADR 0072: a P1 service at its planned address, Prometheus sends to it, and every alert goes to the mock's receiver."""
    root = Path(__file__).resolve().parent.parent
    svc = yaml.safe_load((root / "compose/observability.yml").read_text())["services"]["alertmanager"]
    assert svc["cgroup_parent"] == "workbench-p1.slice" and svc["mem_limit"] == "64m"
    assert svc["ports"] == ["127.0.0.1:9093:9093"] and "cap_add" not in svc and "privileged" not in svc
    assert "../observability/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" in svc["volumes"]
    prom = yaml.safe_load((root / "observability/prometheus.yml").read_text())
    assert prom["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == ["alertmanager:9093"]
    assert {"job_name": "jira-mock", "static_configs": [{"targets": ["jira-mock:8080"]}]} in prom["scrape_configs"]
    am = yaml.safe_load((root / "observability/alertmanager.yml").read_text())
    hook = am["receivers"][0]["webhook_configs"][0]
    assert am["route"]["receiver"] == am["receivers"][0]["name"] and hook["url"] == "http://jira-mock:8080/sim/alerts" and hook["send_resolved"]


def test_every_default_rule_has_a_promtool_case():
    """tests/rules/rules_test.yml holds one case per rule; tests/real/test_alerting_real.py runs it in the Prometheus image."""
    root = Path(__file__).resolve().parent.parent
    rules = {r["alert"] for g in yaml.safe_load((root / "observability/rules.yml").read_text())["groups"] for r in g["rules"]}
    cases = yaml.safe_load((root / "tests/rules/rules_test.yml").read_text())["tests"]
    tested = {t["alertname"] for case in cases for t in case["alert_rule_test"]}
    assert tested == rules
