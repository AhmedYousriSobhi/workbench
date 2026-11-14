"""Config lab store and import (issue 550, ADR 0049, SPEC C-24): `benchctl import lab NAME --from DIR`.

The operator exports files on the production side and copies the folder here; nothing connects to production. Recognised names:

    slurm.conf  topology.conf  gres.conf  sacctmgr.dump  qos.txt  trace.sacct   (one file each)
    k8s/*.yaml  rules/*.yml (rule files; `*.test.yml` are promtool unit tests)  teleport/*.yaml  versions.yaml

Everything is scrubbed, checked for leftover secrets, and written under compose/generated/lab/NAME/ (gitignored, refused by
scripts/check-privacy.sh if staged) with a manifest.json. The import is refused, and nothing is written, if a secret remains.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

import yaml

from . import lab_import as li
from .lab_import import ImportError_ as LabError

ROOT = Path("compose/generated/lab")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
VERSION_KEYS = ("slurm", "kubernetes", "prometheus", "teleport")
MAX_FILE_BYTES = 200 * 1024 * 1024  # a year of sacct output for a large site is tens of MB
SINGLE = {"slurm.conf": "slurm.conf", "topology.conf": "topology", "gres.conf": "gres", "sacctmgr.dump": "sacctmgr",
          "qos.txt": "qos", "trace.sacct": "trace"}
FOLDERS = {"k8s": ("*.yaml", "*.yml"), "rules": ("*.yml", "*.yaml"), "teleport": ("*.yaml", "*.yml")}
NOT_TESTED = ("cgroup.conf", "job_submit.lua", "prolog and epilog scripts", "Grafana dashboards", "Kubernetes workloads and controllers",
              "Secrets", "admission webhooks", "unknown CRDs", "Alertmanager routing")


def valid_name(name: str) -> str:
    if not NAME.fullmatch(name):
        raise LabError(f"lab name {name!r} must be lowercase letters, digits or -, starting with a letter or digit (at most 41)")
    return name


def path(name: str, root: Path | None = None) -> Path:
    return (root or ROOT) / valid_name(name)


def exists(name: str, root: Path | None = None) -> bool:
    return (path(name, root) / "manifest.json").is_file()


def names(root: Path | None = None) -> list[str]:
    base = root or ROOT
    return sorted(p.parent.name for p in base.glob("*/manifest.json")) if base.is_dir() else []


def _read(p: Path) -> str:
    if p.stat().st_size > MAX_FILE_BYTES:
        raise LabError(f"{p.name} is larger than {MAX_FILE_BYTES // 2**20} MB")
    try:
        return p.read_text()
    except UnicodeDecodeError as e:
        raise LabError(f"{p.name} is not text (UTF-8)") from e


def _versions(src: Path) -> dict[str, str | None]:
    f = src / "versions.yaml"
    out: dict[str, str | None] = dict.fromkeys(VERSION_KEYS)
    if not f.is_file():
        return out
    try:
        data = yaml.safe_load(f.read_text()) or {}
    except yaml.YAMLError as e:
        raise LabError("versions.yaml is not valid YAML") from e
    if not isinstance(data, dict):
        raise LabError("versions.yaml must be a mapping such as `slurm: 25.11.9`")
    unknown = sorted(set(data) - set(VERSION_KEYS))
    if unknown:
        raise LabError(f"versions.yaml: unknown key(s) {', '.join(map(str, unknown))} (use {', '.join(VERSION_KEYS)})")
    return {k: (None if data.get(k) is None else str(data[k])) for k in VERSION_KEYS}


def workbench_versions(sim: dict) -> dict[str, str | None]:
    """What this workbench runs, for the version-match banner (Prometheus has no pin in sim.yaml)."""
    from . import slurmver

    return {"slurm": slurmver.version(sim), "kubernetes": (sim.get("kubernetes") or {}).get("version"),
            "prometheus": None, "teleport": (sim.get("teleport") or {}).get("version")}


def _norm(v: str | None) -> str | None:
    return v.lstrip("v") if v else v


def version_match(prod: dict, ours: dict) -> dict[str, str]:
    """{component: same | differs | unknown}: `differs` marks the affected results indicative (ADR 0049)."""
    return {k: "unknown" if not prod.get(k) or not ours.get(k) else "same" if _norm(prod[k]) == _norm(ours[k]) else "differs" for k in VERSION_KEYS}


def _counts(kind: str, data) -> dict:
    if kind == "slurm.conf":
        return {"keys": len(data["keys"]), "nodes": sum(len(li.expand_hostlist(n["NodeName"])) for n in data["nodes"]),
                "partitions": len(data["partitions"])}
    if kind == "sacctmgr":
        return {"accounts": len(data["accounts"]), "users": len(data["users"])}
    if kind == "trace":
        return {"jobs": len(data["jobs"]), "steps_dropped": data["steps_dropped"]}
    return {"rows": len(data)}


def import_lab(name: str, src: Path, root: Path | None = None, replace: bool = False, sim: dict | None = None) -> dict:
    """Parse, scrub and store a folder of exports; returns the manifest. Nothing is written unless every check passes."""
    dest = path(name, root)
    src = Path(src)
    if not src.is_dir():
        raise LabError(f"{src.name or src}: no such folder (export the files on the production side and copy the folder here)")
    if exists(name, root) and not replace:
        raise LabError(f"lab {name} already exists: pick another name, or add --replace to overwrite it")
    stored: dict[str, str] = {}  # stored relative name -> scrubbed text
    files: list[dict] = []
    scrubbed: list[dict] = []
    dropped: dict[str, int] = {}
    seen_names: set[str] = set()

    def keep(rel: str, text: str, kind: str, summary: dict, source_text: str) -> None:
        stored[rel] = text
        files.append({"file": rel, "kind": kind, "sha256": hashlib.sha256(source_text.encode()).hexdigest(), **summary})

    for fname, kind in SINGLE.items():
        p = src / fname
        if not p.is_file():
            continue
        raw = _read(p)
        text, n = li.scrub_text(raw, "slurm.conf" if kind == "slurm.conf" else kind)
        data = {"slurm.conf": li.parse_slurm_conf, "topology": li.parse_topology, "gres": li.parse_gres,
                "sacctmgr": li.parse_sacctmgr_dump, "qos": li.parse_qos, "trace": li.parse_trace}[kind](text)
        if n:
            scrubbed.append({"file": fname, "values": n})
        keep(fname, text, kind, _counts(kind, data), raw)
        seen_names.add(fname)
    for folder, patterns in FOLDERS.items():
        found = sorted({p for pat in patterns for p in (src / folder).glob(pat)}) if (src / folder).is_dir() else []
        for p in found:
            rel = f"{folder}/{p.name}"
            raw = _read(p)
            d: dict[str, int] = {}
            text, kind, summary = raw, folder, {}
            if folder == "k8s":
                objs, d = li.parse_k8s(raw)
                text, kind, summary = yaml.safe_dump_all(objs, sort_keys=False), "kubernetes", {"objects": len(objs), "kinds": sorted({o["kind"] for o in objs})}
            elif folder == "teleport":
                objs, d = li.parse_teleport(raw)
                text, summary = yaml.safe_dump_all(objs, sort_keys=False), {"roles": len(objs)}
            elif re.search(r"\.test\.ya?ml$", p.name):
                doc = li.load_yaml(raw, rel)
                if not isinstance(doc, dict) or not isinstance(doc.get("tests"), list):
                    raise LabError(f"{rel}: a rule test file needs a `tests:` list")
                kind, summary = "rule-tests", {"tests": len(doc["tests"])}
            else:
                groups = li.parse_rules(raw)
                kind, summary = "rules", {"groups": len(groups), "rules": sum(len(g["rules"]) for g in groups)}
            for k, c in d.items():
                dropped[k] = dropped.get(k, 0) + c
            keep(rel, text, kind, summary, raw)
    if not files:
        raise LabError(f"{src.name or src}: no recognised file (looked for {', '.join(SINGLE)}, k8s/, rules/, teleport/); see docs/guides/config-lab.md")
    left = li.residual_secrets(stored)
    if left:
        raise LabError("refused: a secret may remain in the export at " + ", ".join(left[:5]) + (f" and {len(left) - 5} more" if len(left) > 5 else "")
                       + ". Remove it from the exported file and import again; nothing was stored.")
    ignored = sorted(p.name for p in src.iterdir() if p.name not in seen_names and p.name not in FOLDERS and p.name != "versions.yaml")
    prod = _versions(src)
    manifest = {"name": name, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": src.name,
                "versions": prod, "files": files, "scrubbed": scrubbed, "dropped_objects": dropped, "ignored_files": ignored,
                "not_tested": list(NOT_TESTED), "contract": 1}
    if sim is not None:
        ours = workbench_versions(sim)
        manifest["workbench_versions"], manifest["version_match"] = ours, version_match(prod, ours)
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    for rel, text in stored.items():
        f = tmp / "baseline" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return manifest


def summary(m: dict) -> str:
    lines = [f"imported lab {m['name']}: {len(m['files'])} file(s) into compose/generated/lab/{m['name']}/baseline"]
    for f in m["files"]:
        extra = ", ".join(f"{k} {v}" for k, v in f.items() if k not in ("file", "kind", "sha256"))
        lines.append(f"  {f['file']}: {f['kind']}" + (f" ({extra})" if extra else ""))
    if m["scrubbed"]:
        lines.append("scrubbed: " + ", ".join(f"{s['file']} ({s['values']} value(s))" for s in m["scrubbed"]))
    if m["dropped_objects"]:
        lines.append("dropped, not allow-listed: " + ", ".join(f"{k} x{v}" for k, v in sorted(m["dropped_objects"].items())))
    if m["ignored_files"]:
        lines.append("ignored: " + ", ".join(m["ignored_files"]))
    v = m["versions"]
    lines.append("versions: " + ", ".join(f"{k} {v[k] or 'not given'}" for k in VERSION_KEYS))
    diff = [k for k, s in m.get("version_match", {}).items() if s == "differs"]
    if diff:
        lines.append(f"version differs from this workbench ({', '.join(diff)}): results for it are indicative")
    lines.append("not tested: " + ", ".join(m["not_tested"]))
    return "\n".join(lines)
