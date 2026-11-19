"""The tutorials (docs/tutorials, issue 936): every page is in the index, every link resolves, and every benchctl command it shows exists."""
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchctl.cli import app

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "docs/tutorials"
PAGES = sorted(p for p in DIR.glob("*.md") if p.name != "README.md")
SECTIONS = ("You will:", "## Steps", "## You should see", "## Next")


def test_there_are_tutorials_and_each_is_in_the_index():
    index = (DIR / "README.md").read_text()
    assert len(PAGES) >= 10
    for p in PAGES:
        assert f"]({p.name})" in index, p.name


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_each_tutorial_has_its_parts_and_its_links_resolve(page):
    text = page.read_text()
    for s in SECTIONS:
        assert s in text, (page.name, s)
    for target in re.findall(r"\]\(([^)#]+\.md)(?:#[^)]*)?\)", text):
        assert (page.parent / target).resolve().is_file(), (page.name, target)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_benchctl_command_shown_exists(page):
    """The verb of every `benchctl VERB ...` line in a code block answers --help."""
    runner = CliRunner()
    code = "\n".join(re.findall(r"```bash\n(.*?)```", page.read_text(), re.DOTALL))
    for line in code.splitlines():
        m = re.search(r"(?:\./scripts/)?benchctl ((?:[a-z][\w-]*)(?: [a-z][\w-]*)?)", line)
        if not m or line.lstrip().startswith("#"):
            continue
        verb = m.group(1).split()[0]
        assert runner.invoke(app, [verb, "--help"]).exit_code == 0, (page.name, line)
