import re
from pathlib import Path

from typer.testing import CliRunner

from benchctl.cli import app

ROOT = Path(__file__).resolve().parent.parent


def test_getting_started_commands_exist():
    text = (ROOT / "docs/guides/getting-started.md").read_text()
    runner = CliRunner()
    cmds = set(re.findall(r"(?:\./scripts/benchctl|benchctl) ([a-z][\w-]*)", text))
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
    assert "WORKBENCH_GRAFANA_TOKEN" in out and "$$(cat" in out and "sekret" not in out


def test_pyproject_declares_installable_packages():
    import tomllib
    from pathlib import Path

    cfg = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    # flat layout with many top-level dirs: setuptools refuses to guess
    assert cfg["tool"]["setuptools"]["packages"] == ["benchctl", "governor", "webui"]


def test_benchctl_wrapper_explains_a_missing_venv(tmp_path):
    import shutil
    import subprocess

    (tmp_path / "scripts").mkdir()
    shutil.copy(Path(__file__).parent.parent / "scripts" / "benchctl", tmp_path / "scripts" / "benchctl")
    r = subprocess.run([str(tmp_path / "scripts" / "benchctl"), "status"], capture_output=True, text=True,
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
    for c in set(re.findall(r"benchctl (check|doctor|preflight|up|down|scale|scenario)\b", text)):
        assert runner.invoke(app, [c, "--help"]).exit_code == 0, c
    for s in set(re.findall(r"\./(scripts/[\w.-]+)", text)):
        assert (ROOT / s).is_file(), s


MANUAL = ROOT / "docs/manual"


def test_manual_has_an_index_that_lists_every_chapter():
    chapters = sorted(p.name for p in MANUAL.glob("[0-9][0-9]-*.md"))
    assert len(chapters) >= 10
    index = (MANUAL / "README.md").read_text()
    for name in chapters:
        assert name in index, name


def test_manual_commands_and_scripts_exist():
    text = "\n".join(p.read_text() for p in MANUAL.glob("*.md"))
    runner = CliRunner()
    cmds = set(re.findall(r"(?:benchctl\.cli|\./scripts/benchctl|benchctl) (check|doctor|preflight|status|diag|compose|up|down|"
                          r"scale|scenario|measure|get|exec|logs|start|stop|open|ssh-key)\b", text))
    assert {"up", "down", "scale", "scenario", "status", "diag", "measure"} <= cmds
    for c in cmds:
        assert runner.invoke(app, [c, "--help"]).exit_code == 0, c
    for s in set(re.findall(r"\./(scripts/[\w.-]+)", text)):
        assert (ROOT / s).is_file(), s


def test_manual_node_names_match_the_nodes_sim_yaml_generates():
    """The shell examples use workbench-cn001-1 and workbench-gpu001-1: they must be real node names."""
    import yaml

    from benchctl.nodes import node_names

    names = {n for n, _ in node_names(yaml.safe_load((ROOT / "sim.yaml").read_text()))}
    text = (MANUAL / "04-nodes-and-shells.md").read_text()
    for used in set(re.findall(r"workbench-((?:cn|gpu)\d+)-1", text)):
        assert used in names, used


def test_getting_started_explains_how_to_get_a_shell_on_a_node():
    text = (ROOT / "docs/guides/getting-started.md").read_text()
    assert "docker exec -it workbench-cn001-1 bash" in text and "ssh cn001 hostname" in text  # docker exec, and a hop through the login node


def test_manual_images_are_all_used_and_have_alt_text():
    text = "\n".join(p.read_text() for p in MANUAL.glob("*.md"))
    used = set(re.findall(r"\]\(img/([\w.-]+\.png)\)", text))
    assert used == {p.name for p in (MANUAL / "img").glob("*.png")}
    assert not re.findall(r"!\[\]\(", text)


def test_no_old_names_left():
    """simctl, opti and hpcsim were removed (#111): a tracked file may name them only to explain the migration."""
    import subprocess

    allowed = {"docs/manual/09-troubleshooting.md", "docs/adr/0015-one-controller-command-benchctl.md",
               "scripts/install-governor.sh", "tests/test_docs.py",}
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    bad = []
    for f in files:
        if f in allowed or f.endswith((".png", ".svg", ".ico", ".gz", ".pdf")):
            continue
        text = (ROOT / f).read_text(errors="ignore")
        if re.search(r"simctl|hpcsim|OPTI_|\bopti\b", text, re.IGNORECASE):
            bad.append(f)
    assert not bad, bad


def test_verbs_guide_covers_every_command():
    """A new verb must come with its limits: the guide names every command the CLI registers."""
    import typer.main

    text = (ROOT / "docs/guides/benchctl-verbs.md").read_text()
    group = typer.main.get_command(app)
    missing = [name for name in group.commands if f"`{name}" not in text]
    assert not missing, f"add these verbs to docs/guides/benchctl-verbs.md: {missing}"
