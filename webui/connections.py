"""Where to reach every entity and where its credential lives (issue 115).

Addresses are the ones the compose file publishes on the host (`benchctl/compose.py`, `compose/observability.yml`);
credentials are named by path only, never read, so this page cannot leak a secret. The container IP is not here: it is
assigned by Docker at start and this page has no Docker access by design; `benchctl describe node NAME` prints it."""
from benchctl import compose
from benchctl.describe import BMC_TLS_PORT

SECRETS = "compose/generated/secrets"
SSH_KEY = f"{SECRETS}/ssh/id_ed25519"


def entities(sim: dict) -> list[dict]:
    """One row per entity: name, what it is, how to reach it, and where its credential is (or `None`)."""
    users = [u["name"] for u in sim.get("users", [])]
    user = users[0] if users else "alice"
    ssh = f"ssh -i {SSH_KEY} -p {compose.SSH_PORT} {user}@127.0.0.1"
    return [
        {"id": "login", "name": "Login node", "kind": "ssh", "address": f"127.0.0.1:{compose.SSH_PORT}", "command": ssh,
         "credential": SSH_KEY, "how": f"Users: {', '.join(users) or 'none'}. Key login only, no password. "
                                       "`benchctl ssh-key install` copies the key to ~/.ssh/workbench_ed25519."},
        {"id": "nodes", "name": "Compute and GPU nodes", "kind": "ssh", "address": "reached from the login node",
         "command": f"{ssh} ssh <node> hostname", "credential": SSH_KEY,
         "how": "Same key and users. Container IP: `benchctl describe node <node>`."},
        {"id": "bmc", "name": "BMC farm (Redfish)", "kind": "https", "address": f"https://127.0.0.1:{BMC_TLS_PORT}/redfish/v1",
         "command": f"curl -k -H 'Host: bmc-<node>' -u admin:<password> https://127.0.0.1:{BMC_TLS_PORT}/redfish/v1/Systems/1",
         "credential": f"{SECRETS}/bmc_credentials", "how": "User admin; the password is the part after `admin:` in that file. "
                                                              "Plain HTTP without login: http://127.0.0.1:8000/<node>/redfish/v1."},
        {"id": "bcm", "name": "BCM mock (CMDaemon JSON)", "kind": "http", "address": "http://127.0.0.1:8081/json",
         "command": "curl -s -X POST http://127.0.0.1:8081/json -d '{\"service\":\"cmdevice\",\"call\":\"getDevices\"}'",
         "credential": None, "how": "No login."},
        {"id": "jira", "name": "Jira mock", "kind": "http", "address": "http://127.0.0.1:8080",
         "command": "curl -s http://127.0.0.1:8080/rest/api/2/serverInfo", "credential": None, "how": "No login; any basic or bearer auth is accepted."},
        {"id": "slurm", "name": "Slurm REST (slurmrestd)", "kind": "http", "address": "on the cluster network only (port 6820)",
         "command": "benchctl exec slurmctld -- scontrol token username=alice", "credential": None,
         "how": "JWT, minted per user with scontrol token (not root). Not published on the host."},
        {"id": "gpu", "name": "GPU exporter (DCGM metrics)", "kind": "http", "address": "http://127.0.0.1:9400/metrics",
         "command": "curl -s http://127.0.0.1:9400/metrics | head", "credential": None, "how": "No login."},
        {"id": "prometheus", "name": "Prometheus", "kind": "http", "address": "http://127.0.0.1:9090",
         "command": "curl -s http://127.0.0.1:9090/-/ready", "credential": None, "how": "No login."},
        {"id": "grafana", "name": "Grafana", "kind": "http", "address": "http://127.0.0.1:3000",
         "command": f"cat {SECRETS}/grafana_viewer_token", "credential": f"{SECRETS}/grafana_viewer_token",
         "how": "A read-only viewer token for clients; it changes at every `benchctl up`."},
        {"id": "ui", "name": "This page", "kind": "http", "address": "http://127.0.0.1:8088",
         "command": f"cat {SECRETS}/ui_token", "credential": f"{SECRETS}/ui_token", "how": "Local only; the token opens the page."},
    ]
