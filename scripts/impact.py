#!/usr/bin/env python3
"""Which live-test modules does this change need (issue 576, V-2)? Rules: tests/real/impact.yaml.

    python3 scripts/impact.py [--base origin/dev] [--explain]     one module per line, or ALL
    python3 scripts/impact.py --files a b --explain               rules for a given file list, no git

Fail safe: a file no rule matches, or an unreadable rules file, selects ALL."""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "tests" / "real" / "impact.yaml"
ALL = "ALL"


def load_rules(path: Path = RULES) -> list[dict]:
    rules = yaml.safe_load(path.read_text())
    if not isinstance(rules, list) or not all(isinstance(r, dict) and "paths" in r and "tests" in r for r in rules):
        raise ValueError(f"{path}: expected a list of rules with `paths` and `tests`")
    return rules


def _regex(glob: str) -> re.Pattern:
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif glob[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(glob[i]), i + 1
    return re.compile(out + r"\Z")


def matches(path: str, glob: str) -> bool:
    return bool(_regex(glob).match(path)) or (glob.endswith("/**") and path == glob[:-3])


def own_module(path: str) -> str | None:
    m = re.fullmatch(r"tests/real/(test_[A-Za-z0-9_]+)\.py", path)
    return m.group(1) if m else None


def rule_for(path: str, rules: list[dict]) -> dict | None:
    return next((r for r in rules if any(matches(path, g) for g in r["paths"])), None)


def select(files: list[str], rules: list[dict]) -> tuple[str | list[str], list[str]]:
    """(ALL or the sorted module names, one explanation line per file)."""
    mods: set[str] = set()
    why: list[str] = []
    everything = False
    for f in sorted(set(files)):
        own = own_module(f)
        rule = None if own else rule_for(f, rules)
        if own:
            mods.add(own)
            why.append(f"{f} -> its own module {own}")
        elif rule is None:
            everything = True
            why.append(f"{f} -> ALL (no impact rule for {f})")
        elif rule["tests"] == ALL:
            everything = True
            why.append(f"{f} -> ALL (rule: {rule.get('name', rule['paths'][0])})")
        else:
            mods.update(rule["tests"])
            why.append(f"{f} -> {', '.join(rule['tests']) or 'no live tests'} (rule: {rule.get('name', rule['paths'][0])})")
    return (ALL if everything else sorted(mods)), why


def changed(base: str = "origin/dev") -> list[str]:
    """Committed since the merge base, staged, unstaged and untracked: local work counts."""
    def git(*a: str) -> str:
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    mb = git("merge-base", base, "HEAD").strip()
    names = git("diff", "--name-only", mb).splitlines() + git("ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({n for n in names if n})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/dev")
    ap.add_argument("--files", nargs="*", help="use this file list instead of git")
    ap.add_argument("--explain", action="store_true", help="print file -> rule -> modules, then the selection")
    a = ap.parse_args(argv)
    try:
        files = a.files if a.files is not None else changed(a.base)
        sel, why = select(files, load_rules())
    except (subprocess.CalledProcessError, OSError, ValueError, yaml.YAMLError) as e:
        print(f"# impact: {e}; selecting everything", file=sys.stderr)
        print(ALL)
        return 0
    if a.explain:
        print("\n".join(why) or "(no changed files)")
    print(ALL if sel == ALL else "\n".join(sel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
