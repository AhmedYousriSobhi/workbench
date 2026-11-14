"""The in-page wiki: serves the markdown under docs/ (manual, guides, architecture, ADRs) as linked HTML pages.

A small renderer for the subset those files use (headings, paragraphs, lists, tables, fenced code, quotes, images,
links, inline code/bold/italic) plus the constructs of the illustrated page template: GitHub alerts (`> [!TIP]`, and the
project kinds `HOOD` and `DIDYOUKNOW`), `<details>`/`<summary>`, `<p align="center">`, `<picture>` (the help shows its `<img>`),
`<img>`, `<kbd>`, and an italic line after an image as its caption. Only those tags pass; any other tag is escaped. Pages are read and rendered per request, so nothing is held in memory while idle
(SPEC W-1). A link to another markdown file under docs/ becomes a wiki link; any other repo-relative link (SPEC.md,
sim.yaml, source files) is shown as plain code, since the page cannot open files outside docs/.
"""
import html
import posixpath
import re
from pathlib import Path

GROUPS = [("manual", "Manual"), ("guides", "Guides"), ("architecture", "Architecture"), ("adr", "Decisions (ADR)"), ("agents", "For AI agents")]
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
SKIP = {"measurements", "assets", "legacy-lab-reuse.md"}


ALERTS = {  # kind: (icon, label); the label is text, so the kind never depends on colour or icon alone
    "note": ("ℹ️", "Note"), "tip": ("💡", "Tip"), "important": ("❗", "Important"), "warning": ("⚠️", "Warning"),
    "caution": ("🛑", "Caution"), "hood": ("🔧", "Under the hood"), "didyouknow": ("💡", "Did you know?"),
}
ALERT_RE = re.compile(r"^>\s*\[!(" + "|".join(ALERTS) + r")\]\s*$", re.IGNORECASE)


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
        parts = path.relative_to(self.docs).parts
        if parts[0] in SKIP and not (parts[0] == "assets" and len(parts) > 2 and parts[1] == "art"):  # the generated art only
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

        def html_img(m: re.Match) -> str:  # <img src alt width height>: only those attributes, src resolved like a markdown image
            attrs = dict(re.findall(r"(\w+)=&quot;(.*?)&quot;", m.group(1)))
            url = self.link(html.unescape(attrs.get("src", "")), page)
            alt = attrs.get("alt", "")
            if not url:
                return f"<em>[image: {alt}]</em>"
            size = "".join(f' {k}="{attrs[k]}"' for k in ("width", "height") if attrs.get(k, "").isdigit())
            return f'<img src="{esc(url)}" alt="{alt}"{size} loading="lazy">'

        text = re.sub(r"&lt;img\s+((?:(?!&gt;).)*?)/?&gt;", html_img, text)
        text = re.sub(r"&lt;kbd&gt;((?:(?!&lt;).)*)&lt;/kbd&gt;", r"<kbd>\1</kbd>", text)
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

        centered: list[bool] = []  # open <p align="center"> blocks written over several lines
        details = 0

        def flush() -> None:
            if not para:
                return
            body = self.inline(" ".join(para), page)
            para.clear()
            caption = re.fullmatch(r"<em>(.+)</em>", body)
            if caption and out and out[-1].startswith('<p class="img'):
                img = out.pop()
                cls = " center" if "center" in img[:24] else ""
                inner = re.sub(r"^<p[^>]*>|</p>$", "", img)
                out.append(f'<figure class="img{cls}">{inner}<figcaption>{caption.group(1)}</figcaption></figure>')
            elif re.fullmatch(r"<img [^>]*>", body):
                out.append(f'<p class="img">{body}</p>')
            else:
                out.append(f"<p>{body}</p>")

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
            elif re.fullmatch(r"\s*</?picture>\s*|\s*<source\b[^>]*>\s*", line):
                flush()  # <picture>: the help shows the <img> fallback (the page's own theme decides, not media queries)
            elif m := re.fullmatch(r'\s*<p align="center">(.*)</p>\s*', line):
                flush()
                inner = self.inline(m.group(1).strip(), page)
                out.append(f'<p class="img center">{inner}</p>' if re.fullmatch(r"<img [^>]*>", inner) else f'<p class="center">{inner}</p>')
            elif re.fullmatch(r'\s*<p align="center">\s*', line):
                flush()
                out.append('<div class="center">')
                centered.append(True)
            elif centered and re.fullmatch(r"\s*</p>\s*", line):
                flush()
                out.append("</div>")
                centered.pop()
            elif m := re.fullmatch(r"\s*<details>\s*(?:<summary>(.*)</summary>)?\s*", line):
                flush()
                details += 1
                out.append("<details>" + (f"<summary>{self.inline(m.group(1), page)}</summary>" if m.group(1) else ""))
            elif m := re.fullmatch(r"\s*<summary>(.*)</summary>\s*", line):
                flush()
                out.append(f"<summary>{self.inline(m.group(1), page)}</summary>")
            elif details and re.fullmatch(r"\s*</details>\s*", line):
                flush()
                details -= 1
                out.append("</details>")
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
                alert = ALERT_RE.match(line)
                quote = []
                while i < len(lines) and lines[i].startswith(">"):
                    quote.append(lines[i].lstrip(">").strip())
                    i += 1
                i -= 1
                if alert:  # paragraphs split at empty quote lines
                    kind = alert.group(1).lower()
                    icon, name = ALERTS[kind]
                    paras: list[str] = []
                    cur: list[str] = []
                    for q in quote[1:] + [""]:
                        if q:
                            cur.append(q)
                        elif cur:
                            paras.append(" ".join(cur))
                            cur = []
                    body = "".join(f"<p>{self.inline(t, page)}</p>" for t in paras)
                    out.append(f'<div class="alert alert-{kind}" role="note"><p class="alert-title"><span aria-hidden="true">{icon}</span> '
                               f"<strong>{esc(name)}</strong></p>{body}</div>")
                else:
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
        out += ["</div>"] * len(centered) + ["</details>"] * details
        return self.title(path), "\n".join(out)


STYLE = (Path(__file__).parent / "static" / "help.css").read_text()  # served at /static/help.css; no inline style (#525, ADR 0047)


def page(wiki: Wiki, title: str, body: str, current: str = "", query: str = "") -> str:
    nav = "".join(
        f"<h2>{esc(label)}</h2>" + "".join(
            f'<a href="/help/{esc(slug)}"{" aria-current=page" if slug == current else ""}>{esc(t)}</a>' for slug, t in items)
        for label, items in wiki.nav())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · workbench help</title><link rel="stylesheet" href="/static/tokens.css"><link rel="stylesheet" href="/static/help.css"></head><body>
<a class="skip" href="#content">Skip to content</a>
<header class="top"><a class="back" href="/">← Dashboard</a><strong>workbench help</strong>
<form class="search" action="/help/search" method="get" role="search"><input type="search" name="q" value="{esc(query)}" aria-label="Search the help" placeholder="Search the help"><button type="submit">Search</button></form></header>
<div class="wrap"><nav class="side" aria-label="Help pages">{nav}</nav><article id="content" tabindex="-1">{body}</article></div></body></html>"""
