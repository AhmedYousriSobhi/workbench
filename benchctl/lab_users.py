"""The lab's stable user mapping, production user -> `lab-uNNN` (issue 869, owner decision 5 on issue 554).

One file per lab, `compose/generated/lab/NAME/users.json` (mode 600, gitignored). It covers the dump's users plus the trace's
users, is numbered once by sorted name and then only grows: a new name goes at the end, a number is never reused. `apply lab`
rewrites the sacctmgr dump with it, every `run lab` uses it, so the same person is the same `lab-uNNN` whatever the seed or
`--target-hours`. Only the anonymizer ever reads the production side of it.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

UID_BASE = 30000  # lab-u001 -> 30001: above the simulated users' uids (1001+); the same in images/slurm-ctl/lab_driver.py
SYSTEM_USERS = {"root"}
_NAME = re.compile(r"lab-u(\d{3,6})")
# the services that must know the Linux users (slurmdbd needs one for every name it loads; the lab nodes do the same in issue 863)
SERVICES = ("slurmctld", "slurmdbd", "login")


class UsersError(Exception):
    pass


def uid(user: str) -> int:
    m = _NAME.fullmatch(user)
    if not m:
        raise UsersError(f"{user!r} is not a lab user name (lab-uNNN)")
    return UID_BASE + int(m.group(1))


def users_path(name: str, root: Path | None) -> Path:
    return _lab_dir(name, root) / "users.json"


def _lab_dir(name: str, root: Path | None) -> Path:
    from benchctl import lab  # late: lab imports nothing from here
    return lab.path(name, root)


def load(name: str, root: Path | None = None) -> dict[str, str]:
    p = users_path(name, root)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text())
    except ValueError as e:
        raise UsersError(f"{p.name} of lab {name} is not valid JSON ({e}); remove it and apply again to number the users anew") from e
    return {str(k): str(v) for k, v in data.items()}


def ensure(name: str, wanted: set[str], root: Path | None = None, write: bool = True) -> dict[str, str]:
    """The mapping with every name in `wanted` in it. Existing entries never change; new names are numbered after the last, in sorted order."""
    cur = load(name, root)
    new = sorted(n for n in wanted - set(cur) if n)
    if new:
        last = max([int(_NAME.fullmatch(v).group(1)) for v in cur.values()] or [0])  # type: ignore[union-attr]
        width = max(3, len(str(last + len(new))))
        for i, n in enumerate(new, last + 1):
            cur[n] = f"lab-u{i:0{width}d}"
        if not write:
            return cur
        p = users_path(name, root)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(cur, indent=2, sort_keys=True) + "\n")
        tmp.replace(p)
    return cur


def baseline_users(name: str, root: Path | None = None) -> set[str]:
    """Every user of the imported dump and trace (root excluded)."""
    from benchctl import lab, lab_import
    base = lab.path(name, root) / "baseline"
    out = {j["User"] for j in lab.load_baseline(name, root)["trace"] if j.get("User")}  # a trace job of root is replayed as a lab user too
    if (base / "sacctmgr.dump").is_file():
        out |= {u["name"] for u in lab_import.parse_sacctmgr_dump((base / "sacctmgr.dump").read_text())["users"]} - SYSTEM_USERS
    return out


def for_lab(name: str, root: Path | None = None, write: bool = True) -> dict[str, str]:
    return ensure(name, baseline_users(name, root), root, write)


def _word(n: str) -> re.Pattern:
    return re.compile(r"(?<![\w.@-])" + re.escape(n) + r"(?![\w.@-])")


def rewrite_dump(text: str, mapping: dict[str, str]) -> str:
    """The sacctmgr dump with every production user name replaced: `User - 'x'`, Coordinators values and the free text of Description/Organization."""
    mapping = {k: v for k, v in mapping.items() if k not in SYSTEM_USERS}  # root stays root in accounting
    if not mapping:
        return text
    names = sorted(mapping, key=len, reverse=True)
    pats = [(_word(n), mapping[n]) for n in names]

    def sub(s: str) -> str:
        for p, to in pats:
            s = p.sub(to, s)
        return s

    out = []
    for raw in text.splitlines():
        m = re.match(r"^(\s*User - ')([^']*)(')(.*)$", raw)
        if m:
            pre, nm, q, rest = m.groups()
            out.append(f"{pre}{mapping.get(nm, nm)}{q}{sub_values(rest, sub)}")
        elif re.match(r"^\s*(Account|Parent) - '", raw):
            m2 = re.match(r"^(\s*\w+ - ')([^']*)(')(.*)$", raw)
            pre, nm, q, rest = m2.groups()  # type: ignore[union-attr]
            out.append(f"{pre}{nm}{q}{sub_values(rest, sub)}")
        else:
            out.append(raw)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def sub_values(rest: str, sub) -> str:
    """Rewrite only the values of the options that can hold a person's name."""
    return re.sub(r":(Coordinators|Description|Organization)=('[^']*'|[^:]*)", lambda m: f":{m.group(1)}={sub(m.group(2))}", rest)


def setup_script(mapping: dict[str, str]) -> str:
    """A shell script that creates the Linux users (idempotent, no home directory); the same uid rule everywhere."""
    lines = ["set -u"]
    for lab_user in sorted(set(mapping.values())):
        lines.append(f"id {lab_user} >/dev/null 2>&1 || useradd -M -u {uid(lab_user)} -s /bin/sh {lab_user}")
    return "\n".join(lines) + "\n"


def teardown_script(mapping: dict[str, str]) -> str:
    return "set -u\n" + "".join(f"id {u} >/dev/null 2>&1 && userdel {u}\n" for u in sorted(set(mapping.values()))) + "true\n"
