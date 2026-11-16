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


def test_every_python_dependency_is_pinned_exactly():
    """Issue 855: images and Slurm are pinned, so the Python tools are too. A new dependency needs `name==X.Y.Z`."""
    import re
    import tomllib
    from pathlib import Path

    project = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())["project"]
    deps = project["dependencies"] + [d for extra in project["optional-dependencies"].values() for d in extra]
    loose = [d for d in deps if not re.fullmatch(r"[A-Za-z0-9_.-]+==\d+(\.\d+)+", d)]
    assert not loose, f"not pinned to an exact version: {loose}"


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
    assert len((ROOT / "README.md").read_text().splitlines()) <= 160
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
    # pages use the light image; the dark twin is for the README's <picture> (D-8)
    assert used == {p.name for p in (MANUAL / "img").glob("*.png") if not p.name.endswith("-dark.png")}
    assert not re.findall(r"!\[\]\(", text)


def test_screenshots_come_in_light_and_dark_pairs_within_budget():
    """doc_shots.py writes every screenshot and the two README tours as NAME-light and NAME-dark; each PNG is at most 300 KB, each GIF 3 MB."""
    img = MANUAL / "img"
    for ext in ("png", "gif"):
        light = {p.name[: -len(f"-light.{ext}")] for p in img.glob(f"*-light.{ext}")}
        dark = {p.name[: -len(f"-dark.{ext}")] for p in img.glob(f"*-dark.{ext}")}
        assert light == dark, f"{ext}: no twin for {sorted(light ^ dark)}"
    assert any(img.glob("architecture-demo-light.gif")) and any(img.glob("circuit-zoom-light.gif"))
    for p in list(MANUAL.glob("img/*.png")) + list((ROOT / "docs/guides/img").glob("*.png")):
        assert p.stat().st_size <= 300 * 1024, f"{p.name} is over 300 KB"
    for p in img.glob("*.gif"):
        assert p.stat().st_size <= 3 * 1024 * 1024, f"{p.name} is over 3 MB"


def gif_frames(data):
    """(delay in 1/100 s, encoded bytes) for every image of a GIF, and whether it loops for ever; no Pillow (CI has none)."""
    assert data[:6] in (b"GIF87a", b"GIF89a")
    flags = data[10]
    i = 13 + (3 * 2 ** ((flags & 7) + 1) if flags & 0x80 else 0)
    frames, delay, forever = [], 0, False
    while data[i] != 0x3B:
        if data[i] == 0x21:  # an extension: graphic control (the delay) or the NETSCAPE loop block
            label, j = data[i + 1], i + 2
            if label == 0xF9:
                delay = int.from_bytes(data[j + 2:j + 4], "little")
            if label == 0xFF and data[j + 1:j + 12] == b"NETSCAPE2.0":
                forever = data[j + 14:j + 16] == b"\x00\x00"
            while data[j]:
                j += data[j] + 1
            i = j + 1
        else:  # an image: descriptor, local colour table, LZW data
            assert data[i] == 0x2C, hex(data[i])
            start, f = i, data[i + 9]
            i += 11 + (3 * 2 ** ((f & 7) + 1) if f & 0x80 else 0)
            while data[i]:
                i += data[i] + 1
            i += 1
            frames.append((delay, i - start))
    return frames, forever


def test_readme_tours_are_smooth_short_loops():
    """The README's two tours (#799, scripts/demo_tour.py): at least 12 frames a second, 10 to 25 s, looping for ever."""
    for name in ("architecture-demo", "circuit-zoom"):
        for scheme in ("light", "dark"):
            frames, forever = gif_frames((MANUAL / f"img/{name}-{scheme}.gif").read_bytes())
            seconds = sum(d for d, _ in frames) / 100
            assert forever, f"{name}-{scheme} does not loop"
            assert 10 <= seconds <= 25, f"{name}-{scheme} lasts {seconds} s"
            assert len(frames) / seconds >= 12, f"{name}-{scheme}: {len(frames) / seconds:.1f} frames a second"


def test_tour_captions_and_colours():
    """One short caption per step, and the pointer, ripple and caption use the page's tokens, never a hex value (ADR 0069)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("demo_tour", ROOT / "scripts/demo_tour.py")
    tour = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tour)  # stdlib only at import time: Pillow and Playwright load inside the capture
    assert len(tour.STEPS) == 6 and len(tour.ZOOM_STEPS) == 5
    assert all(len(c) <= 58 for c in tour.STEPS + tour.ZOOM_STEPS)
    assert tour.ease(0) == 0 and tour.ease(1) == 1 and abs(tour.glide(0.5) - 0.5) < 1e-9
    for f in ("scripts/demo_tour.py", "scripts/doc_shots.py"):
        hexes = re.findall(r"#(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{3,8}\b|['\"]#\d{3,8}['\"]", (ROOT / f).read_text())  # not issue numbers
        assert not hexes, f"{f}: hex colours {hexes}; use a token"
    assert "var(--fg)" in tour.OVERLAY and "var(--accent)" in tour.OVERLAY


def test_screenshots_are_not_tall_strips():
    """No manual image is taller than 1400 px or more than 2.2 x its width (a phone screen is 390x844); a light/dark pair has one size (#701)."""
    import struct

    def dims(p):  # header only, no Pillow (CI does not install it)
        b = p.read_bytes()[:32]
        return struct.unpack(">II", b[16:24]) if b[:4] == b"\x89PNG" else struct.unpack("<HH", b[6:10])
    sizes = {}
    for p in sorted((MANUAL / "img").glob("*.png")) + sorted((MANUAL / "img").glob("*.gif")):
        w, h = dims(p)
        assert h <= 1400 and h <= 2.2 * w, f"{p.name} is {w}x{h}: capture a region or a fixed viewport"
        sizes.setdefault(p.name.rsplit("-", 1)[0], set()).add((w, h))
    assert all(len(v) == 1 for v in sizes.values()), f"light and dark differ in size: {[k for k, v in sizes.items() if len(v) > 1]}"


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


def test_readme_html_images_exist_and_have_alt_text():
    """The README uses <img> and <picture>; the link check above only sees markdown links."""
    text = (ROOT / "README.md").read_text()
    for tag in re.findall(r"<img\b[^>]*>", text):
        assert re.search(r'alt="[^"]+"', tag), tag
    paths = re.findall(r'(?:src|srcset)="(docs/[^"]+)"', text)
    assert paths
    for p in paths:
        assert (ROOT / p).is_file(), p


TEMPLATED = ("01-what-it-is", "02-install-and-start", "03-using-the-cluster", "04-nodes-and-shells", "05-web-page", "06-scenarios", "07-governor", "08-configuration", "09-troubleshooting")  # chapters rewritten to the page template (issue 596); the list grows


def test_templated_chapters_follow_the_page_template():
    for name in TEMPLATED:
        text = (MANUAL / f"{name}.md").read_text()
        assert text.startswith(f'<p align="center"><img src="../assets/art/{name}-hero.svg" alt="'), name
        assert "**In 30 seconds:**" in text and "## Do it" in text and "## Learn more" in text, name
        hood = text.split("## Under the hood", 1)[1].split("\n## ", 1)[0]
        assert re.search(r"^Source: .+", hood, re.MULTILINE), f"{name}: Under the hood needs a Source line"


GUIDES = {"memory": "memory", "nhc-checks": "nhc-checks", "topology": "topology", "qos-and-preemption": "qos-and-preemption",
          "power-and-reboot": "power-and-reboot", "slurm-resilience": "slurm-resilience", "choose-workbench": "choose-workbench",
          **{k: k for k in ("jira-real", "jira-contract", "jira-tickets", "client-setup-by-component", "client-testing", "ssh-connection-testing", "web-ui-canvas")}}  # guides in the page template (issue 605); the list grows


def test_templated_guides_follow_the_page_template():
    for name, key in GUIDES.items():
        text = (ROOT / "docs/guides" / f"{name}.md").read_text()
        assert text.startswith(f'<p align="center"><img src="../assets/art/guide-{key}-hero.svg" alt="'), name
        assert "**In 30 seconds:**" in text and "## Do it" in text and "## Learn more" in text, name
        assert f"guide-{key}-diagram.svg" in text, name
        hood = text.split("## Under the hood", 1)[1].split("\n## ", 1)[0]
        assert re.search(r"^Source: .+", hood, re.MULTILINE), f"{name}: Under the hood needs a Source line"


REFERENCE = ("10-reference", "11-which-command", "12-glossary")  # reference chapters: hero, 30 seconds and Learn more, no stunts


def test_reference_chapters_have_hero_summary_and_learn_more():
    for name in REFERENCE:
        text = (MANUAL / f"{name}.md").read_text()
        assert text.startswith(f'<p align="center"><img src="../assets/art/{name}-hero.svg" alt="'), name
        assert "**In 30 seconds:**" in text and "## Learn more" in text, name


def test_architecture_how_it_works_index_links_every_under_the_hood_section():
    index = (ROOT / "docs/architecture/README.md").read_text()
    for folder in ("manual", "guides"):
        for f in sorted((ROOT / "docs" / folder).glob("*.md")):
            if "## Under the hood" in f.read_text():
                assert f"../{folder}/{f.name}#under-the-hood" in index, f"{folder}/{f.name} is missing from the How it works index"


def test_architecture_pages_open_with_their_header_art():
    for f in sorted((ROOT / "docs/architecture").glob("*.md")):
        assert f.read_text().startswith(f'<p align="center"><img src="../assets/art/arch-{f.stem}-hero.svg" alt="'), f.name
        assert (ROOT / f"docs/assets/art/arch-{f.stem}-hero.svg").is_file(), f.name
