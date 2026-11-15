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


# ---- the illustrated page template (issue 585) ----------------------------------------------------------------------

ART_PAGE = '''# Art page

<p align="center"><img src="../assets/art/hero.svg" alt="the hero's picture" width="720"></p>

*A caption under the hero.*

> [!TIP]
> Use the **tip**.
>
> Second paragraph.

> [!HOOD]
> The mechanism.

> [!NOTE]
> n

> [!IMPORTANT]
> i

> [!WARNING]
> w

> [!CAUTION]
> c

> [!DIDYOUKNOW]
> d

> an ordinary quote

<details><summary>The full story</summary>

Inside with `code` and press <kbd>Ctrl</kbd>+<kbd>C</kbd>.

</details>

<details>
<summary>Second</summary>

body two

</details>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../assets/art/hero.svg">
    <img src="../assets/art/hero.svg" alt="in a picture">
  </picture>
</p>

<script>alert(1)</script> and <b>raw</b> and <img src="../../SPEC.md" alt="gone">
'''


@pytest.fixture
def art_wiki(tmp_path):
    d = tmp_path / "docs"
    (d / "manual").mkdir(parents=True)
    (d / "assets" / "art").mkdir(parents=True)
    (d / "assets" / "art" / "hero.svg").write_text("<svg/>")
    (d / "assets" / "secret.png").write_bytes(b"x")
    (d / "manual" / "02-art.md").write_text(ART_PAGE)
    return Wiki(d)


def test_every_alert_kind_renders_with_icon_and_label(art_wiki):
    _, body = art_wiki.render("manual/02-art")
    for kind, label in (("tip", "Tip"), ("hood", "Under the hood"), ("note", "Note"), ("important", "Important"),
                        ("warning", "Warning"), ("caution", "Caution"), ("didyouknow", "Did you know?")):
        m = re.search(rf'<div class="alert alert-{kind}" role="note"><p class="alert-title"><span aria-hidden="true">(\S+)</span> '
                      rf"<strong>{re.escape(label)}</strong></p>", body)
        assert m, kind
    assert "<p>Use the <strong>tip</strong>.</p><p>Second paragraph.</p>" in body
    assert "<blockquote>an ordinary quote</blockquote>" in body


def test_details_kbd_picture_and_figure_caption(art_wiki):
    _, body = art_wiki.render("manual/02-art")
    assert "<details><summary>The full story</summary>" in body and body.count("<details>") == body.count("</details>") == 2
    assert "<code>code</code>" in body and "<kbd>Ctrl</kbd>+<kbd>C</kbd>" in body
    assert "<summary>Second</summary>" in body
    assert ('<figure class="img center"><img src="/help/files/assets/art/hero.svg" alt="the hero&#x27;s picture" width="720" '
            'loading="lazy"><figcaption>A caption under the hero.</figcaption></figure>') in body
    # the dark <source> is kept (the help follows the viewer's scheme, issue 797)
    assert ('<picture><source media="(prefers-color-scheme: dark)" srcset="/help/files/assets/art/hero.svg"><img '
            'src="/help/files/assets/art/hero.svg" alt="in a picture" loading="lazy"></picture>') in body
    assert 'alt="in a picture"' in body and '<div class="center">' in body and body.count("<div") == body.count("</div>")


def test_other_tags_are_escaped_and_art_is_the_only_asset_served(art_wiki):
    _, body = art_wiki.render("manual/02-art")
    assert "<script>" not in body and "&lt;script&gt;alert(1)&lt;/script&gt;" in body and "<b>raw" not in body
    assert "<em>[image: gone]</em>" in body  # an <img> that points outside docs/ is not loaded
    assert art_wiki.file_path("assets/art/hero.svg").name == "hero.svg"
    with pytest.raises(WikiError):
        art_wiki.file_path("assets/secret.png")


def test_picture_keeps_only_a_dark_source_inside_docs_and_snapshots_are_served(tmp_path):
    d = tmp_path / "docs"
    (d / "manual").mkdir(parents=True)
    (d / "assets" / "snapshots").mkdir(parents=True)
    for n in ("s-light.svg", "s-dark.svg"):
        (d / "assets" / "snapshots" / n).write_text("<svg/>")
    (d / "assets" / "snapshots" / "s.txt").write_text("raw output")
    (d / "manual" / "03-x.md").write_text(
        '<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="../assets/snapshots/s-dark.svg">\n'
        '  <img src="../assets/snapshots/s-light.svg" alt="Terminal: ls">\n</picture>\n\n*The caption*\n\n'
        '<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="../../outside.svg">\n'
        '  <img src="../assets/snapshots/s-light.svg" alt="no dark">\n</picture>\n\n'
        '<picture>\n  <source media="print" srcset="../assets/snapshots/s-dark.svg">\n</picture>\n')
    w = Wiki(d)
    _, body = w.render("manual/03-x")
    assert ('<figure class="img"><picture><source media="(prefers-color-scheme: dark)" srcset="/help/files/assets/snapshots/'
            's-dark.svg"><img src="/help/files/assets/snapshots/s-light.svg" alt="Terminal: ls" loading="lazy"></picture>'
            '<figcaption>The caption</figcaption></figure>') in body
    assert '<p class="img"><img src="/help/files/assets/snapshots/s-light.svg" alt="no dark" loading="lazy"></p>' in body
    assert body.count("<source") == 1 and "outside" not in body and "print" not in body
    assert w.file_path("assets/snapshots/s-dark.svg").name == "s-dark.svg"
    with pytest.raises(WikiError):
        w.file_path("assets/snapshots/s.txt")   # the captured text is not an image


def test_the_real_snapshot_pages_render_their_cards():
    real = Wiki(ROOT / "docs")
    for slug in ("manual/02-install-and-start", "manual/06-scenarios", "guides/getting-started", "guides/client-testing"):
        _, body = real.render(slug)
        assert re.search(r'<picture><source media="\(prefers-color-scheme: dark\)" srcset="/help/files/assets/snapshots/[\w-]+-dark\.svg">'
                         r'<img src="/help/files/assets/snapshots/[\w-]+-light\.svg" alt="Terminal: ', body), slug


def test_alert_styles_exist_for_every_kind_and_both_themes():
    from webui.wiki import ALERTS, STYLE
    for kind in ALERTS:
        assert f".alert-{kind}" in STYLE or kind in ("note",), kind
    assert "details{" in STYLE and "kbd{" in STYLE and "figcaption{" in STYLE
