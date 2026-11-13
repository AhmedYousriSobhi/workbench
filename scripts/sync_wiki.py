"""Generate the repository's GitHub Wiki from docs/ (the single source), and optionally push it.

    python scripts/sync_wiki.py --out DIR            write the wiki pages into DIR
    python scripts/sync_wiki.py --push               clone the wiki repository, sync it, commit and push

One wiki page per markdown file in docs/manual, docs/guides, docs/architecture and docs/adr; `Home` is the manual's
start page, `_Sidebar` lists everything. Links between pages become wiki links, images are copied to images/, and a link
to a file outside docs/ (SPEC.md, sim.yaml, source files) becomes a link to that file in the repository. Never edit the
wiki by hand: the next sync overwrites it.
GitHub creates the wiki repository only when its first page is saved in the web UI; --push says so when it is missing.
"""
import argparse
import base64
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from webui.wiki import (  # stdlib-only module: reuses the page titles and the docs layout
    IMAGES,
    Wiki,
)

PREFIX = {"manual": "Manual", "guides": "Guide", "architecture": "Architecture", "adr": "ADR"}
# the two project callout kinds GitHub does not know: shown as a quote headed by a bold label
PROJECT_CALLOUTS = {"HOOD": "**🔧 Under the hood**", "DIDYOUKNOW": "**💡 Did you know?**"}
FOOTER = "Generated from `docs/` in the repository by `scripts/sync_wiki.py`. Edit the files there, not this wiki."


def page_name(slug: str) -> str:
    """`manual/04-nodes-and-shells` -> `Manual-04-nodes-and-shells`; each group's README is its index page."""
    group, stem = slug.split("/")
    if group == "manual" and stem == "README":
        return "Home"
    return PREFIX[group] if stem == "README" else f"{PREFIX[group]}-{stem}"


def image_name(rel: str) -> str:
    """`manual/img/web-overview.png` -> `images/manual-web-overview.png`"""
    if rel.startswith("assets/art/"):
        return f"images/art-{rel.rsplit('/', 1)[1]}"
    group, _, name = rel.split("/", 2)
    return f"images/{group}-{name}"


def rewrite(text: str, page: str, docs: Path, blob: str) -> str:
    """Point every relative link at a wiki page, a copied image, or the file in the repository."""
    def fix(m: re.Match) -> str:
        bang, label, target = m.group(1), m.group(2), m.group(3)
        if re.match(r"^([a-z]+:|#|//)", target):
            return m.group(0)
        path, _, frag = target.partition("#")
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(page), path))
        in_docs = not rel.startswith("..")
        suffix = posixpath.splitext(rel)[1]
        suffix_frag = f"#{frag}" if frag else ""
        if in_docs and suffix == ".md" and rel.split("/")[0] in PREFIX and (docs / rel).is_file():
            return f"{bang}[{label}]({page_name(rel[:-3])}{suffix_frag})"
        if in_docs and suffix in IMAGES and (docs / rel).is_file():
            return f"{bang}[{label}]({image_name(rel)})"
        repo_rel = posixpath.normpath(posixpath.join("docs", posixpath.dirname(page), path))
        if repo_rel.startswith(".."):
            return m.group(0)
        return f"{bang}[{label}]({blob}/{repo_rel}{suffix_frag})"

    def fix_attr(m: re.Match) -> str:
        """`src="..."` and `srcset="..."` of <img>, <source>: an image in docs/ is copied, any other file is read from the repository."""
        attr, target = m.group(1), m.group(2)
        if re.match(r"^([a-z]+:|#|//)", target):
            return m.group(0)
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(page), target))
        if not rel.startswith("..") and posixpath.splitext(rel)[1] in IMAGES and (docs / rel).is_file():
            return f'{attr}="{image_name(rel)}"'
        repo_rel = posixpath.normpath(posixpath.join("docs", posixpath.dirname(page), target))
        return m.group(0) if repo_rel.startswith("..") else f'{attr}="{blob}/{repo_rel}?raw=true"'

    out, fenced = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if fenced:
            out.append(line)
            continue
        line = re.sub(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)", fix, line)
        line = re.sub(r"(?<=[\s<])(src|srcset)=\"([^\"]+)\"", fix_attr, line)
        if m := re.match(r"^>\s*\[!(" + "|".join(PROJECT_CALLOUTS) + r")\]\s*$", line, re.IGNORECASE):
            line = f"> {PROJECT_CALLOUTS[m.group(1).upper()]}\n>"
        out.append(line)
    return "\n".join(out) + "\n"


def generate(docs: Path, out: Path, blob: str) -> list[str]:
    """Write the wiki into `out`; returns the page names."""
    wiki = Wiki(docs)
    out.mkdir(parents=True, exist_ok=True)
    (out / "images").mkdir(exist_ok=True)
    names, side = [], ["**[Home](Home)**", ""]
    for label, items in wiki.nav():
        group_lines = []
        for slug, title in items:
            name = page_name(slug)
            (out / f"{name}.md").write_text(rewrite((docs / f"{slug}.md").read_text(encoding="utf-8"), f"{slug}.md", docs, blob),
                                            encoding="utf-8")
            names.append(name)
            if name != "Home":
                group_lines.append(f"- [{title}]({name})")
        side += [f"**{label}**", "", *group_lines, ""]
    for group in PREFIX:
        for img in (docs / group / "img").glob("*"):
            if img.suffix in IMAGES:
                shutil.copyfile(img, out / image_name(f"{group}/img/{img.name}"))
    for img in (docs / "assets" / "art").glob("*"):  # the generated illustrations (scripts/make_doc_art.py)
        if img.suffix in IMAGES:
            shutil.copyfile(img, out / image_name(f"assets/art/{img.name}"))
    (out / "_Sidebar.md").write_text("\n".join(side), encoding="utf-8")
    (out / "_Footer.md").write_text(FOOTER + "\n", encoding="utf-8")
    return names


def repo_urls() -> tuple[str, str]:
    """(public URL without credentials, the same with the credentials from origin, for pushing; never printed)."""
    r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True, check=True)
    private = r.stdout.strip().removesuffix(".git")
    return re.sub(r"//[^@/]*@", "//", private), private


def auth_args(public: str, private: str) -> list[str]:
    """CI: the checkout keeps no credentials in origin, so send GITHUB_TOKEN as a header (as actions/checkout does)."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token or public != private:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraheader=Authorization: Basic {basic}"]


def push(docs: Path, public: str, private: str) -> int:
    blob = public + "/blob/main"
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    url = private + ".wiki.git"

    def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        r = subprocess.run(["git", *auth_args(public, private), *args], cwd=cwd, env=env, capture_output=True, text=True, check=False)
        r.stderr = r.stderr.replace(private, "<repo>")  # never echo the token
        if (token := os.environ.get("GITHUB_TOKEN")):
            r.stderr = r.stderr.replace(token, "***").replace(base64.b64encode(f"x-access-token:{token}".encode()).decode(), "***")
        return r

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "wiki"
        if git("clone", "-q", url, str(work)).returncode:
            print(f"the wiki repository does not exist yet: open {public}/wiki and save a first page (any text), then rerun",
                  file=sys.stderr)
            return 1
        for p in work.iterdir():
            if p.name != ".git":
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        names = generate(docs, work, blob)
        git("add", "-A", cwd=work)
        if not git("status", "--porcelain", cwd=work).stdout.strip():
            print(f"wiki already up to date ({len(names)} pages)")
            return 0
        c = git("commit", "-q", "-m", "Sync the wiki from docs/", cwd=work)
        p = git("push", "-q", cwd=work) if c.returncode == 0 else c
        if p.returncode:
            print(p.stderr.strip(), file=sys.stderr)
            return 1
        print(f"wiki updated: {len(names)} pages, {public}/wiki")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, help="write the wiki pages into this directory")
    ap.add_argument("--push", action="store_true", help="clone the wiki repository, sync, commit and push")
    ap.add_argument("--docs", type=Path, default=ROOT / "docs")
    args = ap.parse_args()
    if bool(args.out) == args.push:
        ap.error("give exactly one of --out DIR or --push")
    public, private = repo_urls()
    blob = public + "/blob/main"
    if args.push:
        return push(args.docs, public, private)
    names = generate(args.docs, args.out, blob)
    print(f"{len(names)} pages written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
