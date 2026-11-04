"""benchctl ssh [USER@]NODE (#275): the command that is run, and one plain message when it cannot be."""
import pytest

from benchctl import nodeshell

SIM = {"users": [{"name": "alice"}, {"name": "bob"}]}
KEY = "compose/generated/secrets/ssh/id_ed25519"


def test_a_bare_node_or_root_is_a_root_shell_through_docker():
    for t in ("cn001", "root@cn001"):
        assert nodeshell.argv(t, SIM, KEY) == ["docker", "exec", "-it", "workbench-cn001-1", "bash"]


def test_a_command_runs_without_a_tty_and_a_pipe_gets_no_tty():
    assert nodeshell.argv("cn001", SIM, KEY, ["id"]) == ["docker", "exec", "workbench-cn001-1", "id"]
    assert "-it" not in nodeshell.argv("cn001", SIM, KEY, tty=False)


def test_a_user_logs_in_over_ssh_through_the_first_key_login():
    a = nodeshell.argv("alice@gpu001", SIM, KEY)
    assert a[0] == "ssh" and a[-1] == "alice@gpu001" and "-t" in a
    proxy = next(x for x in a if x.startswith("ProxyCommand="))
    assert "-p 2222" in proxy and "-W %h:%p" in proxy and proxy.endswith("alice@127.0.0.1") and a[a.index("-i") + 1] == KEY


def test_a_remote_command_follows_the_host_and_takes_no_tty():
    a = nodeshell.argv("bob@cn001", SIM, KEY, ["hostname"])
    assert a[-2:] == ["bob@cn001", "hostname"] and "-t" not in a


def test_the_jump_port_follows_the_first_key_login_node():
    sim = {**SIM, "login": ["teleport", "ssh"]}
    assert "-p 2223" in " ".join(nodeshell.argv("alice@cn001", sim, KEY))


def test_each_refusal_is_one_plain_message():
    for target, why in (("carol@cn001", "no simulated user"), ("alice@CN001", "not a valid node"),
                        ("al ice@cn001", "not a valid user"), ("a/b", "not a valid node")):
        with pytest.raises(ValueError, match=why):
            nodeshell.argv(target, SIM, KEY)


def test_a_teleport_only_cluster_refuses_a_user_login_but_not_root(monkeypatch):
    monkeypatch.setattr(nodeshell.logins, "direct", lambda sim: [])
    with pytest.raises(ValueError, match="Teleport"):
        nodeshell.argv("alice@cn001", SIM, KEY)
    assert nodeshell.argv("cn001", SIM, KEY)[0] == "docker"
