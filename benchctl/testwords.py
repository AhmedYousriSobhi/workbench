"""One sentence per scenario step and per expectation, in Python: the same words as the page (issue 739).

`describe()` and `expectWords()` in webui/static/js/testrunmodel.js give the Test run tab its sentences; `benchctl export test-report`
needs the same sentences without a browser, so this is a line-by-line port. tests/test_testreport.py runs both over every shipped
scenario step and expectation (through Node, when it is installed) and fails when they differ: change both files together.

The port keeps JavaScript's rules where they show in the words: a value is "present" by JavaScript truthiness (an empty list is, 0 and
"" are not), numbers print without a trailing ".0", and a missing value reads "undefined".
"""
from __future__ import annotations

import json
import re


def js(v) -> str:
    """`String(v)` as JavaScript prints it, for the values a scenario file holds."""
    if v is None:
        return "undefined"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, list):
        return ",".join("" if x is None else js(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return str(v)


def has(v) -> bool:
    """JavaScript truthiness: [] and {} are truthy, 0, "" and null are not."""
    if isinstance(v, (list, dict)):
        return True
    return bool(v)


def num(v) -> float:
    """Unary plus, `+v`: NaN for what is not a number."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def lst(a) -> str:
    return ", ".join("" if x is None else js(x) for x in (a if isinstance(a, list) else [] if a is None else [a]))


def join(a) -> str:
    """`a.join(', ')` for a list the scenario file must hold."""
    return ", ".join("" if x is None else js(x) for x in a)


def count(k, one: str, many: str | None = None) -> str:
    return f"{js(k)} {one if num(k) == 1 else many if many is not None else one + 's'}"


def next_calls(k) -> str:
    return "the next call" if num(k) == 1 else f"the next {js(k)} calls"


def pct(v) -> str:
    return js(v) if re.search(r"%$", js(v)) else f"{js(v)}%"


def dur(v, unit: str = "s") -> str:
    """'60s' -> '60 s', '5m' -> '5 min', '200ms' -> '200 ms'; a bare number takes `unit`."""
    if v is None or v == "":
        return ""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ms|s|m|min|h)?", js(v).strip())
    if not m:
        return js(v)
    u = m.group(2) or unit
    return f"{m.group(1)} {'min' if u == 'm' else u}"


def user(d) -> str:
    return js(d.get("user")) if has(d.get("user")) else "alice"


def given(d, k) -> bool:
    """`d.k != null`."""
    return d.get(k) is not None


def orv(v, default):
    """`v || default` (JavaScript)."""
    return v if has(v) else default


def nn(v, default):
    """`v ?? default`."""
    return default if v is None else v



LOGIN = "the login node"


def ssh(d) -> list[str]:
    out = []
    if has(d.get("reset")):
        out.append(f"Restore {LOGIN}'s SSH host key, keys and session limit")
    if has(d.get("state")):
        known = {"down": f"Stop {LOGIN} (SSH to it fails)", "up": f"Bring {LOGIN} back", "hung": f"Freeze {LOGIN} (SSH hangs)"}
        out.append(known.get(d["state"]) or f"Set {LOGIN} to {js(d['state'])}")
    if has(d.get("rotate_host_key")):
        out.append(f"Change {LOGIN}'s SSH host key")
    if has(d.get("reject_auth")):
        out.append(f"Make {LOGIN} refuse the client's SSH key")
    if has(d.get("node_sshd")):
        out.append(f"Stop sshd on {lst(d.get('nodes'))} (the node stays up)" if d["node_sshd"] == "down" else f"Start sshd again on {lst(d.get('nodes'))}")
    if given(d, "max_sessions"):
        out.append(f"Allow {count(d['max_sessions'], 'SSH session')} per connection on {LOGIN}")
    return out


def auth(d) -> list[str]:
    out, tk, tc = [], d.get("slurm_token_ttl"), d.get("teleport_cert_ttl")
    if has(tk):
        out.append(f"Give {user(tk)} a Slurm REST token that expires after {dur(tk.get('ttl'))}")
    if has(tc):
        out.append(f"Make {user(tc)}'s Teleport identity expire after {dur(tc.get('ttl'))}")
    if has(d.get("teleport_cert_restore")):
        out.append(f"Renew the Teleport identity of {lst(d['teleport_cert_restore'])}")
    if has(d.get("munge_wrong_key")):
        out.append(f"Give {lst(d['munge_wrong_key'])} a wrong munge key")
    if has(d.get("munge_restore")):
        out.append(f"Put the right munge key back on {lst(d['munge_restore'])}")
    if "bmc_tls_expired" in d:
        out.append("Expire the BMC farm's TLS certificate" if has(d["bmc_tls_expired"]) else "Give the BMC farm its valid TLS certificate again")
    if "k8s_cert_expired" in d:
        out.append("Expire the Kubernetes API server's certificate" if has(d["k8s_cert_expired"]) else "Give the Kubernetes API server its valid certificate again")
    if has(d.get("kube_binding_missing")):
        out.append(f"Delete the Kubernetes binding of the {lst(d['kube_binding_missing'])} tier (Teleport lets the user in, Kubernetes says forbidden)")
    if "kube_impersonation_revoked" in d:
        out.append("Stop Teleport impersonating users in Kubernetes (every tsh kube request fails)" if has(d["kube_impersonation_revoked"])
                   else "Let Teleport impersonate users in Kubernetes again")
    if has(d.get("kube_binding_restore")):
        out.append("Put back the Kubernetes bindings of the Teleport access tiers")
    return out


POD_FAULT = {"crashloop": "that crash-loops", "oom": "that runs out of memory", "imagepull": "whose image cannot be pulled", "jobfail": "whose job fails"}


def _pod(p) -> str:
    extra = [x for x in (count(p["gpus"], "GPU") if has(p.get("gpus")) else "", f"priority {js(p['priority_class'])}" if has(p.get("priority_class")) else "") if x]
    on = f" on {js(p['node'])}" if has(p.get("node")) else ""
    fault = ""
    if has(p.get("fault")):
        fault = " " + (POD_FAULT.get(p["fault"]) or f"with fault {js(p['fault'])}")
    more = f" ({', '.join(extra)})" if extra else ""
    return f"Start pod {js(p.get('namespace'))}/{js(p.get('name'))}{on}{fault}{more}"


def _queue(q) -> str:
    pend = f" ({count(q['pending'], 'workload')} pending)" if given(q, "pending") else ""
    if q.get("active") is False:
        return f"Stop queue {js(q.get('name'))} admitting work{pend}"
    if q.get("active") is True:
        return f"Let queue {js(q.get('name'))} admit work again{pend}"
    return f"Set queue {js(q.get('name'))} to {count(q.get('pending'), 'pending workload')}"


def k8s(d) -> list[str]:
    out = []

    def kn(k):
        return f"Kubernetes node{'' if len(d[k]) == 1 else 's'} {lst(d[k])}"

    if has(d.get("drain")) and not isinstance(d["drain"], list):
        dr = d["drain"]
        out.append(f"Drain Kubernetes node {js(dr.get('node'))} (waits {js(orv(dr.get('timeout'), 30))} s for evictions)")
    if isinstance(d.get("down"), list):
        out.append(f"Take {kn('down')} down (NotReady)")
    if isinstance(d.get("up"), list):
        out.append(f"Bring {kn('up')} back (Ready)")
    if isinstance(d.get("cordon"), list):
        out.append(f"Cordon {kn('cordon')}")
    if isinstance(d.get("uncordon"), list):
        out.append(f"Uncordon {kn('uncordon')}")
    if has(d.get("pod")):
        out.append(_pod(d["pod"]))
    if has(d.get("delete_pod")):
        out.append(f"Delete pod {js(d['delete_pod'].get('namespace'))}/{js(d['delete_pod'].get('name'))}")
    if has(d.get("scale")):
        sc = d["scale"]
        out.append(f"Scale deployment {js(sc.get('namespace'))}/{js(sc.get('deploy'))} to {count(sc.get('replicas'), 'replica')}")
    if has(d.get("apply")):
        out.append(f"Apply k8s/{lst(d['apply'])} to the Kubernetes API")
    if has(d.get("remove")):
        out.append(f"Remove k8s/{lst(d['remove'])} from the Kubernetes API")
    if has(d.get("queue")):
        out.append(_queue(d["queue"]))
    if d.get("metrics_server") == "stop":
        out.append("Stop metrics-server (the metrics API answers 503)")
    elif d.get("metrics_server") == "start":
        out.append("Start metrics-server again (the metrics API answers)")
    if isinstance(d.get("gpu"), dict):
        g = d["gpu"]
        if "advertised" in g:
            out.append(f"Kubernetes node {g['node']} {'advertises its GPUs again' if g['advertised'] else 'stops advertising its GPUs (allocatable 0)'}")
        else:
            m = g["mig"]
            out.append(f"Kubernetes node {g['node']} " + ("is a normal GPU node again" if m["strategy"] == "none" else f"becomes a MIG node ({m['strategy']}, {m.get('profile', '1g.18gb')})"))
    if isinstance(d.get("kubelet"), dict):
        out.append(f"Kubernetes node {d['kubelet']['node']} reports a kubelet {d['kubelet']['skew']} than the API server" if d["kubelet"]["skew"] != "same" else f"Kubernetes node {d['kubelet']['node']} reports the API server's version")
    if isinstance(d.get("volcano"), dict):
        v = d["volcano"]
        out.append(f"Set Volcano queue {v['queue']} {v['state'].lower()}" if "queue" in v else f"Set Volcano PodGroup {v['namespace']}/{v['podgroup']} to {v['phase']}")
    if isinstance(d.get("usage"), dict):
        out.append(f"Set the CPU use of each {d['usage']['namespace']}/{d['usage']['deploy']} pod to {d['usage']['cpu']} (its autoscaler reacts)")
    return out


def netem(d) -> list[str]:
    if has(d.get("clear")):
        return ["Remove the network delay and loss from every node"]
    what = [x for x in (f"{dur(d['delay'], 'ms')} delay" if given(d, "delay") else "", f"{pct(d['loss'])} packet loss" if given(d, "loss") else "") if x]
    ttl = f" for {dur(d['ttl'])}" if has(d.get("ttl")) else ""
    return [f"Add {' and '.join(what) or 'a network fault'} on {lst(d.get('nodes'))}{ttl}"]


def _calls(d) -> str:
    return next_calls(nn(d.get("count"), 1))


def _retry(d) -> str:
    return js(nn(d.get("retry_after"), 30))


REAL = {
    "outage": lambda d: "Freeze the real Jira (its requests time out)",
    "restore": lambda d: "Unfreeze the real Jira",
    "bad_logins": lambda d: f"Send {count(nn(d.get('count'), 5), 'wrong-password login')} for {user(d)} to the real Jira (it locks with a CAPTCHA)",
    "clear_logins": lambda d: "Reset the failed-login count on the real Jira",
    "rate_limit": lambda d: f"Turn on the real Jira's rate limit ({count(nn(d.get('capacity'), 3), 'request')}, {js(nn(d.get('fill_rate'), 1))} more per second)",
    "rate_limit_off": lambda d: "Turn off the real Jira's rate limit",
    "token_revoke": lambda d: f"Revoke {user(d)}'s API token on the real Jira",
    "token_restore": lambda d: f"Give {user(d)} a new API token on the real Jira",
    "proxy_503": lambda d: f"Make the real Jira's proxy answer 503 for {_calls(d)} (Retry-After {_retry(d)} s)",
    "proxy_429": lambda d: f"Make the real Jira's proxy answer 429 for {_calls(d)} (Retry-After {_retry(d)} s)",
    "proxy_slow": lambda d: f"Make the real Jira's proxy answer {dur(nn(d.get('delay'), 2))} late for {_calls(d)}",
    "proxy_off": lambda d: "Take the faults off the real Jira's proxy",
}


def jira_real(d) -> list[str]:
    if has(d.get("tickets")):
        return [tickets(d["tickets"], "real Jira")]
    return [REAL[d["fault"]](d)] if d.get("fault") in REAL else []


def tickets(t, jira: str = "Jira") -> str:
    action = t.get("action")
    if action == "fault":
        kind = js(orv(t.get("kind"), "fault"))
        if t.get("open") is False:
            return f"Resolve {js(t.get('node'))}'s {kind} {jira} ticket"
        return f"File a {kind} {jira} ticket for {js(t.get('node'))}"
    if action == "reset":
        return f"Delete every {jira} ticket"
    if action == "transition":
        return f"Move the matching {jira} tickets to {js(t.get('to'))}"
    if action == "comment":
        return f"Comment on the matching {jira} tickets"
    verb = {"create": "Create"}.get(action) or js(action)
    c = t.get("count")
    n = js(c) + " " if has(c) else ""
    plural = "" if c is None or (not isinstance(c, bool) and c == 1) else "s"
    summary = f": {js(t['summary'])}" if has(t.get("summary")) else ""
    return f"{verb} {n}{jira} ticket{plural}{summary}"


def storage(st, n: str) -> list[str]:
    m, out = js(orv(st.get("mount"), "the mount")), []
    if has(st.get("reset")):
        return [f"Put {m} back to normal (seen from {n})"]
    if given(st, "used_pct"):
        inodes = f", {pct(st['inodes_used_pct'])} of its inodes" if given(st, "inodes_used_pct") else ""
        out.append(f"Fill {m} to {pct(st['used_pct'])} used{inodes} (seen from {n})")
    elif given(st, "inodes_used_pct"):
        out.append(f"Use {pct(st['inodes_used_pct'])} of {m}'s inodes (seen from {n})")
    if given(st, "stale"):
        out.append(f"Make {m} stale on {n}" if has(st["stale"]) else f"Make {m} answer again on {n}")
    if has(st.get("user")):
        out.append(f"Put {js(st['user'])} at {js(st.get('used_gb'))} GB on {m} (seen from {n})")
    return out


DAEMON = {"slurmd_restart": "Restart", "slurmd_stop": "Stop", "slurmd_start": "Start"}


def _submit(s) -> str:
    gpus = f" holding {js(s['gpus'])} GPUs" if has(s.get("gpus")) else ""
    who = f" as {js(s['user'])}" if has(s.get("user")) else ""
    return f"Submit a {js(orv(s.get('duration'), 600))} s job on {js(s.get('node'))}{gpus}{who}"


def _failure(f) -> str:
    if not has(f.get("mode")):
        return "Jira back to normal"
    nxt = f"for {next_calls(orv(f.get('count'), 1))}"
    if f["mode"] == "slow":
        return f"Make Jira answer {dur(nn(f.get('delay'), 2))} late {nxt}"
    return f"Make Jira answer {js(f['mode'])} {nxt}"


def keyed(d, n: str, t=None) -> list[str]:
    """The keyed steps of the mocks and Slurm: one sentence, from the first key that says what the step does."""
    if has(d.get("drain")) and not isinstance(d["drain"], list):
        return k8s(d)
    reason = js(orv(d.get("reason"), "scenario"))
    if has(d.get("drain")):
        return [f"Drain {join(d['drain'])} in Slurm ({reason})"]
    if isinstance(d.get("down"), list):
        return [f"Mark {join(d['down'])} down in Slurm ({reason})"]
    if has(d.get("resume")):
        return [f"Resume {join(d['resume'])} in Slurm"]
    if has(d.get("power_off")):
        return [f"Freeze {lst(d['power_off'])} (it stops answering, as after a power cut)"]
    if has(d.get("power_on")):
        return [f"Unfreeze {lst(d['power_on'])}"]
    if has(d.get("reboot")):
        return [f"Reboot {lst(d['reboot'])} through Slurm (scontrol reboot ASAP)"]
    if has(d.get("power_down")):
        return [f"Power {lst(d['power_down'])} down through Slurm's power saving"]
    if has(d.get("power_up")):
        return [f"Power {lst(d['power_up'])} up through Slurm's power saving"]
    if has(d.get("submit")):
        return [_submit(d["submit"])]
    if has(d.get("cancel")):
        return ["Cancel the simulated users' jobs"]
    for k, verb in DAEMON.items():
        if has(d.get(k)):
            return [f"{verb} slurmd on {join(d[k])}"]
    if has(d.get("slurmctld_restart")):
        return ["Restart slurmctld"]
    if has(d.get("tickets")):
        return [tickets(d["tickets"])]
    if has(d.get("activity")):
        a = d["activity"]
        return ["Stop the background Jira activity" if a.get("on") is False else f"Start background Jira activity (a change every {js(orv(a.get('every'), 10))} s)"]
    if has(d.get("failure")):
        return [_failure(d["failure"])]
    if has(d.get("storage")):
        return storage(d["storage"], n)
    if has(d.get("clear")):
        return [f"Clear injected faults on {n}"]
    if has(d.get("power")):
        return [f"Power {js(d['power'])} {n}"]
    if has(d.get("closed")):
        return [f"Close {n} in BCM"]
    if has(d.get("state")):
        return [f"Set {n} to {js(d['state'])} in BCM"]
    if has(d.get("xid")):
        return [f"GPU error XID {js(d['xid'])} on {n}"]
    if has(d.get("event")):
        return [f"BMC event on {n} ({js(orv(d.get('severity'), 'Warning'))}): {js(d['event'])}"]
    if given(d, "temp"):
        return [f"Temperature of {n} to {js(d['temp'])} °C"]
    if has(d.get("fan_fail")):
        return [f"{js(d['fan_fail'])} fan(s) fail on {n}"]
    if given(d, "ib_down"):
        return [f"Take {count(d['ib_down'], 'InfiniBand port')} down on {n}" if num(d["ib_down"]) else f"Bring the InfiniBand ports back up on {n}"]
    if given(d, "nic_flap"):
        return [f"Make the Ethernet link flap on {n}" if has(d["nic_flap"]) else f"Stop the Ethernet link flapping on {n}"]
    if isinstance(d.get("down"), bool):
        return [f"Make the GPU exporter stop answering for {n}" if d["down"] else f"Make the GPU exporter answer again for {n}"]
    return []


def _switch(d) -> list[str]:
    return [f"Bring leaf switch {js(d.get('leaf'))} back up" if d.get("state") == "up" else f"Take leaf switch {js(d.get('leaf'))} down (its nodes drop out)"]


def _upgrade(d) -> list[str]:
    return [f"Upgrade {lst([LOGIN if x == 'login' else x for x in orv(d.get('daemons'), [])])} to Slurm {js(d.get('to'))}"]


def _dcgm(d) -> list[str]:
    v = next((k for k in ("stop", "start", "restart") if has(d.get(k))), None)
    return [f"{v[0].upper()}{v[1:]} the real DCGM exporter on {lst(d.get('nodes'))}"] if v else []


def _fault(d) -> list[str]:
    if has(d.get("inject")):
        return [f"Inject the {js(d['inject'])} fault on {lst(d.get('nodes'))}"]
    return [f"Clear the {js(d['clear'])} fault on {lst(d.get('nodes'))}"] if has(d.get("clear")) else []


BY_TARGET = {"ssh": ssh, "auth": auth, "k8s": k8s, "netem": netem, "jira-real": jira_real, "switch": _switch, "slurm-upgrade": _upgrade, "dcgm": _dcgm, "fault": _fault}


def describe(step: dict, raw_ok: bool = True) -> str:
    """One sentence for a step (`target`, `do`). The page falls back to the raw JSON of `do` when a step has no words (`raw_ok`); an
    export must not carry a raw action, so it says only which target the step was for."""
    d = step.get("do") or {}
    by = BY_TARGET.get(step.get("target"))
    words = by(d) if by else keyed(d, lst(d.get("nodes")), step.get("target"))
    if words:
        return "; ".join(words)
    return json.dumps(d, separators=(",", ":"), ensure_ascii=False) if raw_ok else f"A step on {js(step.get('target'))}"


SLURM_STATE = {"drain": "drained", "down": "down", "idle": "idle", "alloc": "allocated", "mix": "mixed", "resv": "reserved"}


def _jira(a) -> str:
    return "real Jira" if a.get("target") == "real" else "Jira"


def _used(v, what: str) -> str:
    return f"at least {pct(v)} {what}"


def _mount(a) -> str:
    bits = [x for x in (_used(a["min_used_pct"], "used") if given(a, "min_used_pct") else "",
                        _used(a["min_inodes_used_pct"], "of its inodes used") if given(a, "min_inodes_used_pct") else "",
                        f"{js(a['quota_exceeded'])} over quota" if has(a.get("quota_exceeded")) else "",
                        ("stale" if has(a["stale"]) else "not stale") if given(a, "stale") else "") if x]
    return f"{js(a.get('mount'))} on {js(orv(a.get('node'), 'cn001'))}: {', '.join(bits) or 'mounted'}"


def _issue_status(a) -> str:
    head = count(a["count"], f"{_jira(a)} ticket") if given(a, "count") else f"The {_jira(a)} tickets"
    return f"{head} matching “{js(a.get('jql'))}” {'is' if num(a.get('count')) == 1 else 'are'} {js(a.get('status'))}"


def _issue_count(a) -> str:
    return f"{count(a.get('count'), f'{_jira(a)} ticket')} {'matches' if num(a.get('count')) == 1 else 'match'} “{js(a.get('jql'))}”"


EXPECT = {
    "slurm.node_state": lambda a: f"Slurm shows {js(a.get('node'))} as {SLURM_STATE.get(js(a.get('state')).lower()) or js(a.get('state'))}",
    "slurm.job_state": lambda a: f"A job on {js(a.get('node'))} is {js(a.get('state')).lower()} in Slurm",
    "slurm.stepd": lambda a: f"No slurmstepd runs on {js(a.get('node'))}" if a.get("alive") is False else f"A slurmstepd runs on {js(a.get('node'))}",
    "k8s.node_ready": lambda a: f"Kubernetes node {js(a.get('node'))} is {'NotReady' if a.get('ready') is False else 'Ready'}",
    "k8s.pod_status": lambda a: f"Pod {js(a.get('namespace'))}/{js(a.get('name'))} shows {js(a.get('reason'))}",
    "k8s.metrics_api": lambda a: f"The metrics API {'is unavailable' if a.get('served') is False else 'answers'}",
    "k8s.node_gpus": lambda a: f"Kubernetes node {js(a.get('node'))} allocates {nn(a.get('allocatable'), 0)} of {a.get('resource') or 'nvidia.com/gpu'}",
    "k8s.node_kubelet": lambda a: f"Kubernetes node {js(a.get('node'))} reports a kubelet {a.get('skew') or 'different'} than the API server",
    "k8s.pods_on_node": lambda a: f"At least {count(nn(a.get('min'), 1), 'Running pod')} ({js(a.get('selector'))}, {js(a.get('namespace'))}) on Kubernetes node {js(a.get('node'))}",
    "storage.mount": _mount,
    "jira.issue_created": lambda a: f"A {_jira(a)} ticket is filed with the labels {lst(a.get('labels'))}",
    "jira.issue_status": _issue_status,
    "jira.issue_count": _issue_count,
}


def expect_words(label) -> str:
    """One sentence for an expectation; the engine's label (`slurm.node_state {"node": "cn001", ...}`) when the kind is unknown."""
    text = js(label) if label else ""
    m = re.fullmatch(r"(\S+) (\{.*\})", text, re.DOTALL)
    if not m or m.group(1) not in EXPECT:
        return text
    try:
        return EXPECT[m.group(1)](json.loads(m.group(2)))
    except ValueError:
        return text
