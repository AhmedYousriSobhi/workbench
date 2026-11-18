"""Every scenario step, expectation and part reads as words on the page (issue 813, ADR 0086).

`describe()` in webui/static/js/testrunmodel.js gives the Test run tab, the Run rail, the phone sheet and the badge labels one sentence
per step. Raw JSON is its last resort. Two checks keep it that way:

- every step of every shipped scenario, after `plan()` expands fault models and ramps, reads as words (RAW_OK lists the exceptions;
  it may only shrink);
- every step key the engine accepts (derived from its own constants, so a new key fails here until it has words) has a sample that
  reads as words.

The same for expectations (`expectWords()`, EXPECT_RAW_OK only shrinks) and for the part ids the engine names (`partWords()` in
webui/static/js/runlayermodel.js): the Run tab says "the login node", never "login" (ADR 0086 phase 3).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from benchctl import authfaults, faultmodel, k8s, netemfaults, scenario
from webui import blast

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "webui/static/js/testrunmodel.js"
RUNMODEL = ROOT / "webui/static/js/runlayermodel.js"
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
WITH_TOPOLOGY = {**SIM, "topology": {"plugin": "tree", "spines": 1, "leaves_per_spine": 2}}

# (scenario, YAML step number) -> why that step still shows JSON. Only shrinks: give the step words, then remove its entry.
RAW_OK: dict[tuple[str, int], str] = {}
# (scenario, expectation number E) -> why that expectation still shows JSON. Only shrinks.
EXPECT_RAW_OK: dict[tuple[str, int], str] = {}

# Words that mean a sentence was built from a missing value, or not built at all.
BAD = re.compile(r"^\{|\bundefined\b|\bnull\b|\bNaN\b|\[object|  | (on|to|of|for|in)$|^\s|\s$")

N = ["cn001"]
SAMPLES = [  # (target, do): one per key the engine or a mock's /sim/inject accepts
    ("slurm", {"drain": N, "reason": "maintenance"}), ("slurm", {"down": N}), ("slurm", {"resume": N}),
    ("slurm", {"power_off": N}), ("slurm", {"power_on": N}), ("slurm", {"power_down": N}), ("slurm", {"power_up": N}),
    ("slurm", {"reboot": N, "reason": "r"}), ("slurm", {"slurmd_stop": N}), ("slurm", {"slurmd_start": N}),
    ("slurm", {"slurmd_restart": N}), ("slurm", {"slurmctld_restart": True}), ("slurm", {"submit": {"node": "gpu001", "gpus": 2, "user": "bob"}}),
    ("slurm", {"cancel": True}),
    ("slurm-upgrade", {"to": "26.05.5", "daemons": ["slurmctld", "login", "cn001"]}),
    ("switch", {"leaf": "leaf1", "state": "down"}), ("switch", {"leaf": "leaf1", "state": "up"}),
    ("ssh", {"state": "down"}), ("ssh", {"state": "up"}), ("ssh", {"state": "hung"}), ("ssh", {"rotate_host_key": True}),
    ("ssh", {"max_sessions": 2}), ("ssh", {"reset": True}), ("ssh", {"reject_auth": True}),
    ("ssh", {"node_sshd": "down", "nodes": N}), ("ssh", {"node_sshd": "up", "nodes": N}),
    ("auth", {"slurm_token_ttl": {"user": "alice", "ttl": "5m"}}), ("auth", {"teleport_cert_ttl": {"user": "alice", "ttl": "5m"}}),
    ("auth", {"teleport_cert_restore": ["alice"]}), ("auth", {"munge_wrong_key": N}), ("auth", {"munge_restore": N}),
    ("auth", {"bmc_tls_expired": True}), ("auth", {"bmc_tls_expired": False}), ("auth", {"k8s_cert_expired": True}),
    ("auth", {"k8s_cert_expired": False}),
    ("netem", {"nodes": N, "delay": "200ms", "loss": "5%", "ttl": "60s"}), ("netem", {"nodes": N, "delay": 50}), ("netem", {"clear": True}),
    ("k8s", {"down": ["kgpu001"]}), ("k8s", {"up": ["kgpu001"]}), ("k8s", {"cordon": ["kgpu001"]}), ("k8s", {"uncordon": ["kgpu001"]}),
    ("k8s", {"pod": {"namespace": "team-b", "name": "p", "fault": "imagepull"}}), ("k8s", {"pod": {"namespace": "team-b", "name": "p", "fault": "jobfail"}}),
    ("k8s", {"delete_pod": {"namespace": "team-b", "name": "p"}}), ("k8s", {"scale": {"namespace": "team-b", "deploy": "web", "replicas": 3}}),
    ("k8s", {"apply": ["scenario/70-apf.yaml"]}), ("k8s", {"remove": ["scenario/70-apf.yaml"]}),
    ("k8s", {"drain": {"node": "kgpu001", "timeout": 30}}), ("k8s", {"queue": {"name": "gpu-training", "active": False, "pending": 3}}), ("k8s", {"gpu": {"node": "kgpu001", "count": 8, "advertised": False}}), ("k8s", {"gpu": {"node": "kgpu001", "count": 8, "mig": {"strategy": "mixed", "profile": "1g.18gb", "devices": 7}}}), ("k8s", {"gpu": {"node": "kgpu001", "count": 8, "mig": {"strategy": "none"}}}), ("k8s", {"kubelet": {"node": "kgpu001", "skew": "newer"}}), ("k8s", {"kubelet": {"node": "kgpu001", "skew": "same"}}), ("k8s", {"volcano": {"queue": "gpu-batch", "state": "Closed"}}), ("k8s", {"volcano": {"podgroup": "train-run", "namespace": "team-a", "phase": "Pending"}}), ("k8s", {"usage": {"namespace": "team-a", "deploy": "hpa-below", "cpu": "400m"}}), ("k8s", {"metrics_server": "stop"}), ("k8s", {"metrics_server": "start"}),
    ("dcgm", {"nodes": ["gpu001"], "stop": True}), ("dcgm", {"nodes": ["gpu001"], "start": True}), ("dcgm", {"nodes": ["gpu001"], "restart": True}),
    ("jira", {"tickets": {"action": "create", "summary": "s"}}), ("jira", {"tickets": {"action": "transition", "jql": "x", "to": "Done"}}),
    ("jira", {"tickets": {"action": "comment", "jql": "x", "body": "b"}}), ("jira", {"tickets": {"action": "fault", "node": "cn001", "kind": "fan"}}),
    ("jira", {"tickets": {"action": "fault", "node": "cn001", "kind": "fan", "open": False}}), ("jira", {"tickets": {"action": "reset"}}),
    ("jira", {"failure": {"mode": "401", "count": 2}}), ("jira", {"failure": {"mode": "slow", "delay": 3}}), ("jira", {"failure": {"mode": None}}),
    ("jira", {"activity": {"on": True, "every": 5}}), ("jira", {"activity": {"on": False}}),
    ("bmc-farm", {"nodes": N, "power": "Off"}), ("bmc-farm", {"nodes": N, "temp": 90}), ("bmc-farm", {"nodes": N, "fan_fail": 2}),
    ("bmc-farm", {"nodes": N, "clear": True}), ("bmc-farm", {"nodes": N, "event": "e", "severity": "Critical"}),
    ("bcm-mock", {"nodes": N, "state": "DOWN"}), ("bcm-mock", {"nodes": N, "closed": True}),
    ("gpu-exporter", {"nodes": N, "xid": 79}), ("gpu-exporter", {"nodes": N, "ib_down": 2}), ("gpu-exporter", {"nodes": N, "ib_down": 0}),
    ("gpu-exporter", {"nodes": N, "nic_flap": True}), ("gpu-exporter", {"nodes": N, "down": True}), ("gpu-exporter", {"nodes": N, "down": False}),
    ("gpu-exporter", {"nodes": N, "temp": 80}), ("gpu-exporter", {"nodes": N, "clear": True}),
    ("gpu-exporter", {"nodes": N, "storage": {"mount": "/scratch", "inodes_used_pct": 95}}),
    ("gpu-exporter", {"nodes": N, "storage": {"mount": "/scratch", "stale": False}}),
    *[("jira-real", {"fault": m}) for m in scenario.JIRA_REAL_MODES],
    *[("jira-real", {"tickets": {"action": a, "summary": "s", "jql": "x", "to": "Done", "body": "b"}}) for a in scenario.JIRA_REAL_TICKET_ACTIONS],
    *[("fault", {"inject": f, "nodes": ["gpu001"]}) for f in faultmodel.FAULTS],
]


def _js(module: Path, fn: str, items: list) -> list[str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    js = (f"import {{ {fn} }} from {json.dumps(module.as_uri())}; let s = ''; process.stdin.on('data', c => s += c)"
          f".on('end', () => process.stdout.write(JSON.stringify(JSON.parse(s).map(x => {fn}(x)))));")
    r = subprocess.run([node, "--input-type=module", "-e", js], input=json.dumps(items), capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr[-600:]
    return json.loads(r.stdout)


def words(steps: list[dict]) -> list[str]:
    return _js(MODEL, "describe", steps)


def test_every_shipped_step_reads_as_words():
    steps, where = [], []
    for sc in scenario.list_all(ROOT / "scenarios", sim=WITH_TOPOLOGY):
        for st in scenario.plan(sc):
            steps.append({"target": st.target, "do": st.do})
            where.append((sc["name"], st.n))
    assert len(steps) > 100
    raw = {}
    for key, step, text in zip(where, steps, words(steps), strict=True):
        if BAD.search(text):
            raw.setdefault(key, f"{step['target']} {json.dumps(step['do'])} -> {text!r}")
    new = {k: v for k, v in raw.items() if k not in RAW_OK}
    gone = sorted(k for k in RAW_OK if k not in raw)
    assert not new, f"steps that show JSON or broken words (give them a sentence in describe(), testrunmodel.js): {new}"
    assert not gone, f"these steps read as words now: remove them from RAW_OK: {gone}"


def test_every_step_key_the_engine_accepts_has_words():
    have = {(t, k) for t, do in SAMPLES for k in do} | {(t, do["fault"]) for t, do in SAMPLES if t == "jira-real" and "fault" in do}
    have |= {(t, do["tickets"]["action"]) for t, do in SAMPLES if "tickets" in do}
    need = ({("slurm", k) for k in (*scenario.SLURM_STATES, *scenario.DAEMON_KEYS, "power_down", "power_up", "reboot")}
            | {("ssh", k) for k in scenario.SSH_KEYS} | {("k8s", k) for k in k8s.STEP_KEYS} | {("auth", k) for k in authfaults.KEYS}
            | {("netem", k) for k in netemfaults.KEYS} | {("jira-real", m) for m in scenario.JIRA_REAL_MODES}
            | {("jira", a) for a in scenario.JIRA_ACTIONS} | {("jira-real", a) for a in scenario.JIRA_REAL_TICKET_ACTIONS}
            | {("dcgm", v) for v in ("stop", "start", "restart")})
    assert not need - have, f"step keys with no sample here (add one to SAMPLES and words to describe()): {sorted(need - have)}"
    steps = [{"target": t, "do": do} for t, do in SAMPLES]
    planned = [{"target": s.target, "do": s.do} for t, do in SAMPLES if t == "fault" for s in scenario.plan({"steps": [{"at": 0, "target": t, "do": do}]})]
    bad = [(s, w) for s, w in zip(steps + planned, words(steps + planned), strict=True) if BAD.search(w)]
    assert not bad, f"no words for: {bad}"
    assert len(set(words(steps))) > len(steps) * 0.9, "different steps should read differently"


def test_every_shipped_expectation_reads_as_words():
    labels, where = [], []
    for sc in scenario.list_all(ROOT / "scenarios", sim=WITH_TOPOLOGY):
        for x in scenario.preview(sc, WITH_TOPOLOGY)["expects"]:
            labels.append(x["label"])
            where.append((sc["name"], x["e"]))
    assert len(labels) > 30
    raw = {k: f"{label} -> {text!r}" for k, label, text in zip(where, labels, _js(MODEL, "expectWords", labels), strict=True)
           if BAD.search(text) or '{"' in text}
    new = {k: v for k, v in raw.items() if k not in EXPECT_RAW_OK}
    gone = sorted(k for k in EXPECT_RAW_OK if k not in raw)
    assert not new, f"expectations that show JSON (give the kind a sentence in expectWords(), testrunmodel.js): {new}"
    assert not gone, f"these expectations read as words now: remove them from EXPECT_RAW_OK: {gone}"


def test_every_expectation_kind_the_engine_knows_has_words():
    kinds = set(re.findall(r'kind == "([a-z0-9_.]+)"', (ROOT / "benchctl/scenario.py").read_text()))
    assert len(kinds) >= 10
    got = _js(MODEL, "expectWords", [f'{k} {{"node": "cn001", "state": "drain", "labels": ["a"], "jql": "x", "status": "Done", "count": 2, '
                                     f'"namespace": "ns", "name": "p", "reason": "Running", "selector": "app=x", "mount": "/scratch", "min_used_pct": 90}}' for k in sorted(kinds)])
    bad = [(k, w) for k, w in zip(sorted(kinds), got, strict=True) if BAD.search(w) or "{" in w]
    assert not bad, f"expectation kinds with no words in expectWords(): {bad}"


def test_every_part_the_engine_names_reads_as_words():
    ids = {*scenario.TARGET_PART.values(), *scenario.AUTH_PART.values(), *scenario.UPGRADE_PART.values(),
           *(p for t in blast.TARGETS.values() for p in t["parts"]), *(p for ps in blast.AUTH_PARTS.values() for p in ps)}
    ids |= {p for kind in ("jira.issue_count", "k8s.pod_status", "storage.mount") for p in scenario.expect_touches({kind: {"target": "real", "node": "cn001"}})} - {"cn001"}
    ids.add("jira-mock")
    ids = sorted(ids)
    raw = [i for i, w in zip(ids, _js(RUNMODEL, "partWords", ids), strict=True) if w == i]
    assert not raw, f"part ids the Run tab would show raw (add them to PART_WORDS in runlayermodel.js): {raw}"
