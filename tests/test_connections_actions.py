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
    assert by["login"]["actions"][0]["value"].startswith("ssh://") and ":2222" in by["login"]["actions"][0]["value"]
    jump = by["nodes"]["actions"][0]
    assert jump["kind"] == "copy" and jump["value"] == "benchctl ssh alice@<node>"


def test_a_part_with_no_localhost_port_has_no_link():
    assert [a for e in rows() if e["id"] == "bcm" for a in e["actions"]] == []
