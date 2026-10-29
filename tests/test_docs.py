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


def test_markdown_relative_links_resolve():
    """The README and docs link to each other; a moved page must not leave a dead link."""
    files = [ROOT / "README.md", ROOT / "SPEC.md", ROOT / "CLAUDE.md",
             *sorted((ROOT / "docs").rglob("*.md"))]
    bad = []
    for f in files:
        for target in re.findall(r"\]\(([^)\s]+)\)", f.read_text()):
            if re.match(r"(https?:|mailto:|#)", target):
                continue
            if not (f.parent / target.split("#")[0]).exists():
                bad.append(f"{f.relative_to(ROOT)} -> {target}")
    assert not bad, bad


def test_readme_stays_short_and_architecture_is_split():
    assert len((ROOT / "README.md").read_text().splitlines()) <= 120
    pages = sorted((ROOT / "docs/architecture").glob("*.md"))
    assert len(pages) >= 10
    index = (ROOT / "docs/architecture/README.md").read_text()
    for p in pages:
        assert p.name == "README.md" or p.name in index, p.name


def test_readme_build_scopes_use_real_commands():
    text = (ROOT / "README.md").read_text()
    runner = CliRunner()
    for c in set(re.findall(r"simctl (check|doctor|preflight|up|down|scale|scenario)\b", text)):
        assert runner.invoke(app, [c, "--help"]).exit_code == 0, c
    for s in set(re.findall(r"\./(scripts/[\w.-]+)", text)):
        assert (ROOT / s).is_file(), s
