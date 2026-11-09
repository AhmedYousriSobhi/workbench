"""The controller node and the jump host, for real (issues 415, 416): whitelisted ssh to the controller, the BCM head node's cmsh
there and not on the login node, and `ssh -J` through a bare bastion. Not run by the agent: `verify.sh --unit access --clean`."""
import os
import subprocess
from pathlib import Path

import pytest
from helpers import wait_for

from benchctl import stack, workbench

ROOT = Path(__file__).resolve().parents[2]
KEY = ROOT / stack.SSH_DIR / "id_ed25519"
OPTS = ["-i", str(KEY), "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-o", "BatchMode=yes", "-o", "LogLevel=ERROR"]


def benchctl(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / "scripts/benchctl"), *args], capture_output=True, text=True, timeout=timeout, check=False)


def ssh(user: str, host: str, port: int, command: str, jump: tuple[str, int] | None = None) -> subprocess.CompletedProcess:
    # `-J` does not pass -i or -o to the jump leg (ssh(1)): the documented form is a ProxyCommand that carries them
    j = ["-o", f"ProxyCommand=ssh {' '.join(OPTS)} -W %h:%p -p {jump[1]} alice@{jump[0]}"] if jump else []
    return subprocess.run(["ssh", *OPTS, *j, "-p", str(port), f"{user}@{host}", command], capture_output=True, text=True, timeout=40, check=False)


@pytest.fixture(scope="module")
def built():
    os.chdir(ROOT)
    out = benchctl("up", "--cpu-nodes", "1", "--gpu-nodes", "0", "--controller", "--access", "jump", "--with", "bcm")
    assert out.returncode == 0, out.stdout + out.stderr
    assert wait_for(lambda: ssh("alice", "127.0.0.1", 2221, "true").returncode == 0, timeout=90)
    yield
    stack.down(say=lambda m: None)
    workbench.forget()


def test_an_admin_gets_in_and_slurmctld_runs_there(built):
    r = ssh("alice", "127.0.0.1", 2221, "scontrol ping; pgrep -x slurmctld")
    assert r.returncode == 0 and "UP" in r.stdout, r.stdout + r.stderr


def test_a_user_outside_the_whitelist_is_refused(built):
    assert ssh("bob", "127.0.0.1", 2221, "true").returncode != 0


def test_cmsh_is_on_the_controller_and_not_on_the_login_node(built):
    assert "HeadNode" in ssh("alice", "127.0.0.1", 2221, "bash -lc 'cmsh -c \"device; list\"'").stdout
    assert ssh("alice", "127.0.0.1", 2222, "command -v cmsh").returncode != 0


def test_ssh_through_the_bare_jump_host_reaches_the_login_node_and_the_controller(built):
    jump = ("127.0.0.1", 2220)
    assert ssh("alice", "login", 22, "hostname", jump=jump).stdout.strip() == "login"
    assert ssh("alice", "controller", 22, "hostname", jump=jump).returncode == 0
    bare = ssh("alice", "127.0.0.1", 2220, "command -v squeue sinfo scontrol python3")
    assert bare.returncode != 0 and bare.stdout.strip() == ""  # nothing installed on the bastion
