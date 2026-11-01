"""Where to reach every entity and where its credential lives (issue 115).

Addresses are the containers' fixed private IPs at their standard ports (`benchctl/addressing.py`, ADR 0018): the host
routes the Docker network, so a client on this machine connects to them directly. The ports published on 127.0.0.1 are
the fallback (`also on`). Credentials are named by path only, never read, so this page cannot leak a secret."""
from benchctl import addressing, compose
from benchctl.describe import BMC_TLS_PORT

SECRETS = "compose/generated/secrets"
SSH_KEY = f"{SECRETS}/ssh/id_ed25519"


def entities(sim: dict) -> list[dict]:
    """One row per entity: name, what it is, how to reach it, and where its credential is (or `None`)."""
    users = [u["name"] for u in sim.get("users", [])]
    user = users[0] if users else "alice"
    ip = addressing.plan(sim)["services"]
    login = addressing.LOGIN_IP
    ssh = f"ssh -i {SSH_KEY} {user}@{login}"
    bmc, bcm, jira = ip["bmc-farm"], ip["bcm-mock"], ip["jira-mock"]
    return [
        {"id": "login", "name": "Login node (head node)", "kind": "ssh", "address": f"{login}:22", "command": ssh,
         "credential": SSH_KEY, "how": f"Users: {', '.join(users) or 'none'}. Key login only, no password. Also on 127.0.0.1:{compose.SSH_PORT}. "
                                       "`benchctl ssh-key install` copies the key to ~/.ssh/workbench_ed25519."},
        {"id": "nodes", "name": "Compute and GPU nodes", "kind": "ssh", "address": "10.141.0.x (compute), 10.141.100.x (GPU)",
         "command": f"{ssh} ssh <node> hostname", "credential": SSH_KEY,
         "how": "Same key and users, from the login node. The exact IPs: `benchctl describe node <node>`."},
        {"id": "teleport", "name": "Teleport (SSH through a proxy)", "kind": "https", "address": "https://127.0.0.1:3080",
         "command": f"tsh --proxy=127.0.0.1:3080 --insecure -i {SECRETS}/teleport/identity/{user} ls",
         "credential": f"{SECRETS}/teleport/identity/{user}",
         "how": "Only when the Teleport part is on (`benchctl start --access teleport`). An identity file per user needs no password; for "
                "`tsh login` or the browser, `benchctl add invite <user>` prints a one-time link (the page cannot run it: no Docker, W-3). "
                "Self-signed certificate, hence --insecure."},
        {"id": "bmc", "name": "BMC farm (Redfish)", "kind": "https", "address": f"https://{bmc}:{BMC_TLS_PORT}/redfish/v1",
         "command": f"curl -k -H 'Host: bmc-<node>' -u admin:<password> https://{bmc}:{BMC_TLS_PORT}/redfish/v1/Systems/1",
         "credential": f"{SECRETS}/bmc_credentials", "how": "User admin; the password is the part after `admin:` in that file. One address for "
                                                              f"all nodes, chosen by the Host header; from the login node use bmc-<node>:{BMC_TLS_PORT}. "
                                                              f"Also on 127.0.0.1:{BMC_TLS_PORT}; plain HTTP without login on :8000."},
        {"id": "bcm", "name": "BCM mock (CMDaemon JSON)", "kind": "http", "address": f"http://{bcm}:8081/json",
         "command": f"curl -s -X POST http://{bcm}:8081/json -d '{{\"service\":\"cmdevice\",\"call\":\"getDevices\"}}'",
         "credential": None, "how": "No login. Also on 127.0.0.1:8081."},
        {"id": "jira", "name": "Jira mock", "kind": "http", "address": f"http://{jira}:8080",
         "command": f"curl -s http://{jira}:8080/rest/api/2/serverInfo", "credential": None,
         "how": "No login; any basic or bearer auth is accepted. Also on 127.0.0.1:8080."},
        {"id": "slurm", "name": "Slurm REST (slurmrestd)", "kind": "http", "address": f"http://{ip['slurmrestd']}:6820",
         "command": "benchctl exec slurmctld -- scontrol token username=alice", "credential": None,
         "how": "JWT, minted per user with scontrol token (not root). Reachable on the cluster network only, not on 127.0.0.1."},
        {"id": "gpu", "name": "GPU exporter (DCGM metrics)", "kind": "http", "address": f"http://{ip['gpu-exporter-farm']}:9400/metrics",
         "command": f"curl -s http://{ip['gpu-exporter-farm']}:9400/metrics | head", "credential": None, "how": "No login. Also on 127.0.0.1:9400."},
        {"id": "prometheus", "name": "Prometheus", "kind": "http", "address": f"http://{ip['prometheus']}:9090",
         "command": f"curl -s http://{ip['prometheus']}:9090/-/ready", "credential": None, "how": "No login. Also on 127.0.0.1:9090."},
        {"id": "grafana", "name": "Grafana", "kind": "http", "address": f"http://{ip['grafana']}:3000",
         "command": f"cat {SECRETS}/grafana_viewer_token", "credential": f"{SECRETS}/grafana_viewer_token",
         "how": "A read-only viewer token for clients; it changes at every `benchctl up`. Also on 127.0.0.1:3000."},
        {"id": "ui", "name": "This page", "kind": "http", "address": "http://127.0.0.1:8088",
         "command": f"cat {SECRETS}/ui_token", "credential": f"{SECRETS}/ui_token", "how": "Local only; the token opens the page."},
    ]
