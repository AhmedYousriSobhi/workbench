"""The generator is deterministic and needs no network or clock (issue 642)."""
import ast
from pathlib import Path

from palette_lib import PAL, ROOT, mp


def test_two_runs_give_byte_identical_output():
    assert mp.render_tokens(PAL) == mp.render_tokens(mp.load())
    assert mp.render_tokens(PAL).encode() == mp.render_tokens(mp.load()).encode()


def test_the_generator_imports_only_the_standard_library_and_yaml():
    tree = ast.parse(Path(ROOT / "scripts/make_palette.py").read_text())
    mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= {"difflib", "json", "math", "re", "sys", "pathlib", "yaml"}, mods
    assert not {"time", "datetime", "random", "urllib", "socket"} & mods
