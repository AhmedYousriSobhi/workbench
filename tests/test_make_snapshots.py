"""Code snapshots (issue 797): real output only, masked, rendered from the palette, readable in both themes, used where promised."""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_snapshots", ROOT / "scripts/make_snapshots.py")
ms = importlib.util.module_from_spec(spec)
sys.modules["make_snapshots"] = ms
spec.loader.exec_module(ms)
sys.path.insert(0, str(ROOT / "scripts"))
import make_palette as mp


def test_check_passes_on_the_repository():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/make_snapshots.py"), "--check"], check=False, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_text_colours_meet_contrast_in_both_themes():
    for theme in ms.THEMES:
        c = ms.colours(theme)
        for fg, bg in ms.TEXT_ROLES.items():
            assert mp.contrast(c[fg], c[bg]) >= 4.5, (theme, fg)


def test_mask_hides_home_user_and_host(monkeypatch):
    monkeypatch.setattr(ms.getpass, "getuser", lambda: "jdoe")
    monkeypatch.setattr(ms.socket, "gethostname", lambda: "box17")
    out = ms.mask(f"\x1b[1m{ms.ROOT}/x\x1b[0m {Path.home()}/y jdoe@box17 jdoes")
    assert out == "~/workbench/x /home/user/y user@host jdoes"


def test_capture_runs_the_command_in_the_repo_and_masks(monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen.update(argv=argv, cwd=kw["cwd"], env=kw["env"])
        return SimpleNamespace(stdout=f"{ms.ROOT}/scenarios\n", stderr="", returncode=0)
    assert ms.capture("plan", run=fake) == "~/workbench/scenarios\n"
    assert seen["argv"] == ["bash", "-c", ms.SNAPSHOTS["plan"].cmd] and seen["cwd"] == ms.ROOT and seen["env"]["NO_COLOR"] == "1"
    with pytest.raises(SystemExit):
        ms.capture("plan", run=lambda argv, **kw: SimpleNamespace(stdout="", stderr=" ", returncode=2))


def test_rows_mark_gaps_wrap_long_lines_and_bold_the_key(monkeypatch):
    monkeypatch.setitem(ms.SNAPSHOTS, "t", ms.Snap("echo hi", "T", "KEY", keep=((0, 2), (-1, None))))
    out = "a\nb\nc\nd\nKEY " + "w " * 60 + "\n"
    r = ms.rows("t", out)
    assert r[0] == ("cmd", "echo hi") and r[1:3] == [("out", "a"), ("out", "b")]
    assert ("note", "... (2 lines left out)") in r
    keys = [t for role, t in r if role == "key"]
    assert len(keys) == 2 and keys[0].startswith("KEY") and all(len(t) <= ms.COLS for _, t in r)   # a wrapped key line stays bold
    assert r[-1][1].startswith("  w")           # the continuation is indented
    assert ms._gap(1) == "... (1 line left out)"
    monkeypatch.setitem(ms.SNAPSHOTS, "u", ms.Snap("x", "U", "zz", keep=((0, 1),)))
    assert ms.rows("u", "a  b\nc\n")[-1] == ("note", "... (1 line left out)") and ms.rows("u", "a  b\nc\n")[1] == ("out", "a  b")
    assert ms.key_line("u", "first\nsecond\n") == "first"   # no key line: the first line stands in
    assert ms.key_line("u", "zz: done. Next: more\n") == "zz: done"


def test_cards_are_real_text_with_alt_and_no_stylesheet():
    for name in ms.SNAPSHOTS:
        text = ms.saved(name)
        for theme in ms.THEMES:
            card = (ms.OUT / f"{name}-{theme}.svg").read_text()
            assert len(card.encode()) <= 60 * 1024 and "<style" not in card and " style=" not in card   # the help's CSP allows no inline style
            assert ms.colours(theme)["card"] in card
            assert f"<desc id=\"d\">Terminal: {ms.SNAPSHOTS[name].cmd}" in card.replace("&#x27;", "'")
            assert re.search(r"<text[^>]*>[^<]{3,}</text>", card)
        assert "/home/" not in text.replace("/home/user", "") and str(Path.home()) not in text


def test_every_snapshot_is_shown_where_it_says_and_nowhere_unknown():
    for name, snap in ms.SNAPSHOTS.items():
        assert snap.used_in, name
        for page in snap.used_in:
            text = (ROOT / page).read_text()
            base = ms.posixpath.relpath("docs/assets/snapshots", str(Path(page).parent))
            assert f'srcset="{base}/{name}-dark.svg"' in text and f'src="{base}/{name}-light.svg"' in text, (name, page)
            assert f'alt="{ms.html.escape(ms.alt(name, ms.saved(name)))}"' in text, f"{page}: stale alt for {name} (--markdown {name})"
    for p in [ROOT / "README.md", *ROOT.glob("docs/**/*.md")]:
        for ref in re.findall(r"assets/snapshots/([\w-]+?)-(?:light|dark)\.svg", p.read_text()):
            assert ref in ms.SNAPSHOTS, (p, ref)


def test_markdown_block_paths_alt_and_caption():
    block = ms.markdown("plan", "docs/manual")
    assert 'srcset="../assets/snapshots/plan-dark.svg"' in block and 'src="../assets/snapshots/plan-light.svg"' in block
    assert 'alt="Terminal: ./scripts/benchctl plan' in block and block.rstrip().endswith(f"*{ms.SNAPSHOTS['plan'].title}*")
    assert 'src="docs/assets/snapshots/plan-light.svg"' in ms.markdown("plan", ".")


def test_main_renders_checks_and_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ms, "OUT", tmp_path)
    monkeypatch.setattr(ms, "SNAPSHOTS", {"t": ms.Snap("echo hi", "T", "hi")})
    assert ms.main(["--check"]) == 1 and "run --capture t" in capsys.readouterr().out
    (tmp_path / "t.txt").write_text("hi\n")
    assert ms.main(["--check"]) == 1                       # cards not rendered yet
    assert ms.main([]) == 0 and (tmp_path / "t-light.svg").is_file() and (tmp_path / "t-dark.svg").is_file()
    assert ms.main(["--check"]) == 0
    (tmp_path / "stray.svg").write_text("<svg/>")
    assert ms.main(["--check"]) == 1 and "not generated here" in capsys.readouterr().out
    assert ms.main(["--markdown", "t", "docs/guides"]) == 0 and "../assets/snapshots/t-dark.svg" in capsys.readouterr().out
    monkeypatch.setattr(ms, "capture", lambda n: "captured\n")
    assert ms.main(["--capture", "t"]) == 0 and (tmp_path / "t.txt").read_text() == "captured\n"
