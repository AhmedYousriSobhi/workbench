"""Login nodes (#189): how many, and how each one authenticates (ssh, teleport, both)."""
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from benchctl import addressing, cli, completion, compose, export, logins, teleport, workbench
from governor import control
from webui import connections

ROOT = Path(__file__).resolve().parent.parent
BASE = yaml.safe_load((ROOT / "sim.yaml").read_text())
runner = CliRunner()


def sim(*modes):
    return {**BASE, "login": list(modes)}


def test_names_modes_and_validation():
    assert [logins.name(i) for i in range(3)] == ["login", "login2", "login3"]
    assert logins.is_login("login") and logins.is_login("login2") and not logins.is_login("login1") and not logins.is_login("loginx")
    assert logins.modes(BASE) == ["ssh"]                      # nothing declared: the one direct-SSH login node, as before
    assert logins.parse(["ssh,teleport", "both"]) == ["ssh", "teleport", "both"]
    assert logins.parse(None) is None
    with pytest.raises(logins.LoginError, match=r"Did you mean 'teleport'\? Valid: ssh, teleport, both"):
        logins.parse("telepor")
    for bad in ([], ["ssh"] * 9):
        with pytest.raises(logins.LoginError, match="1 to 8"):
            logins.parse(bad)
    s = sim("ssh", "teleport", "both")
    assert logins.direct(s) == [("login", 0), ("login3", 2)] and logins.via_teleport(s) == ["login2", "login3"]
    assert logins.problems(s, {"teleport"}) == [] and "login2" in logins.problems(s, set())[0]


def test_addresses_skip_the_gateway_and_stay_clear_of_the_services():
    p = addressing.plan(sim(*["ssh"] * 8))
    ips = list(p["logins"].values())
    assert ips[0] == addressing.LOGIN_IP and len(set(ips)) == 8
    assert addressing.GATEWAY not in ips and all(int(i.rsplit(".", 1)[1]) > max(addressing.SERVICE_HOSTS.values()) for i in ips)
    assert addressing.address_of(p, "login3") == p["logins"]["login3"] and p["ipmi_logins"]["login2"].startswith("10.148.255.")


def test_compose_publishes_a_port_only_for_direct_logins_and_gives_teleport_logins_no_key():
    prof = compose.load_profile("min")
    s = compose.generate(sim("ssh", "teleport", "both"), {**prof, "groups": [*prof["groups"], "teleport"]})["services"]
    assert s["login"]["ports"] == ["127.0.0.1:2222:22"] and "ports" not in s["login2"] and s["login3"]["ports"] == ["127.0.0.1:2224:22"]
    assert [s[n]["environment"]["WORKBENCH_LOGIN_AUTH"] for n in ("login", "login2", "login3")] == ["ssh", "teleport", "both"]
    tele = "./secrets/teleport:/run/workbench/teleport:ro"
    assert tele not in s["login"]["volumes"] and tele in s["login2"]["volumes"] and tele in s["login3"]["volumes"]
    assert "build" in s["login"] and "build" not in s["login2"]   # the image is built once
    assert s["login2"]["hostname"] == "login2" and s["login2"]["mem_limit"] == s["login"]["mem_limit"]
    with pytest.raises(logins.LoginError, match="teleport part is off"):
        compose.generate(sim("ssh", "teleport"), prof)


def test_teleport_registers_only_the_logins_that_use_it():
    s = sim("ssh", "teleport", "both")
    ips = addressing.plan(s, 2)
    assert set(teleport.targets(s, ips)) == {"login2", "login3", *ips["nodes"]}
    docs = list(yaml.safe_load_all(teleport.resources(s, ips)))
    assert {d["spec"]["hostname"]: d["metadata"]["labels"]["kind"] for d in docs if d["kind"] == "node"}["login2"] == "login"


def test_login_script_gives_a_teleport_login_an_empty_authorized_keys():
    text = (ROOT / "images/login/login.sh").read_text()
    assert 'WORKBENCH_LOGIN_AUTH:-ssh}" = "teleport"' in text and "install -m 644 /dev/null /etc/ssh/authorized_keys" in text


def test_client_profile_has_one_cluster_per_login_with_its_auth_method():
    s3 = sim("ssh", "teleport", "both")
    services = {"login", "login2", "login3", "slurmctld"}
    cl = export.build(s3, services, "w", False, None)["clusters"]
    assert [c["name"] for c in cl] == ["w", "w-login3", "w-login2"]            # a key login first (it carries Slurm), Teleport only last
    assert [c["connection"]["authMethod"] for c in cl] == ["private-key", "private-key", "teleport"]
    assert cl[0]["teleport"] is None and cl[0]["scheduler"] is not None and cl[1]["scheduler"] is None
    assert cl[1]["teleport"] == {"proxy": "10.141.255.30:3080", "dnsName": "teleport.workbench.test", "clusterName": "workbench", "insecure": True, "node": "login3", "username": "alice"}
    assert cl[2]["connection"]["port"] == 3080 and cl[2]["connection"]["host"] == "10.141.255.30" and cl[2]["teleport"]["node"] == "login2"
    assert "identity" not in export.to_json({"clusters": cl}, ("x", 1))         # no credential in the file
    only = export.build(sim("teleport"), {"login", "slurmctld"}, "w", False, None)["clusters"]
    assert len(only) == 1 and only[0]["connection"]["authMethod"] == "teleport"  # Teleport only is still a profile, not an error
    loop = export.build(sim("teleport", "ssh"), {"login", "login2", "slurmctld"}, "w", False, None, loopback=True)["clusters"]
    assert loop[0]["connection"]["port"] == 2223 and loop[1]["connection"]["port"] == 3080
    assert any("Teleport" in w for w in export.warnings(services, s3)) and export.warnings(services, sim("ssh")) == [
        "Slurm accounting is not part of this cluster, so `sacct` says accounting storage is disabled and the client cannot read job history: rebuild with `benchctl up --with accounting`"]


def test_connections_has_one_row_per_login_with_the_right_command():
    rows = {e["id"]: e for e in connections.entities(sim("ssh", "teleport", "both"))}
    assert rows["login"]["command"].startswith("ssh -i") and "Teleport only" in rows["login2"]["name"]
    assert rows["login2"]["command"].startswith("tsh --proxy=10.141.255.30:3080") and rows["login2"]["command"].endswith("ssh alice@login2")
    assert "No published port" in rows["login2"]["how"] and "accepts Teleport" in rows["login3"]["how"] and "2224" in rows["login3"]["how"]
    only = {e["id"]: e for e in connections.entities(sim("teleport"))}
    assert only["nodes"]["command"].startswith("tsh ")      # no key login anywhere: nodes are reached through Teleport


def test_spec_carries_the_logins_and_checks_them():
    spec = workbench.defaults(BASE)
    assert spec.login_nodes == ["ssh"] and spec.problems() == []
    spec.login_nodes = ["ssh", "teleport"]
    assert "teleport part is off" in spec.problems()[0]
    spec.parts = [*spec.parts, "teleport"]
    assert spec.problems() == [] and workbench.effective(BASE, spec)[0]["login"] == ["ssh", "teleport"]
    assert "Login nodes: login (ssh), login2 (teleport)" in workbench.render(spec, workbench.estimate(*workbench.effective(BASE, spec)))
    assert workbench.from_dict({"login_nodes": ["both"]}, workbench.defaults(BASE)).login_nodes == ["both"]


def test_cli_login_flag_turns_teleport_on_and_refuses_a_typo(monkeypatch):
    r = runner.invoke(cli.app, ["plan", "--login", "ssh,teleport"])
    assert "Login nodes: login (ssh), login2 (teleport)" in r.output and "teleport" in r.output.split("(always")[0]
    r = runner.invoke(cli.app, ["plan", "--login", "ssh,telepor"])
    assert r.exit_code == 1 and "Did you mean 'teleport'" in r.output
    calls = []
    monkeypatch.setattr(cli, "_launcher", lambda *a: calls.append(a))
    monkeypatch.setattr(cli, "_offer_completion", lambda: None)
    runner.invoke(cli.app, ["start", "--login", "ssh,teleport"])
    args = calls[-1]
    assert args[args.index("--login") + 1] == "ssh,teleport" and "teleport" in [args[i + 1] for i, a in enumerate(args) if a == "--with"]
    assert runner.invoke(cli.app, ["start", "--profile", "min", "--login", "ssh"]).exit_code == 1


def test_login_completion_follows_commas():
    assert completion.login_modes("") == ["ssh", "teleport", "both"]
    assert completion.login_modes("t") == ["teleport"] and completion.login_modes("ssh,") == ["ssh,ssh", "ssh,teleport", "ssh,both"]
    assert completion.login_modes("ssh,t") == ["ssh,teleport"]


def test_governor_checks_the_login_modes_it_will_put_on_a_command_line():
    base = {"cpu_nodes": 2, "gpu_nodes": 0, "gpus_per_node": 8, "parts": []}
    assert control.plan_error(base) is None and control.plan_error({**base, "login_nodes": ["ssh", "teleport"]}) is None
    for bad in (["root; rm"], [], ["ssh"] * 9, "ssh"):
        assert "login_nodes" in control.plan_error({**base, "login_nodes": bad})


def test_the_sheet_has_a_login_row_and_sends_the_modes():
    html = (ROOT / "webui/static/index.html").read_text()
    assert 'id="pl-loginrows"' in html and "q.set('logins', loginModes.join(','))" in html
    assert "LOGIN_LABEL = { ssh: 'Direct SSH', teleport: 'Teleport only', both: 'Direct SSH and Teleport' }" in html
