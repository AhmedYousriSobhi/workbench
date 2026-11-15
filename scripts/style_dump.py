"""Prove a stylesheet change leaves every colour alone (issue 644). Needs the browser extra and Chromium (see CLAUDE.md, UI debugging).

    python scripts/style_dump.py dump OUT.json [--root DIR]   computed colours of every element: 4 themes x home and each workspace, plus login
    python scripts/style_dump.py compare A.json B.json        print the elements whose colours differ (exit 1 if any)

Take A from a clean checkout of the old commit (a git worktree with the same compose/ state) and B from the new one. A live link that
flickers between runs shows up in two runs of the same code; ignore elements that also differ there."""
import contextlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import ui_shots as U
from playwright.sync_api import Error as PE
from playwright.sync_api import sync_playwright

from webui.app import login_url

PROPS = ["color","backgroundColor","borderTopColor","borderRightColor","borderBottomColor","borderLeftColor","outlineColor","fill","stroke","stopColor","boxShadow","textDecorationColor","caretColor","backgroundImage","filter","floodColor"]
JS = """(props) => { const out = {}; const all = document.querySelectorAll('*');
 const path = el => { const p=[]; while (el && el.nodeType===1 && el!==document.documentElement){ let i=1,s=el; while((s=s.previousElementSibling)) if(s.tagName===el.tagName) i++; p.unshift(el.tagName.toLowerCase()+(el.id?'#'+el.id:'')+':'+i); el=el.parentElement;} return p.join('>'); };
 for (const el of all) { if (['SCRIPT','STYLE','HEAD','META','LINK','TITLE'].includes(el.tagName)) continue;
  const cs = getComputedStyle(el); const o = {}; for (const p of props) o[p] = cs[p]; out[path(el)] = o; } return out; }"""
def dump(out):
    port = U.free_port()
    proc = subprocess.Popen([sys.executable, "-m", "webui", str(port)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    res = {}
    try:
        base = proc.stdout.readline().split("UI: ")[1].strip().removesuffix("/login")
        tok = (ROOT / "compose/generated/secrets/ui_token").read_text().strip()
        with sync_playwright() as p:
            b = p.chromium.launch()
            for theme in ("light", "dark", "midnight", "dim"):
                pg = b.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce", bypass_csp=True)
                pg.add_init_script(U.TEST_HOOK); pg.add_init_script(f"localStorage.setItem('workbench-theme','{theme}')")
                pg.goto(login_url(base, tok)); pg.wait_for_timeout(1500)
                pg.wait_for_function("typeof setTree === 'function'", timeout=15000)
                pg.evaluate(U.SYNTH_CLUSTER); pg.add_style_tag(content="*,*::before,*::after{transition:none!important;animation:none!important}"); pg.wait_for_timeout(1500)
                res[f"{theme}|home"] = pg.evaluate(JS, PROPS)
                for i, tab in enumerate(pg.locator("nav a, nav button, [role=tab]").all()[:12]):
                    with contextlib.suppress(PE):
                        tab.click(timeout=1500); pg.wait_for_timeout(900)
                        res[f"{theme}|tab{i}"] = pg.evaluate(JS, PROPS)
                pg.close()
            for theme in ("light", "dark"):
                pg = b.new_page(color_scheme=theme, reduced_motion="reduce")
                pg.goto(base + "/login"); pg.wait_for_timeout(400)
                res[f"{theme}|login"] = pg.evaluate(JS, PROPS); pg.close()
            b.close()
    finally:
        proc.terminate()
    Path(out).write_text(json.dumps(res, sort_keys=True))
    print({k: len(v) for k, v in res.items()})

def compare(fa, fb) -> int:
    a, b = json.loads(Path(fa).read_text()), json.loads(Path(fb).read_text())
    n = 0
    for k in sorted(set(a) | set(b)):
        if k not in a or k not in b:
            print("view missing:", k)
            n += 1
            continue
        for el in sorted(set(a[k]) & set(b[k])):
            d = {p: (a[k][el][p], b[k][el][p]) for p in a[k][el] if a[k][el][p] != b[k][el][p]}
            if d:
                n += 1
                print(k, el[-80:], dict(list(d.items())[:2]))
    print("elements with differing colour:", n)
    return int(n > 0)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv[:1] == ["dump"]:
        if "--root" in argv:
            ROOT = Path(argv[argv.index("--root") + 1]).resolve()
        dump(argv[1])
    elif argv[:1] == ["compare"] and len(argv) == 3:
        sys.exit(compare(argv[1], argv[2]))
    else:
        sys.exit(__doc__)
