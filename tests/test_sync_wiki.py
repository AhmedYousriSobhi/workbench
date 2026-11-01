import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sync_wiki", ROOT / "scripts/sync_wiki.py")
sw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sw)

BLOB = "https://example.test/o/r/blob/main"


def test_page_and_image_names():
    assert sw.page_name("manual/README") == "Home"
    assert sw.page_name("manual/04-nodes-and-shells") == "Manual-04-nodes-and-shells"
    assert sw.page_name("guides/getting-started") == "Guide-getting-started"
    assert sw.page_name("adr/0014-sshd-on-compute-nodes") == "ADR-0014-sshd-on-compute-nodes"
    assert sw.page_name("architecture/README") == "Architecture"
    assert sw.image_name("manual/img/web-overview.png") == "images/manual-web-overview.png"


def test_rewrite_points_links_at_wiki_pages_images_and_repo_files(tmp_path):
    docs = tmp_path / "docs"
    for f in ("manual/README.md", "manual/06-scenarios.md", "guides/g.md", "manual/img/a.png"):
        (docs / f).parent.mkdir(parents=True, exist_ok=True)
        (docs / f).write_text("x")
    text = ("[ch](06-scenarios.md#top) [guide](../guides/g.md) ![a](img/a.png) [spec](../../SPEC.md) "
            "[web](https://x.test/a.md) [here](#sec) [gone](missing.md)\n```\n[keep](06-scenarios.md)\n```\n")
    out = sw.rewrite(text, "manual/README.md", docs, BLOB)
    assert "[ch](Manual-06-scenarios#top)" in out and "[guide](Guide-g)" in out and "![a](images/manual-a.png)" in out
    assert f"[spec]({BLOB}/SPEC.md)" in out and "[web](https://x.test/a.md)" in out and "[here](#sec)" in out
    assert f"[gone]({BLOB}/docs/manual/missing.md)" in out
    assert "[keep](06-scenarios.md)" in out  # code blocks are left alone


def test_generated_wiki_of_the_real_docs_is_closed_and_clean(tmp_path):
    names = set(sw.generate(ROOT / "docs", tmp_path, BLOB))
    assert {"Home", "Manual-11-which-command", "Guide-ssh-connection-testing"} <= names and len(names) >= 25
    pages = {p.stem for p in tmp_path.glob("*.md")}
    sidebar = (tmp_path / "_Sidebar.md").read_text()
    for n in names:
        assert f"({n})" in sidebar or n == "Home", n
    for p in tmp_path.glob("*.md"):
        text = p.read_text()
        assert "/home/" not in text, p.name
        for target in re.findall(r"\]\(([^)\s#]+)", text):
            if not target.startswith(("http", "images/")):
                assert target in pages, (p.name, target)
            elif target.startswith("images/"):
                assert (tmp_path / target).is_file(), (p.name, target)


def test_ci_token_goes_in_a_header_only_when_origin_has_no_credentials(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ci-value")
    plain = "https://github.com/o/r"
    args = sw.auth_args(plain, plain)
    assert args[0] == "-c" and args[1].startswith("http.extraheader=Authorization: Basic ") and "ci-value" not in args[1]
    assert sw.auth_args(plain, "https://github.com/o/r?with=credentials") == []  # a developer's origin carries its own
    monkeypatch.delenv("GITHUB_TOKEN")
    assert sw.auth_args(plain, plain) == []
