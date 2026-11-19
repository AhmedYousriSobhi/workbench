"""Every fault a shipped scenario injects has a restore step at or after the last time it is injected (issue 830).

A scenario that stops half-way leaves its fault in place (ADR 0052), so the file must at least undo what it does. This reads the
files only; it never runs a step. A fault that ends by itself is not listed (a `count`-bounded Jira failure, a Slurm token that
carries its own `exp`, a `reboot` that Slurm completes, a job that ends).
"""
from benchctl import scenario


def _tags(target: str, do: dict) -> tuple[set[str], set[str]]:
    """(faults, restores) one step makes, as short tags that pair up."""
    f: set[str] = set()
    r: set[str] = set()
    if target in ("slurm", "switch"):
        if target == "switch":
            (f if do.get("state") == "down" else r).add("drain")
        for key, tag in (("power_off", "power"), ("slurmd_stop", "slurmd"), ("drain", "drain"), ("down", "drain"), ("power_down", "powerdown")):
            if do.get(key):
                f.add(tag)
        for key, tag in (("power_on", "power"), ("slurmd_start", "slurmd"), ("resume", "drain"), ("power_up", "powerdown")):
            if do.get(key):
                r.add(tag)
    elif target == "bmc-farm":
        if do.get("power") == "Off" or do.get("fan_fail") or do.get("temp") or do.get("ramp") or do.get("event") and do.get("severity") != "OK":
            f.add("bmc")
        if do.get("clear") or do.get("power") == "On":
            r.add("bmc")
    elif target == "bcm-mock":
        (f if do.get("state") == "DOWN" else r).add("bcm")
    elif target == "gpu-exporter":
        st = do.get("storage")
        if st:
            (r if st.get("reset") else f).add("storage")
        if do.get("xid") or do.get("ib_down") or do.get("nic_flap"):
            f.add("gpu")
        if do.get("clear") or ("ib_down" in do and not do["ib_down"]) or ("nic_flap" in do and not do["nic_flap"]):
            r.add("gpu")
    elif target == "fault":
        (f if "inject" in do else r).add("model:" + str(do.get("inject") or do.get("clear")))
    elif target == "jira":
        fail = do.get("failure")
        if fail is not None and not fail.get("count"):  # a count-bounded failure ends by itself
            (f if fail.get("mode") else r).add("jira")
        elif fail is not None and fail.get("mode") is None:
            r.add("jira")
    elif target == "jira-real":
        for fault, tag, down in (("outage", "outage", True), ("restore", "outage", False), ("rate_limit", "rate", True), ("rate_limit_off", "rate", False),
                                 ("bad_logins", "logins", True), ("clear_logins", "logins", False), ("token_revoke", "token", True),
                                 ("token_restore", "token", False), ("proxy_off", "proxy", False)):
            if do.get("fault") == fault:
                (f if down else r).add(tag)
        if str(do.get("fault", "")).startswith("proxy_") and do["fault"] != "proxy_off":
            f.add("proxy")
    elif target == "ssh":
        if do.get("state") in ("down", "hung"):
            f.add("login")
        if do.get("state") == "up" or do.get("reset"):
            r.add("login")
        if do.get("rotate_host_key") or do.get("reject_auth") or do.get("max_sessions"):
            f.add("sshd")
        if do.get("reset"):
            r.add("sshd")
        if "node_sshd" in do:
            (f if do["node_sshd"] == "down" else r).add("node-sshd")
    elif target == "auth":
        for key, tag in (("munge_wrong_key", "munge"), ("teleport_cert_ttl", "teleport")):
            if key in do:
                f.add(tag)
        for key, tag in (("munge_restore", "munge"), ("teleport_cert_restore", "teleport")):
            if key in do:
                r.add(tag)
        for key in ("bmc_tls_expired", "k8s_cert_expired", "kube_impersonation_revoked", "teleport_outage"):
            if key in do:
                (f if do[key] else r).add({"kube_impersonation_revoked": "kube", "teleport_outage": "tele-outage"}.get(key, key))
        if "kube_binding_missing" in do:
            f.add("kube")
        if do.get("kube_binding_restore"):
            r.add("kube")
    elif target == "k8s":
        for key, tag in (("down", "notready"), ("cordon", "cordon"), ("drain", "cordon"), ("apply", "apply"), ("pod", "pod")):
            if do.get(key):
                f.add(tag)
        for key, tag in (("up", "notready"), ("uncordon", "cordon"), ("remove", "apply"), ("delete_pod", "pod")):
            if do.get(key):
                r.add(tag)
        if "queue" in do:
            (r if do["queue"].get("active", True) else f).add("queue")
    elif target == "netem":
        (r if do.get("clear") else f).add("netem")
    return f, r


def test_every_fault_in_a_shipped_scenario_is_undone_by_a_later_step():
    problems = []
    for sc in scenario.list_all():
        last_fault: dict[str, float] = {}
        last_restore: dict[str, float] = {}
        for s in sc["steps"]:
            at = scenario.parse_duration(s["at"])
            f, r = _tags(s["target"], s.get("do") or {})
            for tag in f:
                last_fault[tag] = max(at, last_fault.get(tag, 0))
            for tag in r:
                last_restore[tag] = max(at, last_restore.get(tag, 0))
        problems += [f"{sc['name']}: {tag} is injected at {at:g}s and never restored after it" for tag, at in last_fault.items()
                     if last_restore.get(tag, -1) < at]
    assert problems == []


def test_the_restore_check_sees_a_missing_restore():
    f, r = _tags("ssh", {"state": "down"})
    assert f == {"login"} and not r
    f, r = _tags("auth", {"munge_wrong_key": ["cn001"]})
    assert f == {"munge"} and not r
    f, r = _tags("slurm", {"slurmd_start": ["cn001"]})
    assert r == {"slurmd"} and not f
