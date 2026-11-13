"""AGENTS.md, the recipes and llms.txt (SPEC AG-1, AG-5; ADR 0060, 0063, 0068): length, headings, current generated blocks, and
every command they show exists in the CLI. Hermetic."""
import re
from pathlib import Path

import pytest

from benchctl import agents

ROOT = Path(__file__).resolve().parent.parent
RECIPES = ROOT / "docs" / "agents" / "recipes"
NAMES = ["connect-your-app", "gpu-fault-to-alert", "drain-to-ticket", "node-down", "api-errors-and-retries", "ssh-failures",
         "kubernetes-node-notready", "what-did-my-app-call", "write-a-scenario", "load-and-memory"]
HEADINGS = ["Goal", "Needs", "Steps", "What your app should show", "Assert", "Clean up", "Memory cost"]


@pytest.fixture(autouse=True)
def in_repo(monkeypatch):
    monkeypatch.chdir(ROOT)


def commands(text: str) -> list[str]:
    """The benchctl command lines inside code fences, pipes and trailing comments cut."""
    out = []
    for block in re.findall(r"```(?:bash)?\n(.*?)```", text, re.DOTALL):
        for line in block.splitlines():
            line = line.split("#")[0].split("|")[0].strip()
            m = re.match(r"(?:\./scripts/)?benchctl\s+(.*)", line)
            if m:
                out.append(m.group(1))
    return out


def check_command(line: str) -> None:
    words = line.split()
    path = []
    for w in words:
        if w.startswith("-"):
            break
        probe = " ".join([*path, w])
        if agents.command_at(probe) is None:
            break
        path.append(w)
    cmd = agents.command_at(" ".join(path)) if path else None
    assert cmd is not None and not getattr(cmd, "commands", None), f"not a benchctl command: {line!r}"
    known = {opt for p in cmd.params for opt in [*p.opts, *p.secondary_opts]}
    for w in words:
        if w.startswith("--"):
            assert w.split("=")[0] in known, f"{line!r}: {w} is not an option of `benchctl {' '.join(path)}`"


def test_agents_md_is_short_and_points_at_claude_md():
    text = (ROOT / "AGENTS.md").read_text()
    assert agents.tokens(text) <= agents.MAX_TOKENS, f"{agents.tokens(text)} tokens"
    assert "CLAUDE.md" in text.splitlines()[2]
    assert "AGENTS.md" in (ROOT / "CLAUDE.md").read_text().splitlines()[2], "CLAUDE.md opens with a pointer to AGENTS.md"


def test_the_generated_blocks_are_current():
    assert agents.main(["--check"]) == 0, "run: python -m benchctl.agents"


def test_every_listed_verb_exists():
    assert all(agents.command_at(v) is not None for v in agents.VERBS)


def test_every_command_in_agents_md_and_the_recipes_exists():
    files = [ROOT / "AGENTS.md", *sorted(RECIPES.glob("*.md"))]
    seen = 0
    for f in files:
        for line in commands(f.read_text()):
            check_command(line)
            seen += 1
    assert seen > 40


def test_the_recipes_are_the_agreed_set_with_the_fixed_headings_and_length():
    assert sorted(p.stem for p in RECIPES.glob("*.md")) == sorted(NAMES)
    for name in NAMES:
        text = (RECIPES / f"{name}.md").read_text()
        assert len(text.splitlines()) <= 60, name
        assert re.findall(r"^## (.+)$", text, re.MULTILINE) == HEADINGS, name
        assert text.startswith(f"# Recipe: {name}\n"), name


def test_llms_txt_links_exist():
    text = (ROOT / "llms.txt").read_text()
    links = re.findall(r"\]\(([^)]+)\)", text)
    assert len(links) >= 9
    for link in links:
        assert (ROOT / link).exists(), link
    assert "AGENTS.md" in text


def test_agent_docs_hold_no_private_path_or_client_name():
    for f in [ROOT / "AGENTS.md", ROOT / "llms.txt", *RECIPES.glob("*.md")]:
        assert not re.search(r"/home/[a-z_]", f.read_text()), f
