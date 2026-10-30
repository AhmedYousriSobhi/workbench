"""Render docs/progress.yaml into docs/PROGRESS.md (GitHub Mermaid) and docs/progress.html (any browser).

Run `python -m benchctl.progress` to regenerate, `--check` to fail when the outputs are stale.
"""
import html
import sys
from pathlib import Path

import yaml

SRC, MD, HTML = Path("docs/progress.yaml"), Path("docs/PROGRESS.md"), Path("docs/progress.html")
MERMAID_JS = "https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.9.1/mermaid.min.js"
LABEL = {"done": "Done", "review": "In review", "partial": "Partial", "todo": "To do"}
ICON = {"done": "✅", "review": "🔍", "partial": "🟡", "todo": "⬜"}
COLOR = {"done": "#1a7f37", "review": "#0969da", "partial": "#bf8700", "todo": "#6e7781"}


def load(path: Path = SRC) -> dict:
    data = yaml.safe_load(path.read_text())
    for ph in data["phases"]:
        for it in ph["items"]:
            if it["status"] not in LABEL:
                raise ValueError(f"{ph['id']}/{it['name']}: unknown status {it['status']!r}")
    return data


def prs(item: dict) -> list[int]:
    v = item.get("pr")
    return [] if not v else v if isinstance(v, list) else [v]


def phase_status(items: list[dict]) -> str:
    st = {i["status"] for i in items}
    if st <= {"done"}:
        return "done"
    if st <= {"todo"}:
        return "todo"
    return "review" if st <= {"done", "review"} else "partial"


def counts(items: list[dict]) -> dict[str, int]:
    return {s: sum(i["status"] == s for i in items) for s in LABEL}


def pct(items: list[dict]) -> int:
    """Share of items that are built (done or in review)."""
    c = counts(items)
    return round(100 * (c["done"] + c["review"]) / len(items)) if items else 0


def all_items(data: dict) -> list[dict]:
    return [i for ph in data["phases"] for i in ph["items"]]


def _classdefs() -> list[str]:
    return [f"    classDef {s} fill:{COLOR[s]},stroke:{COLOR[s]},color:#fff" for s in LABEL]


def roadmap_mermaid(data: dict) -> str:
    phases = [p for p in data["phases"] if p["id"] != "x"]
    lines = ["flowchart LR"]
    for p in phases:
        lines.append(f'    {p["id"]}["{p["title"]}<br/>{pct(p["items"])}%"]:::{phase_status(p["items"])}')
    lines.append("    " + " --> ".join(p["id"] for p in phases))
    return "\n".join(lines + _classdefs())


def architecture_mermaid(data: dict) -> str:
    a = data["architecture"]
    lines = ["flowchart TB"]
    for g in a["groups"]:
        lines.append(f'    subgraph {g["id"]}["{g["title"]}"]')
        lines += [f'        {n["id"]}["{n["label"]}"]:::{n["status"]}' for n in a["nodes"] if n["group"] == g["id"]]
        lines.append("    end")
    for src, dst, label in a["edges"]:
        lines.append(f'    {src} -->|"{label}"| {dst}' if label else f"    {src} --> {dst}")
    return "\n".join(lines + _classdefs())


def _pr_links(item: dict, repo: str, legacy: int = 0) -> str:
    """PR numbers up to `legacy` come from the previous repository: shown as text, since nothing there to link to."""
    return ", ".join(f"#{n}" if n <= legacy else f"[#{n}]({repo}/pull/{n})" for n in prs(item)) or "-"


def _legend() -> str:
    return " ".join(f"{ICON[s]} {LABEL[s]}" for s in LABEL)


def render_md(data: dict) -> str:
    repo, items, legacy = data["repo"], all_items(data), data.get("legacy_pr_max", 0)
    c = counts(items)
    intro = (f"Last updated {data['updated']}. Legend: {_legend()}. "
             "Mermaid diagrams render on GitHub; for any browser open [progress.html](progress.html).")
    summary = (f"**{pct(items)}% built** ({c['done']} done, {c['review']} in review, {c['partial']} partial, "
               f"{c['todo']} to do, {len(items)} items).")
    legend = ("What exists and how the parts connect. Green is verified, blue is in review, "
              "amber is partial, grey is not started.")
    out = [
        "# Progress", "",
        "<!-- GENERATED from docs/progress.yaml by `python -m benchctl.progress`; do not edit. -->",
        intro, "", summary, "",
        "## Roadmap", "", "```mermaid", roadmap_mermaid(data), "```", "",
        "## As built", "", legend, "",
        "```mermaid", architecture_mermaid(data), "```", "",
        "## Phases", "",
    ]
    for p in data["phases"]:
        n = counts(p["items"])
        out += [f"### {p['title']} ({pct(p['items'])}%)", "", f"_{p['goal']}._", "",
                "| Part | Status | How it is built | Where | PR |", "|---|---|---|---|---|"]
        for i in p["items"]:
            where = ", ".join(f"`{w.strip()}`" for w in i["where"].split(",")) if i.get("where") else "-"
            out.append(f"| {i['name']} | {ICON[i['status']]} {LABEL[i['status']]} | {i['how']} | {where} | "
                       f"{_pr_links(i, repo, legacy)} |")
        out += ["", f"{n['done']}/{len(p['items'])} done", ""]
    out += ["## Next up", ""] + [f"{k}. {t}" for k, t in enumerate(data["next"], 1)]
    out += ["", "## Known gaps", ""] + [f"- {g}" for g in data["gaps"]]
    return "\n".join(out) + "\n"


def render_html(data: dict) -> str:
    repo, items, legacy = data["repo"], all_items(data), data.get("legacy_pr_max", 0)
    c = counts(items)
    rows = []
    for p in data["phases"]:
        rows.append(f'<h3>{html.escape(p["title"])} <small>{pct(p["items"])}%</small></h3>'
                    f'<p class="goal">{html.escape(p["goal"])}</p><table><tr><th>Part</th><th>Status</th>'
                    "<th>How it is built</th><th>Where</th><th>PR</th></tr>")
        for i in p["items"]:
            where = ", ".join(f"<code>{html.escape(w.strip())}</code>" for w in i["where"].split(",")) \
                if i.get("where") else "-"
            prl = ", ".join(f"#{n}" if n <= legacy else f'<a href="{repo}/pull/{n}">#{n}</a>' for n in prs(i)) or "-"
            rows.append(f'<tr><td>{html.escape(i["name"])}</td><td><span class="pill {i["status"]}">'
                        f'{LABEL[i["status"]]}</span></td><td>{html.escape(i["how"])}</td><td>{where}</td>'
                        f"<td>{prl}</td></tr>")
        rows.append("</table>")
    css = (":root{--bg:#fff;--fg:#1f2328;--mut:#59636e;--line:#d1d9e0}"
           "@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--mut:#9198a1;--line:#30363d}}"
           "body{background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;max-width:1100px;"
           "margin:0 auto;padding:16px}h1,h2,h3{line-height:1.2}small,.goal{color:var(--mut)}"
           "table{border-collapse:collapse;width:100%;margin-bottom:24px;font-size:14px}"
           "td,th{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}"
           ".pill{color:#fff;border-radius:10px;padding:1px 8px;font-size:12px;white-space:nowrap}"
           + "".join(f".pill.{s}{{background:{COLOR[s]}}}" for s in LABEL)
           + ".mermaid{overflow-x:auto;margin:12px 0}a{color:#0969da}"
           "@media(max-width:700px){table{display:block;overflow-x:auto}}")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>workbench progress</title><style>{css}</style></head><body>
<!-- GENERATED from docs/progress.yaml by `python -m benchctl.progress`; do not edit. -->
<h1>workbench progress</h1>
<p>Last updated {data['updated']}. <b>{pct(items)}% built</b>: {c['done']} done, {c['review']} in review,
{c['partial']} partial, {c['todo']} to do ({len(items)} items).</p>
<h2>Roadmap</h2><pre class="mermaid">{roadmap_mermaid(data)}</pre>
<h2>As built</h2><p class="goal">Green verified, blue in review, amber partial, grey not started.</p>
<pre class="mermaid">{architecture_mermaid(data)}</pre>
<h2>Phases</h2>{"".join(rows)}
<h2>Next up</h2><ol>{"".join(f"<li>{html.escape(t)}</li>" for t in data["next"])}</ol>
<h2>Known gaps</h2><ul>{"".join(f"<li>{html.escape(g)}</li>" for g in data["gaps"])}</ul>
<script src="{MERMAID_JS}"></script>
<script>mermaid.initialize({{startOnLoad:true,securityLevel:"strict",
theme:matchMedia("(prefers-color-scheme: dark)").matches?"dark":"default"}});</script>
</body></html>
"""


def write(data: dict | None = None) -> list[Path]:
    data = data or load()
    MD.write_text(render_md(data))
    HTML.write_text(render_html(data))
    return [MD, HTML]


def stale(data: dict | None = None) -> list[Path]:
    data = data or load()
    want = {MD: render_md(data), HTML: render_html(data)}
    return [p for p, text in want.items() if not p.exists() or p.read_text() != text]


def main(argv: list[str]) -> int:
    if "--check" in argv:
        bad = stale()
        for p in bad:
            print(f"STALE: {p} (run: python -m benchctl.progress)")
        return 1 if bad else 0
    for p in write():
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
