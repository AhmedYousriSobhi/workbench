"""The client test worksheet (issue 946): every scenario and benchctl verb it shows exists, its links resolve, and it is linked."""
import re
from pathlib import Path

from typer.testing import CliRunner

from benchctl.cli import app

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs/guides/client-test-worksheet.md"
TEXT = PAGE.read_text()


def test_every_scenario_run_names_a_scenario_file():
    names = set(re.findall(r"scenario run ([a-z][\w-]*)", TEXT))
    assert len(names) >= 30
    for n in names:
        assert (ROOT / "scenarios" / f"{n}.yaml").is_file(), n


def test_every_scenario_row_has_the_six_columns():
    rows = [ln for ln in TEXT.splitlines() if ln.startswith("| ") and "scenario run" in ln]
    assert rows
    for ln in rows:
        assert ln.count("|") - 1 == 6 or "docker exec" in ln, ln


def test_every_benchctl_command_shown_exists():
    runner = CliRunner()
    code = "\n".join(re.findall(r"```bash\n(.*?)```", TEXT, re.DOTALL)) + "\n" + "\n".join(re.findall(r"`([^`]*benchctl [^`]*)`", TEXT))
    seen = 0
    for line in code.splitlines():
        m = re.search(r"(?:\./scripts/)?benchctl ([a-z][\w-]*)", line)
        if not m or line.lstrip().startswith("#"):
            continue
        seen += 1
        assert runner.invoke(app, [m.group(1), "--help"]).exit_code == 0, line
    assert seen >= 10


def test_links_resolve():
    for target in re.findall(r"\]\(([^)#:]+\.md)(?:#[^)]*)?\)", TEXT):
        assert (PAGE.parent / target).resolve().is_file(), target


def test_it_is_linked_from_the_guide_the_manual_and_the_tutorials():
    for rel in ("docs/guides/client-testing.md", "docs/manual/README.md", "docs/tutorials/README.md"):
        assert "client-test-worksheet.md" in (ROOT / rel).read_text(), rel


def test_no_home_path_in_the_page():
    assert "/home/" not in TEXT
