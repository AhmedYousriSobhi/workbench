"""scripts/install-benchctl.sh and the wrapper's missing-venv message (issue 924)."""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "scripts" / "install-benchctl.sh"


def run(*args, bindir):
    env = {**os.environ, "BENCHCTL_BIN_DIR": str(bindir), "HOME": str(bindir)}
    return subprocess.run([str(INSTALL), *args], capture_output=True, text=True, env=env, check=False)


def test_install_links_the_wrapper_and_is_idempotent(tmp_path):
    bindir = tmp_path / "bin"
    r = run(bindir=bindir)
    assert r.returncode == 0 and "installed" in r.stdout
    assert (bindir / "benchctl").resolve() == REPO / "scripts" / "benchctl"
    assert "already installed" in run(bindir=bindir).stdout
    assert "removed" in run("--uninstall", bindir=bindir).stdout
    assert not (bindir / "benchctl").exists()


def test_install_never_replaces_a_foreign_file(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "benchctl").write_text("mine")
    r = run(bindir=bindir)
    assert r.returncode == 1 and "refusing" in r.stderr
    assert (bindir / "benchctl").read_text() == "mine"
    assert "nothing of ours" in run("--uninstall", bindir=bindir).stdout


def test_wrapper_without_a_venv_names_the_command_and_exits_1(tmp_path):
    (tmp_path / "scripts").mkdir()
    wrapper = tmp_path / "scripts" / "benchctl"
    wrapper.write_text((REPO / "scripts" / "benchctl").read_text())
    wrapper.chmod(0o755)
    r = subprocess.run([str(wrapper), "status"], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False)
    assert r.returncode == 1 and "no virtualenv" in r.stderr and "python3 -m venv" in r.stderr
