"""Blast radius (#315): what a fault means for a client, as declared data.

One reviewed table: for each fault the page can apply (the node actions of `cluster.ACTIONS`), the services a client
reads that the fault changes, and one plain sentence saying what that client would observe. It is *declared* from the
simulator's own rules (`benchctl/scenario.side_effects`: a power change also reaches the BCM, the GPU exporter and the
node's Slurm state; every fault files, and every recovery resolves, the node's Jira ticket); nothing here is measured, and
the page says so on every caption. `tests/test_graph.py` checks that every service named here has a client edge in the
declared graph, so the table cannot drift from the edge model.
"""
from benchctl import scenario

JIRA = "jira-mock"
FAULTS = {
    "power-off": {"services": ["bmc-farm", "bcm-mock", "gpu-exporter", JIRA],
                  "caption": "The BMC reports the node Off, the BCM marks it down and its GPU metric series stop; Slurm sees it freeze; a Jira ticket is filed."},
    "power-on": {"services": ["bmc-farm", "bcm-mock", "gpu-exporter", JIRA],
                 "caption": "The BMC reports the node On again, the BCM state recovers and its GPU metrics resume; the ticket is resolved."},
    "overheat": {"services": ["bmc-farm", JIRA],
                 "caption": "The BMC reads 92 °C on the node's CPU (a client's temperature alert fires); a Jira ticket is filed."},
    "fan-fail": {"services": ["bmc-farm", JIRA],
                 "caption": "The BMC reports failed fans and a degraded thermal status; a Jira ticket is filed."},
    "gpu-xid": {"services": ["gpu-exporter", JIRA],
                "caption": "The GPU exporter publishes XID 79 for a GPU on the node (a client sees a GPU error); a Jira ticket is filed."},
    "ib-down": {"services": ["gpu-exporter", JIRA],
                "caption": "The exporter reports an InfiniBand port down on the node; a Jira ticket is filed."},
    "nic-flap": {"services": ["gpu-exporter", JIRA],
                 "caption": "The exporter reports the node's NIC flapping; a Jira ticket is filed."},
    "clear": {"services": ["bmc-farm", "gpu-exporter", JIRA],
              "caption": "The BMC and exporter faults on the node are cleared, it is powered on and thawed; its tickets are resolved."},
}
# a fault applied to a service itself (a scenario step with no node action): what a client of that service sees
SERVICES = {"bmc-farm": "Clients reading Redfish from the BMC farm see the injected condition.",
            "bcm-mock": "Clients calling the BCM see the injected condition.",
            "gpu-exporter": "Prometheus and other clients scraping the GPU exporter see the injected condition.",
            "jira-mock": "Clients using Jira see the injected failure (an error status or a slow answer)."}
BASIS = "declared from the simulator's rules, not measured"

# A scenario step of each target kind (ADR 0086 phase 2): the parts a client reads that the step changes (ids the Circuit view draws),
# and one sentence. A step's blast is its own entry plus the entries of the steps it implies (`scenario.side_effects`: a BMC power change
# also reaches the BCM, the GPU exporter, DCGM, Slurm and Jira). `tests/test_blast_targets.py` fails when a target kind the engine
# accepts has no entry here, or names a part the Circuit view does not draw.
SLURM = ["slurmrestd", "login"]  # clients read Slurm through slurmrestd, or with sinfo and squeue over SSH on the login node
TARGETS = {
    "bmc-farm": {"parts": ["bmc-farm"], "caption": "Clients reading Redfish from the BMC farm see the change: power, temperature, fans or an event."},
    "bcm-mock": {"parts": ["bcm-mock"], "caption": "Clients calling the BCM see the node's device state change."},
    "gpu-exporter": {"parts": ["gpu-exporter"], "caption": "Prometheus and other clients scraping the GPU exporter see the change: an XID, a port, a link, a mount or a quota."},
    "jira": {"parts": [JIRA], "caption": "Clients using Jira see the tickets change, or an injected error or slow answer."},
    "jira-real": {"parts": ["jira-real"], "caption": "Clients of the real Jira see its real answer: a timeout, a 401, a CAPTCHA, a rate limit, or the tickets."},
    "dcgm": {"parts": ["dcgm-exporter"], "caption": "Clients scraping the real DCGM exporter see it stop, start or restart."},
    "slurm": {"parts": SLURM, "caption": "Clients reading Slurm (slurmrestd, or sinfo and squeue over SSH) see the node or job change."},
    "switch": {"parts": SLURM, "caption": "Clients reading Slurm see every node under the leaf switch drop out, then come back."},
    "slurm-upgrade": {"parts": SLURM, "caption": "Clients see Slurm restart on the new release: slurmrestd and the commands answer with its version."},
    "ssh": {"parts": ["login"], "caption": "Clients connecting over SSH see the failure: refused, hung, a changed host key, a rejected key or a session limit."},
    "auth": {"parts": [], "caption": "Clients see the real error of an expired or wrong credential: a 401, an expired certificate or a rejected munge message."},
    "k8s": {"parts": ["k8s-api"], "caption": "Clients reading Kubernetes (kubectl over SSH) see nodes, pods or queues change."},
    "netem": {"parts": ["login"], "caption": "Traffic to and from the node is slow or lossy: clients that reach it over SSH or through Slurm see delays."},
}
AUTH_PARTS = {"slurm_token_ttl": ["slurmrestd"], "teleport_cert_ttl": ["teleport"], "teleport_cert_restore": ["teleport"], "munge_wrong_key": SLURM,
              "munge_restore": SLURM, "bmc_tls_expired": ["bmc-farm"], "k8s_cert_expired": ["k8s-api"],
              "kube_binding_missing": ["k8s-api"], "kube_impersonation_revoked": ["k8s-api"], "kube_binding_restore": ["k8s-api"],
              "teleport_outage": ["teleport"]}


def _parts(step) -> list[str]:
    if step.target == "auth":
        return [p for k in step.do or {} for p in AUTH_PARTS.get(k, [])]
    return list(TARGETS.get(step.target, {}).get("parts", []))


def step_blast(step) -> dict:
    """`blast` (the parts whose clients may see a change, in order, once each) and `blast_caption` for one planned step."""
    parts = [p for s in (step, *scenario.side_effects(step)) for p in _parts(s)]
    entry = TARGETS.get(step.target)
    return {"blast": list(dict.fromkeys(parts)), "blast_caption": entry["caption"] if entry else ""}


def preview(sc: dict, sim: dict | None = None) -> dict:
    """`scenario.preview` with each step's declared blast: what the page draws before Play (ADR 0086 phase 2)."""
    pv = scenario.preview(sc, sim)
    for row, step in zip(pv["steps"], scenario.plan(sc), strict=True):
        row.update(step_blast(step))
    return pv


def payload() -> dict:
    """What `/api/cluster` carries for the page."""
    return {"faults": FAULTS, "services": SERVICES, "basis": BASIS}
