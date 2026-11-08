"""Login nodes (#189): how many, and how each one authenticates (ssh, teleport, both)."""
import re
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


def test_client_profile_has_one_entry_per_route_into_each_login():
    s3 = sim("ssh", "teleport", "both")
    services = {"login", "login2", "login3", "slurmctld"}
    cl = export.build(s3, services, "w", False, None)["clusters"]
    assert [c["name"] for c in cl] == ["w", "w-login3", "w-login2", "w-login3-teleport"]   # key logins first (the first carries Slurm); `both` has two routes
    assert [c["connection"]["authMethod"] for c in cl] == ["private-key", "private-key", "agent", "agent"]
    assert cl[0]["teleport"] is None and cl[0]["scheduler"] is not None and cl[1]["scheduler"] is None
    # a Teleport route is the node's name plus {proxy, user, insecure}: the only fields the client's importer reads (checked against its parser)
    assert cl[2]["connection"] == {"host": "login2", "port": 22, "username": "alice", "authMethod": "agent"}
    assert cl[2]["teleport"] == {"proxy": "10.141.255.30:3080", "user": "alice", "insecure": True}
    assert cl[3]["connection"]["host"] == "login3" and cl[3]["teleport"] == cl[2]["teleport"]
    assert not re.search(r"identity|privateKeyPath|password|token", export.to_json({"clusters": cl}, ("x", 1)), re.IGNORECASE)  # no credential in the file
    only = export.build(sim("teleport"), {"login", "slurmctld"}, "w", False, None)["clusters"]
    assert len(only) == 1 and only[0]["teleport"] is not None                                # Teleport only is still a profile, not an error
    loop = export.build(sim("teleport", "ssh"), {"login", "login2", "slurmctld"}, "w", False, None, loopback=True)["clusters"]
    assert loop[0]["connection"]["port"] == 2223 and loop[1]["teleport"]["proxy"] == "127.0.0.1:3080"
    assert any("Teleport" in w and "Log in" in w for w in export.warnings(services, s3)) and not any("Teleport" in w for w in export.warnings(services, sim("ssh")))


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


# ---- s-login and k-login (issue 380): MODE[:KIND] ----
def test_an_entry_may_name_the_scheduler_it_serves():
    assert logins.parse("ssh:slurm,ssh:k8s,teleport") == ["ssh:slurm", "ssh:k8s", "teleport"]
    s = sim("ssh:slurm", "teleport:k8s", "both")
    assert logins.nodes(s) == [("login", "ssh"), ("login2", "teleport"), ("login3", "both")]   # auth modes: everything that used them is unchanged
    assert logins.kinds(s) == {"login": "slurm", "login2": "k8s", "login3": "both"}            # no kind = both, as before
    assert [n for n, _ in logins.direct(s)] == ["login", "login3"] and logins.via_teleport(s) == ["login2", "login3"]
    assert logins.serves("ssh:k8s", "k8s") and not logins.serves("ssh:k8s", "slurm") and logins.serves("ssh", "slurm") and logins.serves("ssh", "k8s")
    with pytest.raises(logins.LoginError, match=r"unknown login type 'k8x' in 'ssh:k8x'. Did you mean 'k8s'"):
        logins.parse("ssh:k8x")
    assert logins.problems(sim("ssh:k8s"), {"teleport"}) == ["login node login is a k-login (Kubernetes) but the k8s part is off (add --with k8s)"]
    assert logins.problems(sim("ssh:k8s"), {"k8s"}) == [] and logins.problems(sim("ssh:slurm"), set()) == []


def test_compose_gives_an_s_login_no_kubeconfig_and_a_k_login_no_slurm_configuration():
    s = {**sim("ssh:slurm", "ssh:k8s", "ssh"), "kubernetes": BASE.get("kubernetes", {})}
    prof = compose.load_profile("min")
    svcs = compose.generate(s, {**prof, "groups": [*prof["groups"], "k8s"]})["services"]
    vol = {n: svcs[n]["volumes"] for n in ("login", "login2", "login3")}
    kube = lambda n: any("/run/workbench/k8s/" in v for v in vol[n])
    slurm = lambda n: "./slurm:/etc/slurm:ro" in vol[n]
    assert (slurm("login"), kube("login")) == (True, False)       # s-login
    assert (slurm("login2"), kube("login2")) == (False, True)     # k-login
    assert (slurm("login3"), kube("login3")) == (True, True)      # both
    assert svcs["login2"]["depends_on"] == [] and svcs["login"]["depends_on"] == ["slurmctld"]
    assert [svcs[n]["environment"]["WORKBENCH_LOGIN_KIND"] for n in ("login", "login2", "login3")] == ["slurm", "k8s", "both"]
    with pytest.raises(logins.LoginError, match="k-login"):
        compose.generate(sim("ssh:k8s"), prof)


def test_connections_names_the_type_and_every_ssh_command_carries_identities_only():
    rows = {e["id"]: e for e in connections.entities({**sim("ssh:slurm", "ssh:k8s"), "kubernetes": BASE.get("kubernetes", {})})}
    assert "s-login" in rows["login"]["name"] and "k-login" in rows["login2"]["name"]
    assert "kubectl only" in rows["login2"]["how"] and "Slurm tools only" in rows["login"]["how"]
    for r in rows.values():
        if r["kind"] == "ssh" or r["id"] == "k8s":
            assert "IdentitiesOnly=yes" in r["command"], r["id"]
    assert rows["k8s"]["command"].startswith("ssh -i") and "10.141.255.252" in rows["k8s"]["command"]    # kubectl runs on the k-login
    assert "10.141.255.254" in rows["nodes"]["command"]                                                  # the nodes are reached from the s-login
    k8s_row = next(a for a in rows["k8s"]["actions"] if a["kind"] == "copy")
    assert "@login2 kubectl" in k8s_row["value"]


def test_the_graph_joins_each_login_to_the_nodes_it_serves():
    from webui import graph

    nodes = [{"name": "cn001", "kind": "compute"}, {"name": "kgpu001", "kind": "k8s"}]
    lg = [{"name": "login", "mode": "ssh", "kind": "slurm"}, {"name": "login2", "mode": "ssh", "kind": "k8s"}, {"name": "login3", "mode": "ssh", "kind": "both"}]
    g = graph.build({}, nodes, lg)
    ids = {e["id"] for e in g["edges"]}
    assert {"login>nodes:jump", "login2>k8s-nodes:kubectl", "login3>nodes:jump", "login3>k8s-nodes:kubectl"} <= ids
    assert not ({"login2>nodes:jump", "login>k8s-nodes:kubectl"} & ids)
    assert {"id": "k8s-nodes", "type": "group"} in g["entities"] and {"id": "kgpu001", "type": "k8s-node", "group": "k8s-nodes"} in g["entities"]
    assert not any(e["id"] == "k8s-nodes" for e in graph.build({}, nodes[:1], lg)["entities"])      # no Kubernetes nodes, no group


def test_the_cli_and_the_governor_accept_a_login_kind():
    base = {"cpu_nodes": 2, "gpu_nodes": 0, "gpus_per_node": 8, "parts": ["k8s"]}
    assert control.plan_error({**base, "login_nodes": ["ssh:slurm", "teleport:k8s", "both:both"]}) is None
    for bad in (["ssh:"], ["ssh:root"], ["ssh:k8s;rm"], [":k8s"]):
        assert "login_nodes" in control.plan_error({**base, "login_nodes": bad}), bad
    assert completion.login_modes("ssh:k") == ["ssh:k8s"] and completion.login_modes("ssh,teleport:s") == ["ssh,teleport:slurm"]


def test_the_page_has_a_type_per_login_a_kubernetes_group_and_a_kubectl_line():
    html = (ROOT / "webui/static/index.html").read_text()
    for needle in ("KIND_LABEL", "seg(i, 'kind'", "pl-lg-split", "pl-kon", "pl-kcpu", "pl-sum", "GROUP_TITLE", "br-k8s", "k8s-nodes:kubectl", "'kubectl'", "KIND_NAME[loginKind(l)]"):
        assert needle in html, needle
