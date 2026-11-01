import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from benchctl import addressing, compose, stack, teleport, workbench

ROOT = Path(__file__).resolve().parents[1]
SIM = {**yaml.safe_load((ROOT / "sim.yaml").read_text()), "login": ["both", "teleport"]}  # login: direct SSH and Teleport; login2: Teleport only
IPS = addressing.plan(SIM, 2)


def cp(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


def test_settings_default_and_validated():
    assert teleport.settings({}) == {"version": teleport.DEFAULT_VERSION, "cert_ttl": "12h"}
    assert teleport.image({"teleport": {"version": "15.4.0"}}).endswith("teleport-distroless:15.4.0")
    with pytest.raises(teleport.TeleportError, match="version"):
        teleport.settings({"teleport": {"version": "latest"}})
    with pytest.raises(teleport.TeleportError, match="cert_ttl"):
        teleport.settings({"teleport": {"cert_ttl": "forever"}})


def test_config_is_auth_and_proxy_only_on_one_port():
    c = yaml.safe_load(teleport.config(SIM))
    assert c["auth_service"]["proxy_listener_mode"] == "multiplex" and c["version"] == "v3" and c["ssh_service"] == {"enabled": False}
    assert c["auth_service"]["cluster_name"] == "workbench" and c["auth_service"]["authentication"] == {"type": "local", "second_factors": ["otp"]}
    assert c["proxy_service"]["web_listen_addr"] == "0.0.0.0:3080" and c["proxy_service"]["public_addr"] == ["10.141.255.30:3080", "teleport.workbench.test:3080"]


def test_resources_name_every_node_and_user_with_stable_ids():
    docs = list(yaml.safe_load_all(teleport.resources(SIM, IPS)))
    kinds = [d["kind"] for d in docs]
    assert kinds[0] == "role" and "user" not in kinds   # users are separate files, created only when absent
    nodes = {d["spec"]["hostname"]: d for d in docs if d["kind"] == "node"}
    assert set(nodes) == {"login", "login2", *IPS["nodes"]}
    assert nodes["login"]["spec"]["addr"] == f"{addressing.LOGIN_IP}:22" and nodes["login"]["sub_kind"] == "openssh"
    assert nodes["login2"]["spec"]["addr"] == f"{IPS['logins']['login2']}:22"
    assert nodes["cn001"]["metadata"]["name"] == teleport.node_id("cn001") != teleport.node_id("cn002")
    assert nodes["gpu001"]["metadata"]["labels"] == {"kind": "gpu"}
    role = docs[0]["spec"]
    assert role["allow"]["logins"] == [u["name"] for u in SIM["users"]] and role["options"]["max_session_ttl"] == "12h"


def test_fetch_ca_strips_the_prefix_and_rejects_anything_else():
    assert teleport.fetch_ca(opener=lambda url: "cert-authority ssh-ed25519 AAAA x\n") == "ssh-ed25519 AAAA x\n"
    with pytest.raises(teleport.TeleportError):
        teleport.fetch_ca(opener=lambda url: "<html>")


def test_compose_adds_teleport_only_when_selected():
    prof = compose.load_profile("min")
    assert "teleport" not in compose.generate({**SIM, "login": None}, prof)["services"]
    svcs = compose.generate(SIM, {**prof, "groups": [*prof["groups"], "teleport"]})["services"]
    t = svcs["teleport"]
    assert t["networks"]["default"]["aliases"] == ["teleport.workbench.test"]  # the name resolves inside the cluster; the IP is its address
    assert t["ports"] == ["127.0.0.1:3080:3080", "127.0.0.1:3434:3434"] and "build" not in t and t["mem_limit"] == "256m"
    assert t["labels"]["workbench.class"] == "P2" and "command" not in t  # the image entrypoint starts it
    assert "privileged" not in t and "cap_add" not in t and t["networks"]["default"]["ipv4_address"].endswith(".255.30")
    mount = f"./secrets/teleport:{teleport.CONTAINER_SECRETS}:ro"
    assert mount in svcs["login"]["volumes"] and mount in svcs["cn001"]["volumes"] and mount not in t["volumes"]


def test_teleport_is_an_optional_part_that_is_off_by_default():
    assert "teleport" in workbench.PARTS and "teleport" not in workbench.DEFAULT_PARTS
    _, prof = workbench.effective(SIM, workbench.Spec(parts=[*workbench.DEFAULT_PARTS, "teleport"]))
    assert "teleport" in prof["groups"]
    assert "teleport" not in workbench.effective(SIM, workbench.Spec())[1]["groups"]  # a whole-group part is left out, not dropped


def test_prepare_writes_files_and_directories(tmp_path):
    teleport.prepare(SIM, IPS, tmp_path / "conf", tmp_path / "sec")
    assert (tmp_path / "conf" / "data").is_dir() and (tmp_path / "sec" / "hosts").is_dir()
    assert yaml.safe_load((tmp_path / "conf" / "teleport.yaml").read_text())["version"] == "v3"
    assert "kind: node" in (tmp_path / "conf" / "resources.yaml").read_text()


def test_prepare_drops_certificates_signed_by_a_cluster_that_no_longer_exists(tmp_path):
    conf, sec = tmp_path / "conf", tmp_path / "sec"
    (sec / "hosts").mkdir(parents=True)
    (sec / "hosts" / "cn001-cert.pub").write_text("old")
    (sec / "openssh_ca.pub").write_text("old")
    (conf / "data").mkdir(parents=True)
    (conf / "data" / "proc").write_text("state")  # data exists: the certificates still belong to it
    teleport.prepare(SIM, IPS, conf, sec)
    assert (sec / "hosts" / "cn001-cert.pub").is_file()
    (conf / "data" / "proc").unlink()  # data wiped: new CA on the next start
    teleport.prepare(SIM, IPS, conf, sec)
    assert not (sec / "hosts" / "cn001-cert.pub").exists() and not (sec / "openssh_ca.pub").exists()


def run_bootstrap(tmp_path, runner, **kw):
    msgs = []
    ok = teleport.bootstrap(SIM, IPS, runner, stack.dc, msgs.append, secrets=tmp_path,
                            fetch=kw.pop("fetch", lambda: "ssh-ed25519 AAAA\n"), sleep=lambda s: None, **kw)
    return ok, msgs


def test_bootstrap_loads_resources_signs_hosts_and_identities(tmp_path):
    (tmp_path / "hosts").mkdir()
    (tmp_path / "hosts" / "cn001-cert.pub").write_text("kept")  # already signed: not signed again
    calls = []

    def runner(cmd):
        calls.append(cmd[cmd.index("tctl") + 1:])
        return cp()

    ok, msgs = run_bootstrap(tmp_path, runner)
    assert ok and not msgs and (tmp_path / "openssh_ca.pub").read_text() == "ssh-ed25519 AAAA\n"
    signs = [c for c in calls if c[:2] == ["auth", "sign"]]
    hosts = [c for c in signs if "--format=openssh" in c]
    assert len(hosts) == len(teleport.targets(SIM, IPS)) - 1 and not any("cn001," in c[2] for c in hosts)
    assert any(f"--host={addressing.LOGIN_IP},login,{teleport.node_id('login')}" == c[2] for c in hosts)
    ids = [c for c in signs if c[2].startswith("--user=")]
    assert [c[2] for c in ids] == [f"--user={u['name']}" for u in SIM["users"]] and "--ttl=12h" in ids[0]
    assert calls[0] == ["status"] and ["create", "-f", "/etc/teleport/resources.yaml"] in calls
    assert not any(c[0] == "create" and "user-" in c[-1] for c in calls)  # every user exists (get succeeded): none is touched, so no password is lost
    assert [c for c in calls if c[0] == "get" and c[1] != "nodes"] == [["get", f"user/{u['name']}"] for u in SIM["users"]]


def test_bootstrap_says_why_it_failed(tmp_path):
    ok, msgs = run_bootstrap(tmp_path, lambda c: cp(1) if "status" in c else cp(), )
    assert not ok and "did not become ready" in msgs[0]
    ok, msgs = run_bootstrap(tmp_path, lambda c: cp(1, err="bad yaml") if "create" in c else cp())
    assert not ok and "resources: bad yaml" in msgs[0]
    ok, msgs = run_bootstrap(tmp_path, lambda c: cp(), fetch=lambda: (_ for _ in ()).throw(teleport.TeleportError("no ca")))
    assert not ok and "CA key: no ca" in msgs[0]
    ok, msgs = run_bootstrap(tmp_path, lambda c: cp(1, err="denied") if "--format=openssh" in c else cp())
    assert not ok and "host certificate for login: denied" in msgs[0]
    ok, msgs = run_bootstrap(tmp_path, lambda c: cp(1, err="no user") if "--format=file" in c else cp())
    assert not ok and "identity for alice: no user" in msgs[0]


def test_invite_returns_the_link_or_says_why_not():
    url = "https://10.141.255.30:3080/web/invite/abc123"
    assert teleport.invite("alice", lambda c: cp(out=f"User alice has been reset. Share this URL:\n{url}\n"), stack.dc) == url
    with pytest.raises(teleport.TeleportError, match="alice"):
        teleport.invite("alice", lambda c: cp(1, err="container is not running"), stack.dc)


def test_up_bootstraps_teleport_before_the_nodes_start(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for d in ("profiles", "components", "images"):
        shutil.copytree(ROOT / d, tmp_path / d, dirs_exist_ok=True)
    started, order = [], []

    def runner(cmd):
        if "tctl" in cmd:
            order.append(("tctl", list(started)))
        if "up" in cmd:
            started.extend(cmd[cmd.index("--no-deps") + 1:])
        return cp(out="\n".join(started)) if "ps" in cmd else cp()

    monkeypatch.setattr(stack, "governor_admits", lambda n, say: True)
    monkeypatch.setattr(stack, "provision_token", lambda **kw: kw.get("path", tmp_path / "t"))
    monkeypatch.setattr(teleport, "fetch_ca", lambda *a, **k: "ssh-ed25519 AAAA\n")
    sim, prof = workbench.effective(SIM, workbench.Spec(parts=["teleport"], cpu_nodes=1, gpu_nodes=0))
    msgs = []
    assert stack.up(sim, "workbench", runner=runner, say=msgs.append, available=12 * 1024**3, wait=1, prof=prof)
    assert order and all("cn001" not in seen and "login" not in seen and "teleport" in seen for _, seen in order)
    assert (tmp_path / "compose/generated/teleport/teleport.yaml").is_file()
    assert any(m.startswith("teleport: https://10.141.255.30:3080") and "teleport.workbench.test" in m for m in msgs) and any("/etc/hosts" in m for m in msgs)


def test_bootstrap_creates_a_missing_user_but_never_replaces_an_existing_one(tmp_path):
    (tmp_path / "hosts").mkdir()
    calls = []

    def runner(cmd):
        c = cmd[cmd.index("tctl") + 1:]
        calls.append(c)
        return cp(1) if c[:2] == ["get", "user/bob"] else cp()   # bob is new, the others exist

    ok, msgs = run_bootstrap(tmp_path, runner)
    creates = [c for c in calls if c[0] == "create" and c[1] != "-f"]
    assert ok and creates == [["create", "/etc/teleport/user-bob.yaml"]] and "-f" not in creates[0]
    assert "kind: user" in teleport.user_resource("bob") and teleport.ROLE in teleport.user_resource("bob")
    ok, msgs = run_bootstrap(tmp_path, lambda c: cp(1, err="denied") if "create" in c and "user-" in c[-1] or "get" in c else cp())
    assert not ok and "teleport user" in msgs[0]


def test_prepare_writes_one_file_per_user(tmp_path):
    teleport.prepare(SIM, IPS, tmp_path / "conf", tmp_path / "sec")
    for u in SIM["users"]:
        assert f"name: {u['name']}" in (tmp_path / "conf" / f"user-{u['name']}.yaml").read_text()


def node_json(*names, kind="openssh"):
    return json.dumps([{"kind": "node", "sub_kind": kind, "metadata": {"name": teleport.node_id(n)}, "spec": {"hostname": n}} for n in names])


def test_prune_forgets_nodes_the_workbench_no_longer_has_and_their_certificates(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.mkdir()
    for n in ("login", "login3", "cn001"):
        (hosts / f"{n}-cert.pub").write_text("c")
        (hosts / n).write_text("k")
    calls = []

    def runner(cmd):
        c = cmd[cmd.index("tctl") + 1:]
        calls.append(c)
        if c[:2] == ["get", "nodes"]:
            return cp(out=node_json("login", "login3", "cn001", "cn999", "somebody-elses", kind="openssh"))
        return cp()

    sim = {**SIM, "login": ["both"]}                                   # only `login`; login3 was dropped, cn999 is not in the cluster
    gone = teleport.prune(sim, addressing.plan(sim, 1), runner, stack.dc, tmp_path)
    assert sorted(gone) == ["cn999", "login3"]                           # a name this tool does not register is never touched
    assert [c for c in calls if c[0] == "rm"] == [["rm", f"node/{teleport.node_id(n)}"] for n in ("login3", "cn999")]
    assert not (hosts / "login3-cert.pub").exists() and not (hosts / "login3").exists()      # its certificate and key go too
    assert (hosts / "login-cert.pub").exists() and (hosts / "cn001-cert.pub").exists()


def test_prune_never_fails_up_on_an_unreadable_list(tmp_path):
    sim = {**SIM, "login": ["both"]}
    for out, code in (("not json", 0), ("", 0), ("[]", 1), ('{"a": 1}', 0)):
        assert teleport.prune(sim, addressing.plan(sim, 1), lambda c, o=out, k=code: cp(k, out=o), stack.dc, tmp_path) == []


def test_bootstrap_says_what_it_forgot(tmp_path):
    (tmp_path / "hosts").mkdir()
    ok, msgs = run_bootstrap(tmp_path, lambda c: cp(out=node_json("login9")) if "nodes" in c else cp())
    assert ok and msgs == ["teleport: forgot login9 (no longer in the workbench)"]
