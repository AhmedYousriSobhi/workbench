"""The SSH login node in the live min stack: key login, Slurm tools on a non-interactive PATH, forwarding, refusals.

This is what an SSH-based client does: connect, run squeue/sinfo, open a forwarded channel.
"""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from benchctl import compose, stack

ROOT = Path(__file__).resolve().parents[2]
KEY = ROOT / stack.SSH_DIR / "id_ed25519"


def ssh(*cmd: str, user: str = "alice", opts: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    # Host-key pinning is the client's business; the key is regenerated only when its file is deleted.
    return subprocess.run(["ssh", "-i", str(KEY), "-p", str(compose.SSH_PORT), "-o", "IdentitiesOnly=yes",
                           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                           "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", *opts, f"{user}@127.0.0.1", *cmd],
                          capture_output=True, text=True, timeout=30, check=False)


@pytest.fixture(scope="module")
def login():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    assert wait_for(lambda: ssh("true").returncode == 0, timeout=45), ssh("true").stderr
    yield sim
    stack.down(say=lambda m: None)


def test_slurm_tools_work_over_a_non_interactive_session(login):
    r = ssh("sinfo", "-h", "-o", "%N")
    assert r.returncode == 0 and "cn[001-004]" in r.stdout  # sinfo folds node names into ranges
    assert ssh("squeue", "-h").returncode == 0


def test_runs_as_the_simulated_user(login):
    assert ssh("id", "-un").stdout.strip() == "alice"


def test_root_and_unknown_users_are_refused(login):
    assert ssh("true", user="root").returncode != 0
    assert ssh("true", user="mallory").returncode != 0


def test_password_login_is_off(login):
    r = ssh("true", opts=("-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive"))
    assert r.returncode != 0


def test_forwarding_reaches_another_service(login):
    """A client opens a channel through the login node (how a client reaches a BMC); jira-mock answers behind it."""
    import socket
    import urllib.request

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(["ssh", "-N", "-i", str(KEY), "-p", str(compose.SSH_PORT), "-o", "IdentitiesOnly=yes",
                             "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                             "-o", "ExitOnForwardFailure=yes", "-L", f"{port}:jira-mock:8080", "alice@127.0.0.1"])
    try:
        def ok() -> bool:
            try:
                return urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3).status == 200
            except OSError:
                return False
        assert wait_for(ok, timeout=15)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_ssh_is_published_on_localhost_only(login):
    out = subprocess.run(["docker", "port", "workbench-login-1", "22"], capture_output=True, text=True, check=True).stdout
    assert out.strip().startswith("127.0.0.1:") and "0.0.0.0" not in out


def test_unprivileged_and_memory_limited(login):
    r = subprocess.run(["docker", "inspect", "workbench-login-1", "-f",
                        "{{.HostConfig.Privileged}} {{.HostConfig.Memory}} {{.HostConfig.CapAdd}}"],
                       capture_output=True, text=True, check=True).stdout.split()
    assert r[0] == "false" and int(r[1]) > 0 and r[2] == "[]"


def test_shell_is_bash_on_login_and_nodes(login):
    """A terminal session is a real bash: the account's login shell, not dash (#154)."""
    assert ssh("getent", "passwd", "alice").stdout.strip().endswith(":/bin/bash")
    assert ssh("ssh", "cn001", "getent", "passwd", "alice").stdout.strip().endswith(":/bin/bash")


def test_interactive_shell_has_completion_history_and_prompt(login):
    out = ssh("bash -ic 'complete -p squeue; complete -p cd; echo HIST=$HISTFILE; echo PS1=$PS1'").stdout
    assert "squeue" in out  # Slurm tab completion is registered
    assert "HIST=" in out and ".bash_history" in out
    assert "PS1=" in out and "\\u@\\h" in out


def test_non_interactive_output_is_clean(login):
    """Shell niceties must not leak into the commands a client runs."""
    assert ssh("echo", "hi").stdout == "hi\n"
    assert ssh("sinfo", "-h", "-o", "%P").stdout.split() == ["cpu*", "gpu"]
