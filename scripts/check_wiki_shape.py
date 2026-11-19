"""Measure the readability of every page the wiki publishes, and fail on walls of text.

    python scripts/check_wiki_shape.py            check (exit 1 on a finding); run by validate.sh
    python scripts/check_wiki_shape.py --table    print the ranked audit table

Rules (docs/guides/github-wiki.md): no paragraph over MAX_PARA words, no section over MAX_RUN lines without a subheading,
and no paragraph or list item of a manual, guide or agent page over MAX_STEP words (a how-to is one action per line).
A page left on purpose goes in ALLOW below, with the reason; a page not yet rewritten goes in STEP_TODO, a list that only shrinks
(the test fails when a listed page already passes). Stdlib only.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GROUPS = ("manual", "tutorials", "guides", "architecture", "adr", "agents")
MAX_PARA = 150
MAX_RUN = 120
# One paragraph or list item in a how-to page: about four sentences of 17 words. The second pass (docs/wiki-second-pass) left
# 19 items above it in 11 pages and none above 110; 70 is the strictest round figure those pages and the rewritten ones can meet.
MAX_STEP = 70
STEP_GROUPS = ("manual", "tutorials", "guides", "agents")  # how-to pages; architecture explains and ADRs record, so they keep MAX_PARA only
STEP_SKIP = {"docs/agents/CHANGELOG.md": "a changelog entry is a record, not a procedure"}
# Not yet rewritten into one action per line. Remove a page when it passes; never add one (tests/test_wiki_shape.py).
STEP_TODO = (
    "docs/guides/brand-assets.md",
    "docs/guides/client-test-report-real-jira.md",
    "docs/guides/client-testing.md",
    "docs/guides/jira-tickets.md",
    "docs/guides/memory.md",
    "docs/guides/power-and-reboot.md",
    "docs/guides/slurm-resilience.md",
    "docs/guides/slurm-version.md",
    "docs/guides/web-ui-canvas.md",
    "docs/manual/01-what-it-is.md",
)
# page -> reason it is left as it is
ADR = "an ADR is a record of a decision; its wording is left as decided"
ALLOW: dict[str, str] = {
    "docs/adr/0009-traffic-dots-from-request-counters.md": ADR,
    "docs/adr/0025-slurm-is-real-the-rest-is-simulated.md": ADR,
    "docs/adr/0026-real-jira-data-center-as-an-optional-part.md": ADR,
    "docs/adr/0035-workspace-navigation.md": ADR,
    "docs/adr/0037-real-gpu-passthrough.md": ADR,
    "docs/adr/0042-on-demand-wire-capture.md": ADR,
    "docs/adr/0064-mcp-server-benchctl-serve-mcp.md": ADR,
    "docs/adr/0069-one-colour-system-from-one-palette-file.md": ADR,
    "docs/adr/0079-selectable-slurm-version-and-upgrade-scenario.md": ADR,
}


def measure(text: str) -> dict:
    lines = text.splitlines()
    in_code = False
    para: list[str] = []
    longest_para = run = longest_run = bullets = longest_bullets = 0
    heads = tables = code = imgs = 0
    run_start = 1
    worst_run_at = 1
    para_at = 1

    def flush(n: int):
        nonlocal para, longest_para, para_at
        w = len(" ".join(para).split())
        if w > longest_para:
            longest_para, para_at = w, n - len(para)
        para = []

    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith("```"):
            if not in_code:
                code += 1
            in_code = not in_code
            flush(i)
            run += 1
            continue
        if in_code:
            run += 1
            continue
        s = re.sub(r"^(>\s?)+", "", ln.strip())  # a quote or callout is read as its content
        if re.match(r"^#{1,6} ", s):
            heads += 1
            flush(i)
            if run > longest_run:
                longest_run, worst_run_at = run, run_start
            run, run_start = 0, i
            bullets = 0
            continue
        run += 1
        if "<img" in s or s.startswith("!["):
            imgs += 1
        if s.startswith("|"):
            if not (re.sub(r"^(>\s?)+", "", lines[i - 2].strip()).startswith("|") if i > 1 else False):
                tables += 1
            flush(i)
            continue
        if re.match(r"^([-*]|\d+\.) ", s):
            bullets += 1
            longest_bullets = max(longest_bullets, bullets)
            flush(i)
            continue
        if not s:
            flush(i)
            bullets = 0
            continue
        para.append(s)
    flush(len(lines) + 1)
    if run > longest_run:
        longest_run, worst_run_at = run, run_start
    return {"lines": len(lines), "words": len(text.split()), "para": longest_para, "para_at": para_at,
            "run": longest_run, "run_at": worst_run_at, "heads": heads, "tables": tables, "code": code,
            "imgs": imgs, "bullets": longest_bullets}


def long_steps(text: str) -> list[tuple[int, int]]:
    """(line, words) of every paragraph or list item over MAX_STEP words, outside code, tables, quotes and Source lines."""
    out: list[tuple[int, int]] = []
    cur: list[str] = []
    start = 0
    in_code = False

    def flush():
        nonlocal cur
        if cur and not cur[0].startswith("Source:") and len(" ".join(cur).split()) > MAX_STEP:
            out.append((start, len(" ".join(cur).split())))
        cur = []

    for i, ln in enumerate(text.splitlines(), 1):
        if ln.lstrip().startswith("```"):
            in_code = not in_code
            flush()
            continue
        s = ln.strip()
        if in_code or ln.startswith(">") or not s or s.startswith(("|", "<", "![")) or re.match(r"^#{1,6} ", s):
            flush()
            continue
        m = re.match(r"^(?:[-*]|\d+\.) (.*)", s)
        if m:
            flush()
            start, cur = i, [m.group(1)]
        else:
            if not cur:
                start = i
            cur.append(s)
    flush()
    return out


def pages() -> list[Path]:
    return sorted(p for g in GROUPS for p in (ROOT / "docs" / g).glob("*.md"))


def main() -> int:
    rows = []
    for p in pages():
        rel = p.relative_to(ROOT).as_posix()
        rows.append((rel, measure(p.read_text(encoding="utf-8"))))
    if "--table" in sys.argv:
        rows.sort(key=lambda r: -(r[1]["para"] / MAX_PARA + r[1]["run"] / MAX_RUN))
        print("| page | lines | words | longest paragraph | longest run, no heading | headings | tables | code | images | longest list |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for rel, m in rows:
            print(f"| {rel} | {m['lines']} | {m['words']} | {m['para']} | {m['run']} | {m['heads']} | {m['tables']} | {m['code']} | {m['imgs']} | {m['bullets']} |")
        return 0
    bad = 0
    for rel, m in rows:
        if rel in ALLOW:
            continue
        if m["para"] > MAX_PARA:
            print(f"{rel}:{m['para_at']}: paragraph of {m['para']} words (max {MAX_PARA}): split it, use a list or a table")
            bad += 1
        if m["run"] > MAX_RUN:
            print(f"{rel}:{m['run_at']}: {m['run']} lines without a subheading (max {MAX_RUN}): add short headings")
            bad += 1
    for p in pages():
        rel = p.relative_to(ROOT).as_posix()
        if p.parent.name not in STEP_GROUPS or rel in STEP_SKIP or rel in STEP_TODO or rel in ALLOW:
            continue
        for line, words in long_steps(p.read_text(encoding="utf-8")):
            print(f"{rel}:{line}: paragraph or list item of {words} words (max {MAX_STEP}): one action per line, numbered steps or bullets")
            bad += 1
    if bad:
        print(f"{bad} wiki-shape finding(s); fix the page or add it to ALLOW in scripts/check_wiki_shape.py with a reason")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
