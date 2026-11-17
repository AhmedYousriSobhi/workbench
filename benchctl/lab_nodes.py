"""Config lab nodes (issue 863, ADR 0050): the lab node containers and the `slurm.conf` nodes and partitions the replay needs.

`plan` takes the downscale of issue 551 (production names kept, every partition x shape x leaf group alive) and returns, for the lab
marker: the compose services to start (`lab-node-NNN`, hostname = the production node name), the NodeName and PartitionName lines
that go in the lab block of `slurm.conf`, and the matching `gres.conf` lines. Nothing here touches Docker; `lab_apply` applies the plan.
A simulated partition with the same name as a lab partition is switched off while the lab is active, and the lab's default
partition takes over; `strip` puts both back.
"""
from __future__ import annotations

import re
from typing import Any

from . import lab_slurm
from .lab_import import expand_hostlist
from .lab_slurm import LabError

SERVICE = "lab-node-{:03d}"
DROPPED_PARTITION_KEYS = ("AllowGroups", "DenyGroups")  # production groups do not exist here, so the lab would refuse every job
OFF = "# lab-off "  # prefix of a simulated partition line the lab switched off
DEFAULT_WAS = " Default=NO #lab-default"  # a simulated partition that lost the default to a lab one


def _fmt(pairs: dict[str, str]) -> str:
    return " ".join(f"{k}={v}" if not re.search(r"\s", v) else f'{k}="{v}"' for k, v in pairs.items())


def _node_line(prod: dict[str, str], host: str, service: str) -> str:
    drop = {"NodeName", "NodeAddr", "NodeHostname", "State", "Reason"}
    return _fmt({"NodeName": host, "NodeHostname": host, "NodeAddr": service, **{k: v for k, v in prod.items() if k not in drop}})


def plan(baseline: dict, slurm_text_parsed: dict | None, nodes_scale: dict, sim_nodes: set[str] = frozenset()) -> dict[str, Any]:
    """{nodes: [{service, host, gpus}], slurm: [lines], gres: [lines], dropped: [...]}.

    `baseline` is `lab.load_baseline`; `slurm_text_parsed` the side's parsed slurm.conf (partitions may differ from the baseline's in a candidate);
    `nodes_scale` the `lab_slurm.downscale` result.
    """
    b = baseline["slurm"]
    if b is None:
        raise LabError("the lab has no slurm.conf: the node shapes come from it")
    clash = sorted(set(nodes_scale["nodes"]) & set(sim_nodes))
    if clash:
        raise LabError(f"refused: lab node name(s) {', '.join(clash[:5])} are also simulated nodes; rename them in the export (or in sim.yaml), then apply again")
    line_of: dict[str, dict[str, str]] = {}
    for line in b["nodes"]:
        for n in expand_hostlist(line["NodeName"]):
            line_of[n] = line
    kept = list(nodes_scale["nodes"])
    services = {n: SERVICE.format(i) for i, n in enumerate(kept, 1)}
    prod = {p["name"]: p for p in lab_slurm.production_nodes(b, baseline["topology"], baseline["gres"])}
    nodes, slurm, gres = [], [], []
    for n in kept:
        slurm.append(_node_line(line_of[n], n, services[n]))
        g = (line_of[n].get("Gres") or "")
        gpus = sum(int(x.rsplit(":", 1)[-1]) for x in g.split(",") if x.startswith("gpu") and x.rsplit(":", 1)[-1].isdigit())
        nodes.append({"service": services[n], "host": n, "gpus": gpus})
        for item in [x for x in g.split(",") if x]:  # `gpu:h100:8` or `gpu:8`: a stand-in has no device file, so Slurm counts it without one
            bits = item.split(":")
            if len(bits) >= 2 and bits[-1].isdigit():
                gres.append(_fmt({"NodeName": n, "Name": bits[0], **({"Type": bits[1]} if len(bits) == 3 else {}), "Count": bits[-1]}))
    dropped: list[str] = []
    parts = (slurm_text_parsed or b)["partitions"] or b["partitions"]
    for p in parts:
        members = [n for n in kept if p["PartitionName"] in prod[n]["partitions"]]
        if not members:
            dropped.append(f"partition {p['PartitionName']} (no node of it is in the lab)")
            continue
        keep = {k: v for k, v in p.items() if k not in ("Nodes", *DROPPED_PARTITION_KEYS)}
        slurm.append(_fmt({"PartitionName": keep.pop("PartitionName"), "Nodes": ",".join(members), **keep}))
        for k in DROPPED_PARTITION_KEYS:
            if k in p:
                dropped.append(f"{k} of partition {p['PartitionName']}")
    return {"nodes": nodes, "slurm": slurm, "gres": gres, "dropped": dropped}


def _part_name(line: str) -> str | None:
    m = re.match(r"\s*PartitionName=(\S+)", line)
    return m.group(1) if m else None


def strip(text: str) -> str:
    """Undo `switch_off`: simulated partition lines come back."""
    out = []
    for line in text.splitlines():
        line = line.removeprefix(OFF)
        out.append(line.replace(DEFAULT_WAS, " Default=YES"))
    return "\n".join(out) + "\n"


def switch_off(text: str, lab_lines: list[str]) -> str:
    """Simulated partitions the lab replaces are commented out; the others lose `Default=YES` when a lab partition has it."""
    lab_parts = {n for ln in lab_lines if (n := _part_name(ln))}
    lab_default = any(_part_name(ln) and re.search(r"\bDefault=YES\b", ln) for ln in lab_lines)
    out = []
    for line in strip(text).splitlines():
        n = _part_name(line)
        if n and n in lab_parts:
            line = OFF + line
        elif n and lab_default and re.search(r"\bDefault=YES\b", line):
            line = re.sub(r"\s*\bDefault=YES\b", DEFAULT_WAS, line)
        out.append(line)
    return "\n".join(out) + "\n"
