"""Config lab import: parsers and the secret scrubber (issue 550, ADR 0049, SPEC C-24).

Every parser takes text and returns plain data; none touches the network or a production system. The scrubber replaces the
known secrets with `<scrubbed>`, then a residual scan (the token rules of scripts/check-privacy.sh plus assignments to
password-like names) refuses the import when anything secret-looking is left. It reports `file:line [rule]`, never the value.
"""
from __future__ import annotations

import re
from typing import Any

import yaml

SCRUBBED = "<scrubbed>"


class ImportError_(ValueError):
    """One message for one condition; the CLI prints it and exits 1."""


# ---------------------------------------------------------------- hostlists

_RANGE = re.compile(r"\[([^\]]+)\]")


def _split_top(text: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "["
        depth -= ch == "]"
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur] if cur else out


def expand_hostlist(text: str) -> list[str]:
    """`gpu[001-003,010],cpu1` -> names, in order (one bracket group per element, as Slurm writes them)."""
    names: list[str] = []
    for part in _split_top(text.strip()):
        m = _RANGE.search(part)
        if not m:
            if part:
                names.append(part)
            continue
        pre, post = part[:m.start()], part[m.end():]
        for piece in m.group(1).split(","):
            lo, _, hi = piece.partition("-")
            if not lo.isdigit() or (hi and not hi.isdigit()) or (hi and int(hi) < int(lo)):
                raise ImportError_(f"bad node list {text!r}")
            width = len(lo)
            for i in range(int(lo), int(hi or lo) + 1):
                names += expand_hostlist(f"{pre}{str(i).zfill(width)}{post}")
    return names


# ---------------------------------------------------------------- slurm.conf, topology.conf, gres.conf

def _logical_lines(text: str) -> list[tuple[int, str]]:
    out, buf, start = [], "", 0
    for n, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"(^|\s)#.*$", "", raw).rstrip()
        if not buf:
            start = n
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        if buf.strip():
            out.append((start, buf.strip()))
        buf = ""
    if buf.strip():
        out.append((start, buf.strip()))
    return out


def _pairs(line: str) -> dict[str, str]:
    """`A=1 B="x y"` -> {A: 1, B: x y}. A key repeated on one line keeps the last."""
    return {m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in re.finditer(r'([A-Za-z][A-Za-z0-9_]*)=(?:"([^"]*)"|(\S*))', line)}


MULTI = ("NodeName", "PartitionName", "NodeSet", "DownNodes", "SwitchName", "BlockName")


def parse_slurm_conf(text: str) -> dict[str, Any]:
    """{"keys": {Key: value}, "nodes": [...], "partitions": [...], "other": [...]}: one dict per NodeName/PartitionName/... line."""
    out: dict[str, Any] = {"keys": {}, "nodes": [], "partitions": [], "other": []}
    for n, line in _logical_lines(text):
        pairs = _pairs(line)
        if not pairs or "=" not in line:
            raise ImportError_(f"slurm.conf line {n} is not Key=Value")
        first = next(iter(pairs))
        if first == "NodeName":
            out["nodes"].append(pairs)
        elif first == "PartitionName":
            out["partitions"].append(pairs)
        elif first in MULTI:
            out["other"].append(pairs)
        else:
            out["keys"].update(pairs)
    return out


def parse_topology(text: str) -> list[dict[str, str]]:
    rows = [_pairs(line) for _, line in _logical_lines(text)]
    for r in rows:
        if not ({"SwitchName", "BlockName"} & set(r)):
            raise ImportError_("topology.conf: every line needs SwitchName= or BlockName=")
    return rows


def parse_gres(text: str) -> list[dict[str, str]]:
    rows = [_pairs(line) for _, line in _logical_lines(text)]
    for r in rows:
        if "Name" not in r and "AutoDetect" not in r:
            raise ImportError_("gres.conf: every line needs Name= (or AutoDetect=)")
    return rows


# ---------------------------------------------------------------- sacctmgr

_DUMP = re.compile(r"^(\w+) - '([^']*)'(.*)$")


def parse_sacctmgr_dump(text: str) -> dict[str, Any]:
    """`sacctmgr dump` -> {"cluster": name, "accounts": [{name, parent, opts}], "users": [{name, opts}]}; the parent comes from the last `Parent - 'x'`."""
    out: dict[str, Any] = {"cluster": None, "accounts": [], "users": []}
    parent = None
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = _DUMP.match(raw.strip())
        if not m:
            raise ImportError_(f"sacctmgr dump line {n} is not 'Kind - 'name':Key=Value'")
        kind, name, rest = m.groups()
        opts = {k: v.strip("'") for k, v in re.findall(r":([A-Za-z]+)=('[^']*'|[^:]*)", rest)}
        if kind == "Cluster":
            out["cluster"] = name
        elif kind == "Parent":
            parent = name
        elif kind == "Account":
            out["accounts"].append({"name": name, "parent": parent, "opts": opts})
        elif kind == "User":
            out["users"].append({"name": name, "opts": opts})
        else:
            raise ImportError_(f"sacctmgr dump line {n}: unknown record kind {kind!r}")
    if not out["accounts"] and not out["users"]:
        raise ImportError_("sacctmgr dump holds no accounts or users")
    return out


def parse_parsable(text: str, required: tuple[str, ...], what: str) -> tuple[list[str], list[dict[str, str]]]:
    """A `--parsable2` table (`|` separated, header first, a trailing `|` tolerated)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ImportError_(f"{what} is empty")
    head = lines[0].rstrip("|").split("|")
    missing = [f for f in required if f not in head]
    if missing:
        raise ImportError_(f"{what} lacks the column(s) {', '.join(missing)}")
    rows = []
    for n, ln in enumerate(lines[1:], 2):
        cells = ln.split("|")
        if len(cells) == len(head) + 1 and cells[-1] == "":
            cells.pop()
        if len(cells) != len(head):
            raise ImportError_(f"{what} line {n} has {len(cells)} columns, the header has {len(head)}")
        rows.append(dict(zip(head, cells)))
    return head, rows


QOS_REQUIRED = ("Name",)
TRACE_REQUIRED = ("JobID", "User", "Account", "Partition", "Submit", "Elapsed", "State")
TRACE_ONE_OF = ("AllocTRES", "ReqTRES")


def parse_qos(text: str) -> list[dict[str, str]]:
    return parse_parsable(text, QOS_REQUIRED, "QOS export")[1]


def parse_trace(text: str) -> dict[str, Any]:
    """A `sacct --parsable2` export: job rows kept, step rows (`123.batch`) counted and dropped."""
    head, rows = parse_parsable(text, TRACE_REQUIRED, "sacct trace")
    if not set(TRACE_ONE_OF) & set(head):
        raise ImportError_(f"sacct trace needs the column {' or '.join(TRACE_ONE_OF)}")
    jobs = [r for r in rows if "." not in r["JobID"]]
    return {"columns": head, "jobs": jobs, "steps_dropped": len(rows) - len(jobs)}


# ---------------------------------------------------------------- Kubernetes, Prometheus, Teleport (YAML)

K8S_KINDS = ("Namespace", "DeviceClass", "ResourceClaimTemplate", "PriorityClass", "ResourceQuota", "LimitRange",
             "PodDisruptionBudget", "FlowSchema", "PriorityLevelConfiguration", "Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding")
TELEPORT_KINDS = ("role",)


def load_yaml(text: str, what: str):
    try:
        return yaml.safe_load(text) if text.strip() else None
    except yaml.YAMLError as e:
        raise ImportError_(f"{what} is not valid YAML") from e


def _docs(text: str, what: str) -> list[dict]:
    try:
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as e:
        raise ImportError_(f"{what} is not valid YAML") from e
    out = []
    for d in docs:
        if not isinstance(d, dict):
            raise ImportError_(f"{what} holds a document that is not a mapping")
        out += d.get("items", []) if d.get("kind") == "List" else [d]
    return out


def parse_k8s(text: str) -> tuple[list[dict], dict[str, int]]:
    """(allow-listed objects, {kind: count dropped}). A Secret is dropped here and never stored."""
    kept, dropped = [], {}
    for d in _docs(text, "Kubernetes manifest"):
        kind = str(d.get("kind"))
        if kind in K8S_KINDS and (d.get("metadata") or {}).get("name"):
            kept.append(d)
        else:
            dropped[kind] = dropped.get(kind, 0) + 1
    return kept, dropped


def parse_rules(text: str) -> list[dict]:
    doc = load_yaml(text, "Prometheus rule file")
    groups = doc.get("groups") if isinstance(doc, dict) else None
    if not isinstance(groups, list) or not groups:
        raise ImportError_("Prometheus rule file needs a non-empty `groups:` list")
    for g in groups:
        if not isinstance(g, dict) or not g.get("name") or not isinstance(g.get("rules"), list):
            raise ImportError_("Prometheus rule group needs a name and a `rules:` list")
        for r in g["rules"]:
            if not isinstance(r, dict) or not r.get("expr") or not ({"alert", "record"} & set(r)):
                raise ImportError_(f"rule group {g['name']}: every rule needs `alert:` or `record:` and `expr:`")
    return groups


def parse_teleport(text: str) -> tuple[list[dict], dict[str, int]]:
    """(roles, {kind: count dropped}). Tokens and other kinds are dropped."""
    kept, dropped = [], {}
    for d in _docs(text, "Teleport resource"):
        kind = str(d.get("kind"))
        if kind in TELEPORT_KINDS and (d.get("metadata") or {}).get("name"):
            kept.append(d)
        else:
            dropped[kind] = dropped.get(kind, 0) + 1
    return kept, dropped


# ---------------------------------------------------------------- scrubber

SECRET_SLURM_KEYS = re.compile(r"^(StoragePass|AccountingStoragePass|JobCompPass|JwtKey|AuthInfo|CredType|"
                               r"AuthAltParameters|MungeKey|[A-Za-z]*(Pass|Password|Secret|Token)[A-Za-z]*)$", re.IGNORECASE)
_MUNGE = re.compile(r"(?i)(/[\w./-]*munge[\w./-]*\.key|munge[_.-]?key)")

# The token rules of scripts/check-privacy.sh section 4 (tests/test_lab_import.py checks they stay in step).
TOKEN_RULES: tuple[tuple[str, str], ...] = (
    ("github-token", r"gh[pousr]_[A-Za-z0-9]{20,}"), ("github-pat", r"github_pat_[A-Za-z0-9_]{20,}"),
    ("gitlab-token", r"glpat-[A-Za-z0-9_-]{20,}"), ("aws-key", r"AKIA[0-9A-Z]{16}"),
    ("slack-token", r"xox[baprs]-[A-Za-z0-9-]{10,}"), ("google-key", r"AIza[0-9A-Za-z_-]{35}"),
    ("api-key", r"(^|[^A-Za-z0-9])sk-[A-Za-z0-9_-]{32,}"),
    ("jwt", r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KE" + r"Y-----"),
    ("url-credential", r"https?://[^/\s]*:[^@/\s]+@"),
)
_ASSIGN = re.compile(r"""(?ix)\b([\w-]*(?:pass(?:word|wd)?|secret|token|api[_-]?key|private[_-]?key|credential)[\w-]*)\b["']?\s*[=:]\s*["']?([^\s"',;]+)""")
_PLACEHOLDER = re.compile(r"^(<scrubbed>|<[^>]*>|\*+|null|none|~|true|false|\d+)$", re.IGNORECASE)


def scrub_slurm_conf(text: str) -> tuple[str, int]:
    """Replace the value of every secret key; returns (text, number of values replaced)."""
    n = 0

    def fix(m: re.Match) -> str:
        nonlocal n
        key, val = m.group(1), m.group(2)
        if SECRET_SLURM_KEYS.match(key) and val != SCRUBBED:
            n += 1
            return f"{key}={SCRUBBED}"
        if _MUNGE.search(val):
            n += 1
            return f"{key}={SCRUBBED}"
        return m.group(0)

    out = re.sub(r'([A-Za-z][A-Za-z0-9_]*)=("[^"]*"|\S*)', fix, text)
    return out, n


def scrub_text(text: str, kind: str) -> tuple[str, int]:
    return scrub_slurm_conf(text) if kind == "slurm.conf" else (text, 0)


def residual_secrets(files: dict[str, str]) -> list[str]:
    """`file:line [rule]` for every secret-looking thing left in `files` ({stored name: text}); never the value."""
    hits = []
    for name, text in sorted(files.items()):
        for n, line in enumerate(text.splitlines(), 1):
            for rule, rx in TOKEN_RULES:
                if re.search(rx, line):
                    hits.append(f"{name}:{n} [{rule}]")
            for m in _ASSIGN.finditer(line):
                if not _PLACEHOLDER.match(m.group(2)):
                    hits.append(f"{name}:{n} [secret-assignment]")
    return sorted(set(hits))
