"""The kubeconfig fixtures on the k-login (issue 767): the catalogue, the files written, the login script that installs them, the guide."""
import json
import re
import subprocess
from pathlib import Path

import yaml

from benchctl import addressing, compose, k8s, kubeconfigs

ROOT = Path(__file__).resolve().parents[1]
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
GUIDE = (ROOT / "docs/guides/client-testing.md").read_text()


def test_the_catalogue_covers_the_cases_the_issue_names():
    f = kubeconfigs.BY_NAME
    assert f["loose-world.yaml"].mode == "666" and f["loose-group.yaml"].mode == "660" and f["loose-group.yaml"].group == kubeconfigs.USERS_GROUP
    assert f["foreign-owner.yaml"].owner == "other" and f["root-owned.yaml"].owner == "root"
    assert [x.client for x in (f["loose-world.yaml"], f["loose-group.yaml"], f["foreign-owner.yaml"])] == ["refused"] * 3
    assert f["root-owned.yaml"].client == f["exec-token.yaml"].client == "accepted" and f["browser-signin.yaml"].client == "unusable"
    assert f["exec-token.yaml"].mode == f["browser-signin.yaml"].mode == "600"  # a client may only run a plugin from a private file
    assert f["browser-signin.yaml"].kubectl == "error" and all(x.kubectl == "ok" for n, x in f.items() if n != "browser-signin.yaml")
    assert "config" not in f and "limited.yaml" not in f  # never replaces the normal files


def test_prepare_writes_the_files_and_a_manifest_the_login_script_reads(tmp_path):
    ips = addressing.plan(SIM)
    k8s.prepare(SIM, ips, tmp_path / "s", tmp_path / "st")
    root = tmp_path / "s" / kubeconfigs.DIR
    token = k8s._token("viewer", tmp_path / "s")
    for rel in [f.name for f in kubeconfigs.FIXTURES] + list(kubeconfigs.SCRIPTS) + ["manifest"]:
        assert (root / rel).stat().st_mode & 0o777 == 0o600, rel
    assert root.stat().st_mode & 0o777 == 0o700
    lines = [ln.split() for ln in (root / "manifest").read_text().splitlines()]
    assert {ln[0] for ln in lines} == {f.name for f in kubeconfigs.FIXTURES} | set(kubeconfigs.SCRIPTS) and all(len(ln) == 4 for ln in lines)
    tok = yaml.safe_load((root / "loose-world.yaml").read_text())
    assert tok["users"][0]["user"]["token"] == token and tok["clusters"][0]["cluster"]["server"] == f"https://{k8s.api_ip(ips)}:6443"
    assert (root / "plugins/exec-token.sh").read_text().count(token) == 1
    ex = yaml.safe_load((root / "exec-token.yaml").read_text())["users"][0]["user"]["exec"]
    assert ex["interactiveMode"] == "Never" and ex["apiVersion"] == kubeconfigs.EXEC_API and ex["command"] == "@HOME@/.kube/plugins/exec-token.sh"
    br = yaml.safe_load((root / "browser-signin.yaml").read_text())["users"][0]["user"]["exec"]
    assert br["interactiveMode"] == "Always" and br["command"] == "@HOME@/.kube/plugins/browser-signin.sh"
    assert kubeconfigs.BROWSER_URL in (root / "plugins/browser-signin.sh").read_text()
    first = (root / "manifest").read_text()
    k8s.prepare(SIM, ips, tmp_path / "s", tmp_path / "st")  # rewritten without error, same content
    assert (root / "manifest").read_text() == first


def test_the_exec_plugin_prints_a_valid_exec_credential_and_the_browser_one_fails(tmp_path):
    script = tmp_path / "p.sh"
    script.write_text(kubeconfigs.exec_token_script("tok123"))
    out = subprocess.run(["sh", str(script)], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == {"apiVersion": kubeconfigs.EXEC_API, "kind": "ExecCredential", "status": {"token": "tok123"}}
    bad = tmp_path / "b.sh"
    bad.write_text(kubeconfigs.browser_script())
    r = subprocess.run(["sh", str(bad)], capture_output=True, text=True, check=False)
    assert r.returncode == 1 and "browser" in r.stderr and r.stdout == ""


def test_compose_mounts_the_fixture_directory_on_the_k_login_only_with_the_part():
    on = compose.generate(SIM, {"groups": ["core", "access", "k8s"]})["services"]["login"]["volumes"]
    assert any(v.startswith(f"./secrets/k8s/{kubeconfigs.DIR}:/run/workbench/k8s/{kubeconfigs.DIR}") and v.endswith(":ro") for v in on)
    off = compose.generate(SIM, {"groups": ["core", "access"]})["services"]["login"]["volumes"]
    assert not any(kubeconfigs.DIR in v for v in off)


def test_the_login_script_installs_each_manifest_line_with_its_owner_group_and_mode():
    sh = (ROOT / "images/login/login.sh").read_text()
    assert "/run/workbench/k8s/kubeconfigs" in sh and 'install -m "$mode" -o "$o" -g "$g"' in sh and "@HOME@" in sh
    assert 'install -m 600 -o "${u%%:*}" -g "${u%%:*}" /run/workbench/k8s/viewer.kubeconfig "/home/${u%%:*}/.kube/config"' in sh  # config unchanged
    assert not re.search(r">\s*/home/[^ ]*/\.kube/config", sh)


def test_the_guide_has_the_generated_table_and_names_the_documented_refusal():
    assert kubeconfigs.render_table() in GUIDE, "regenerate: python -c 'from benchctl import kubeconfigs; print(kubeconfigs.render_table())'"
    assert kubeconfigs.INTERACTIVE_ERROR in GUIDE
