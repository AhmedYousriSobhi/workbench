"""The GUI-access registry (#273): every action of a Connections row is a known kind, a safe scheme and holds no secret."""
import re
from pathlib import Path

import yaml

from webui import connections

SIM = yaml.safe_load(Path("sim.yaml").read_text())


def rows(sim=SIM):
    return connections.entities(sim)


def test_every_action_is_valid_and_has_a_text_label():
    acts = [(e["id"], a) for e in rows() for a in e["actions"]]
    assert acts
    for rid, a in acts:
        assert connections.valid_action(a), (rid, a)


def test_links_point_to_localhost_never_everywhere():
    for e in rows():
        for a in e["actions"]:
            if a["kind"] in ("link", "uri"):
                assert "127.0.0.1" in a["value"] and "0.0.0.0" not in a["value"], a


def test_no_action_carries_a_secret():
    secret = re.compile(r"(password|token)=|admin:[^<\s]|Bearer ", re.IGNORECASE)
    for e in rows():
        for a in e["actions"]:
            assert not secret.search(a["value"]), a


def test_validator_refuses_bad_actions():
    bad = [{"kind": "link", "label": "x", "value": "javascript:alert(1)"},
           {"kind": "link", "label": "x", "value": "http://0.0.0.0:80"},
           {"kind": "link", "label": "x", "value": "https://user:pw@127.0.0.1"},
           {"kind": "uri", "label": "x", "value": "http://127.0.0.1"},
           {"kind": "exec", "label": "x", "value": "rm"}, {"kind": "copy", "label": "", "value": "ls"}]
    assert not any(connections.valid_action(a) for a in bad)


def test_a_direct_login_gets_an_ssh_uri_and_the_nodes_a_jump_command():
    by = {e["id"]: e for e in rows()}
    copy_, uri = by["login"]["actions"]  # Copy first: it works everywhere (#281)
    assert copy_["kind"] == "copy" and copy_["value"] == "benchctl ssh alice@login"
    assert uri["kind"] == "uri" and uri["value"].startswith("ssh://") and ":2222" in uri["value"]
    assert "needs a handler" in uri["label"]
    jump = by["nodes"]["actions"][0]
    assert jump["kind"] == "copy" and jump["value"] == "benchctl ssh alice@<node>"


def test_a_part_with_no_localhost_port_has_no_link():
    assert [a for e in rows() if e["id"] == "bcm" for a in e["actions"]] == []


def test_api_links_say_they_open_json_and_the_bmc_goes_to_the_redfish_root():
    by = {e["id"]: e for e in rows()}
    for part in ("jira", "bmc"):
        assert "API (JSON" in by[part]["actions"][0]["label"], part
    assert by["bmc"]["actions"][0]["value"].endswith("/redfish/v1")


def test_the_real_jira_has_a_row_with_a_link_and_only_the_path_of_its_secrets():
    by = {e["id"]: e for e in rows()}
    row = by["jira-real"]
    assert row["actions"][0]["kind"] == "link" and row["actions"][0]["value"] == "http://127.0.0.1:8083"
    assert row["credential"].endswith("jira-real.env") and "PAT_" not in str(row)


def test_the_real_jira_row_offers_a_post_button_for_one_known_path_only():
    post = [a for e in rows() if e["id"] == "jira-real" for a in e["actions"] if a["kind"] == "post"]
    assert post == [{"kind": "post", "label": "Refresh licence (restarts Jira)", "value": "/api/jira-real/refresh"}]
    assert connections.valid_action(post[0])
    assert not connections.valid_action({"kind": "post", "label": "x", "value": "/api/anything"})
