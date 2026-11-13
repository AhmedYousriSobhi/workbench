"""The unit-test guard in tests/conftest.py (#473): host tools never run for real; docker, virsh and ssh-keygen have stand-ins."""
import subprocess

import pytest


def test_docker_answers_like_a_stopped_daemon():
    r = subprocess.run(["docker", "ps"], capture_output=True, text=True, check=False)
    assert r.returncode == 1 and "Cannot connect to the Docker daemon" in r.stderr


def test_virsh_answers_like_no_libvirt():
    r = subprocess.run(["virsh", "list"], capture_output=True, text=True, check=False)
    assert r.returncode == 1 and "failed to connect" in r.stderr


def test_ssh_keygen_writes_placeholder_keys(tmp_path):
    key = tmp_path / "k"
    r = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "c", "-f", str(key)], capture_output=True, text=True, check=False)
    assert r.returncode == 0
    assert (key.stat().st_mode & 0o777) == 0o600
    assert (tmp_path / "k.pub").read_text().startswith("ssh-ed25519 ") and "STUB" in key.read_text()


def test_a_command_line_string_is_redirected_too():
    r = subprocess.run("docker ps", shell=True, capture_output=True, text=True, check=False)
    assert r.returncode == 1 and "Cannot connect" in r.stderr


@pytest.mark.parametrize("tool", ["systemctl", "systemd-run", "kubectl", "tc", "sudo"])
def test_other_host_tools_fail_loudly(tool):
    with pytest.raises(RuntimeError, match="hermetic guard"):
        subprocess.run([tool, "x"], capture_output=True, check=False)


def test_ordinary_tools_still_run():
    assert subprocess.run(["bash", "-c", "exit 3"], check=False).returncode == 3
