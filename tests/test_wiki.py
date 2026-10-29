import re
from pathlib import Path

import pytest

pytest.importorskip("starlette")
from webui.wiki import Wiki, WikiError, page

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def wiki(tmp_path):
    d = tmp_path / "docs"
    (d / "manual" / "img").mkdir(parents=True)
    (d / "guides").mkdir()
    (d / "manual" / "README.md").write_text("# Manual\n\nSee [guide](../guides/g.md#step) and [spec](../../SPEC.md).\n")
    (d / "manual" / "01-a.md").write_text(
        "# 1. A page\n\nText with `code <b>`, **bold**, *em* and ![shot](img/s.png).\n\n"
        "| Col | Other |\n|---|---|\n| `a` | b \\| c |\n\n- one\n  wrapped\n- two\n\n1. first\n2. second\n\n"
        "> a quote\n\n```bash\necho '<hi>' && ls\n```\n\n## Next step\n\n[ext](https://example.com) [gone](missing.md)\n")
    (d / "guides" / "g.md").write_text("# Guide\n\nsecret word: pineapple\n")
    (d / "manual" / "img" / "s.png").write_bytes(b"\x89PNG")
    (tmp_path / "SPEC.md").write_text("outside")
    return Wiki(d)


def test_renders_the_markdown_subset_and_escapes_html(wiki):
    title, body = wiki.render("manual/01-a")
    assert title == "1. A page"
    assert "<code>code &lt;b&gt;</code>" in body and "<strong>bold</strong>" in body and "<em>em</em>" in body
    assert '<img src="/help/files/manual/img/s.png" alt="shot"' in body
    assert "<th>Col</th>" in body and "<td>b | c</td>" in body
    assert "<li>one wrapped</li>" in body and "<ol>" in body and "<blockquote>a quote</blockquote>" in body
    assert "echo &#x27;&lt;hi&gt;&#x27; &amp;&amp; ls" in body and "<b>" not in body.replace("<strong>", "")
    assert '<h2 id="next-step">Next step</h2>' in body


def test_links_stay_inside_the_wiki(wiki):
    _, home = wiki.render("manual/README")
    assert 'href="/help/guides/g#step"' in home
    assert "<code>spec</code>" in home and "SPEC.md" not in home  # outside docs/: no dead link
    _, body = wiki.render("manual/01-a")
    assert 'href="https://example.com"' in body and 'rel="noopener noreferrer"' in body
    assert "<code>gone</code>" in body  # a missing page is not linked


@pytest.mark.parametrize("slug", ["../SPEC", "manual/../../SPEC", "nope/x", "manual/missing", "/etc/passwd"])
def test_unknown_or_escaping_pages_are_refused(wiki, slug):
    with pytest.raises(WikiError):
        wiki.render(slug)


def test_files_are_images_inside_docs_only(wiki):
    assert wiki.file_path("manual/img/s.png").name == "s.png"
    for bad in ("manual/README.md", "../SPEC.md", "manual/img/missing.png", "../docs/../SPEC.md"):
        with pytest.raises(WikiError):
            wiki.file_path(bad)


def test_nav_orders_the_start_page_first_and_search_ranks_by_title_and_count(wiki):
    nav = dict(wiki.nav())
    assert nav["Manual"][0] == ("manual/README", "Start here") and ("guides/g", "Guide") in nav["Guides"]
    assert [h["slug"] for h in wiki.search("pineapple")] == ["guides/g"]
    assert "pineapple" in wiki.search("pineapple")[0]["snippet"]
    assert wiki.search("   ") == [] and wiki.search("zzz") == []
    assert wiki.search("guide")[0]["slug"] == "guides/g"


def test_page_marks_the_current_entry_and_escapes_the_query(wiki):
    out = page(wiki, "T", "<p>x</p>", current="guides/g", query='"><script>')
    assert 'href="/help/guides/g" aria-current=page' in out and "<script>" not in out
    assert 'href="/"' in out and 'name="q"' in out


def test_every_real_page_renders_and_every_internal_link_resolves():
    real = Wiki(ROOT / "docs")
    seen = 0
    for folder in ("manual", "guides", "architecture", "adr"):
        for p in (ROOT / "docs" / folder).glob("*.md"):
            _, body = real.render(f"{folder}/{p.stem}")
            seen += 1
            for url in re.findall(r'(?:href|src)="(/help/[^"#]+)', body):
                if url.startswith("/help/files/"):
                    real.file_path(url[len("/help/files/"):])
                else:
                    real.page_path(url[len("/help/"):])
    assert seen >= 25
