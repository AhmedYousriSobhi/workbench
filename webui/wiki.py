"""The in-page wiki: serves the markdown under docs/ (manual, guides, architecture, ADRs) as linked HTML pages.

A small renderer for the subset those files use (headings, paragraphs, lists, tables, fenced code, quotes, images,
links, inline code/bold/italic). Pages are read and rendered per request, so nothing is held in memory while idle
(SPEC W-1). A link to another markdown file under docs/ becomes a wiki link; any other repo-relative link (SPEC.md,
sim.yaml, source files) is shown as plain code, since the page cannot open files outside docs/.
"""
import html
import posixpath
import re
from pathlib import Path

GROUPS = [("manual", "Manual"), ("guides", "Guides"), ("architecture", "Architecture"), ("adr", "Decisions (ADR)")]
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
SKIP = {"measurements", "assets", "legacy-lab-reuse.md"}


class WikiError(Exception):
    """Unknown page or file; the caller answers 404."""


def esc(text: str) -> str:
    return html.escape(text, quote=True)


class Wiki:
    def __init__(self, docs: Path):
        self.docs = docs.resolve()

    # --- lookup: only files under docs/, never above it ------------------------------------------------------------
    def resolve(self, rel: str, suffix: str) -> Path:
        path = (self.docs / rel).resolve()
        if not path.is_relative_to(self.docs) or path.suffix != suffix or not path.is_file():
            raise WikiError(rel)
        if path.relative_to(self.docs).parts[0] in SKIP:
            raise WikiError(rel)
        return path

    def page_path(self, slug: str) -> Path:
        return self.resolve(f"{slug}.md", ".md")

    def file_path(self, rel: str) -> Path:
        path = self.resolve(rel, Path(rel).suffix)
        if path.suffix not in IMAGES:
            raise WikiError(rel)
        return path

    def title(self, path: Path) -> str:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return re.sub(r"[`*]", "", line[2:]).strip()
        return path.stem

    def nav(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """(group title, [(slug, page title)]) for the sidebar; manual chapters in number order."""
        out = []
        for folder, label in GROUPS:
            files = sorted((self.docs / folder).glob("*.md"), key=lambda p: (p.stem != "README", p.name))
            if files:
                out.append((label, [(f"{folder}/{p.stem}", self.title(p) if p.stem != "README" else "Start here") for p in files]))
        return out

    # --- search -----------------------------------------------------------------------------------------------------
    def search(self, query: str, limit: int = 30) -> list[dict]:
        words = [w for w in query.lower().split() if w]
        if not words:
            return []
        hits = []
        for folder, _ in GROUPS:
            for p in sorted((self.docs / folder).glob("*.md")):
                text = p.read_text(encoding="utf-8")
                low = text.lower()
                if not all(w in low for w in words):
                    continue
                title = self.title(p)
                score = sum(low.count(w) for w in words) + (20 if all(w in title.lower() for w in words) else 0)
                i = low.find(words[0])
                snippet = " ".join(text[max(0, i - 60):i + 120].split())
                hits.append({"slug": f"{folder}/{p.stem}", "title": title, "snippet": snippet, "score": score})
        return sorted(hits, key=lambda h: -h["score"])[:limit]

    # --- rendering --------------------------------------------------------------------------------------------------
    def link(self, target: str, page: str) -> str | None:
        """The wiki URL for a markdown link target, or None when it does not point inside docs/."""
        if re.match(r"^[a-z]+:", target) or target.startswith("//"):
            return target if target.startswith(("http://", "https://")) else None
        path, _, frag = target.partition("#")
        if not path:
            return f"#{frag}"
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(page), path))
        if rel.startswith(".."):
            return None
        suffix = posixpath.splitext(rel)[1]
        try:
            if suffix == ".md":
                self.page_path(rel[:-3])
                return f"/help/{rel[:-3]}" + (f"#{frag}" if frag else "")
            if suffix in IMAGES:
                self.file_path(rel)
                return f"/help/files/{rel}"
        except WikiError:
            return None
        return None

    def inline(self, text: str, page: str) -> str:
        codes: list[str] = []

        def stash(m: re.Match) -> str:
            codes.append(f"<code>{esc(m.group(1))}</code>")
            return f"\x00{len(codes) - 1}\x00"

        text = re.sub(r"`([^`]+)`", stash, text)
        text = esc(text)

        def image(m: re.Match) -> str:
            url = self.link(html.unescape(m.group(2)), page)
            alt = m.group(1)
            return f'<img src="{esc(url)}" alt="{alt}" loading="lazy">' if url else f"<em>[image: {alt}]</em>"

        def anchor(m: re.Match) -> str:
            url = self.link(html.unescape(m.group(2)), page)
            if url is None:  # a file outside docs/: keep the label, drop the dead link
                return f"<code>{m.group(1)}</code>" if not m.group(1).startswith("\x00") else m.group(1)
            ext = ' rel="noopener noreferrer" target="_blank"' if url.startswith("http") else ""
            return f'<a href="{esc(url)}"{ext}>{m.group(1)}</a>'

        text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", image, text)
        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", anchor, text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])", r"<em>\1</em>", text)
        return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)

    def render(self, slug: str) -> tuple[str, str]:
        """(title, body html) for one page."""
        path = self.page_path(slug)
        page = f"{slug}.md"
        lines = path.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        para: list[str] = []
        i = 0

        def flush() -> None:
            if para:
                out.append(f"<p>{self.inline(' '.join(para), page)}</p>")
                para.clear()

        while i < len(lines):
            line = lines[i]
            if line.startswith("```"):
                flush()
                lang = line[3:].strip()
                code = []
                i += 1
                while i < len(lines) and not lines[i].startswith("```"):
                    code.append(lines[i])
                    i += 1
                out.append(f'<pre class="code"><code data-lang="{esc(lang)}">{esc(chr(10).join(code))}</code></pre>')
            elif m := re.match(r"^(#{1,6})\s+(.*)$", line):
                flush()
                n = len(m.group(1))
                anchor = re.sub(r"[^\w\- ]", "", re.sub(r"[`*]", "", m.group(2)).lower()).strip().replace(" ", "-")
                out.append(f'<h{n} id="{esc(anchor)}">{self.inline(m.group(2), page)}</h{n}>')
            elif re.match(r"^\s*\|", line) and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
                flush()
                cells = lambda s: [c.strip() for c in s.strip().strip("|").replace("\\|", "\x01").split("|")]
                head = cells(line)
                i += 2
                rows = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    rows.append(cells(lines[i]))
                    i += 1
                i -= 1
                th = "".join(f"<th>{self.inline(c.replace(chr(1), '|'), page)}</th>" for c in head)
                body = "".join("<tr>" + "".join(f"<td>{self.inline(c.replace(chr(1), '|'), page)}</td>" for c in r) + "</tr>" for r in rows)
                out.append(f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>')
            elif re.match(r"^\s*([-*]|\d+\.)\s+", line):
                flush()
                ordered = bool(re.match(r"^\s*\d+\.", line))
                items: list[str] = []
                while i < len(lines) and (m := re.match(r"^\s*(?:[-*]|\d+\.)\s+(.*)$", lines[i]) or re.match(r"^\s{2,}(\S.*)$", lines[i])):
                    if re.match(r"^\s*(?:[-*]|\d+\.)\s+", lines[i]):
                        items.append(m.group(1))
                    else:  # a wrapped continuation line
                        items[-1] += " " + m.group(1)
                    i += 1
                i -= 1
                tag = "ol" if ordered else "ul"
                out.append(f"<{tag}>" + "".join(f"<li>{self.inline(t, page)}</li>" for t in items) + f"</{tag}>")
            elif line.startswith(">"):
                flush()
                quote = []
                while i < len(lines) and lines[i].startswith(">"):
                    quote.append(lines[i].lstrip(">").strip())
                    i += 1
                i -= 1
                out.append(f"<blockquote>{self.inline(' '.join(quote), page)}</blockquote>")
            elif re.match(r"^(-{3,}|\*{3,})\s*$", line):
                flush()
                out.append("<hr>")
            elif not line.strip():
                flush()
            else:
                para.append(line.strip())
            i += 1
        flush()
        return self.title(path), "\n".join(out)


STYLE = """
:root{color-scheme:light dark;--bg:#f6f8fa;--panel:#fff;--line:#d0d7de;--fg:#1f2328;--dim:#57606a;--accent:#0969da;--on-accent:#fff}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--panel:#161b22;--line:#30363d;--fg:#e6edf3;--dim:#9da7b3;--accent:#58a6ff;--on-accent:#0d1117}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.skip{position:absolute;left:-999px;top:8px;background:var(--accent);color:var(--on-accent);padding:8px 12px;border-radius:6px}.skip:focus{left:8px}
header.top{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;padding:10px 24px;background:var(--panel);border-bottom:1px solid var(--line)}
header.top a.back{font-weight:600;text-decoration:none;color:var(--accent);min-height:40px;display:inline-flex;align-items:center}
header.top strong{margin-right:auto}
form.search{display:flex;gap:8px}form.search input{font:inherit;min-height:40px;min-width:0;width:240px;max-width:100%;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);padding:0 10px}
form.search button{font:inherit;font-weight:600;min-height:40px;padding:0 14px;border:0;border-radius:6px;background:var(--accent);color:var(--on-accent);cursor:pointer}
.wrap{display:grid;grid-template-columns:260px minmax(0,1fr);gap:24px;max-width:1280px;margin:0 auto;padding:16px 24px 48px;align-items:start}
nav.side{position:sticky;top:72px;max-height:calc(100vh - 88px);overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px}
nav.side h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin:12px 8px 4px}nav.side h2:first-child{margin-top:0}
nav.side a{display:block;padding:6px 8px;border-radius:6px;color:var(--fg);text-decoration:none;font-size:14px;min-height:32px}
nav.side a:hover{background:var(--bg)}nav.side a[aria-current=page]{background:var(--accent);color:var(--on-accent);font-weight:600}
article{min-width:0;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:8px 32px 32px}
article h1{font-size:28px;letter-spacing:-.01em}article h2{margin-top:32px;padding-bottom:6px;border-bottom:1px solid var(--line)}article h3{margin-top:24px}
a{color:var(--accent)}img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:8px;display:block;margin:8px 0}
code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--bg);padding:1px 5px;border-radius:4px}
pre.code{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow:auto}pre.code code{background:none;padding:0}
.tablewrap{overflow-x:auto}table{border-collapse:collapse;width:100%;margin:12px 0}th,td{text-align:left;vertical-align:top;padding:8px 10px;border:1px solid var(--line)}th{background:var(--bg)}
blockquote{margin:12px 0;padding:4px 14px;border-left:4px solid var(--accent);background:var(--bg);border-radius:4px}
.hit{padding:10px 0;border-bottom:1px solid var(--line)}.hit p{margin:2px 0;color:var(--dim);font-size:14px}
@media(max-width:860px){.wrap{grid-template-columns:minmax(0,1fr);padding:12px 16px 32px}nav.side{position:static;max-height:none;order:2}article{padding:4px 16px 24px}header.top{padding:10px 16px}}
"""


def page(wiki: Wiki, title: str, body: str, current: str = "", query: str = "") -> str:
    nav = "".join(
        f"<h2>{esc(label)}</h2>" + "".join(
            f'<a href="/help/{esc(slug)}"{" aria-current=page" if slug == current else ""}>{esc(t)}</a>' for slug, t in items)
        for label, items in wiki.nav())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · workbench help</title><style>{STYLE}</style></head><body>
<a class="skip" href="#content">Skip to content</a>
<header class="top"><a class="back" href="/">← Dashboard</a><strong>workbench help</strong>
<form class="search" action="/help/search" method="get" role="search"><input type="search" name="q" value="{esc(query)}" aria-label="Search the help" placeholder="Search the help"><button type="submit">Search</button></form></header>
<div class="wrap"><nav class="side" aria-label="Help pages">{nav}</nav><article id="content" tabindex="-1">{body}</article></div></body></html>"""
