"""Blast radius (#315): what a fault means for a client, as declared data.

One reviewed table: for each fault the page can apply (the node actions of `cluster.ACTIONS`), the services a client
reads that the fault changes, and one plain sentence saying what that client would observe. It is *declared* from the
simulator's own rules (`benchctl/scenario.side_effects`: a power change also reaches the BCM, the GPU exporter and the
node's Slurm state; every fault files, and every recovery resolves, the node's Jira ticket); nothing here is measured, and
the page says so on every caption. `tests/test_graph.py` checks that every service named here has a client edge in the
declared graph, so the table cannot drift from the edge model.
"""

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


def payload() -> dict:
    """What `/api/cluster` carries for the page."""
    return {"faults": FAULTS, "services": SERVICES, "basis": BASIS}
