import re
from pathlib import Path

from typer.testing import CliRunner

from simctl.cli import app

ROOT = Path(__file__).resolve().parent.parent


def test_getting_started_commands_exist():
    text = (ROOT / "docs/guides/getting-started.md").read_text()
    runner = CliRunner()
    cmds = set(re.findall(r"python -m simctl\.cli (\w+)", text))
    assert {"check", "doctor", "up", "scale", "down", "status", "diag", "preflight"} <= cmds
    for c in cmds:
        assert runner.invoke(app, [c, "--help"]).exit_code == 0, c


def test_documented_scripts_exist():
    text = (ROOT / "docs/guides/getting-started.md").read_text()
    for s in set(re.findall(r"\./(scripts/[\w.-]+)", text)):
        assert (ROOT / s).is_file(), s


def test_install_script_prints_a_unit_pointing_at_this_checkout():
    import subprocess
    out = subprocess.run([str(ROOT / "scripts/install-governor.sh"), "--print"], capture_output=True,
                         text=True, check=True).stdout
    assert f"WorkingDirectory={ROOT}" in out and f"--workdir {ROOT}" in out and "--require-lock" in out
    assert "HPCSIM_GRAFANA_TOKEN" in out and "$$(cat" in out and "sekret" not in out


def test_pyproject_declares_installable_packages():
    import tomllib
    from pathlib import Path

    cfg = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    # flat layout with many top-level dirs: setuptools refuses to guess
    assert cfg["tool"]["setuptools"]["packages"] == ["simctl", "governor", "webui"]


def test_simctl_wrapper_explains_a_missing_venv(tmp_path):
    import shutil
    import subprocess

    (tmp_path / "scripts").mkdir()
    shutil.copy(Path(__file__).parent.parent / "scripts" / "simctl", tmp_path / "scripts" / "simctl")
    r = subprocess.run([str(tmp_path / "scripts" / "simctl"), "status"], capture_output=True, text=True,
                       check=False)
    assert r.returncode == 1 and "python3 -m venv .venv" in r.stderr
