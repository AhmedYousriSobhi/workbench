"""Where to reach every entity and where its credential lives (issue 115).

Addresses are the containers' fixed private IPs at their standard ports (`benchctl/addressing.py`, ADR 0018): the host
routes the Docker network, so a client on this machine connects to them directly. The ports published on 127.0.0.1 are
the fallback (`also on`). Credentials are named by path only, never read, so this page cannot leak a secret."""
from benchctl import addressing, compose, logins, teleport
from benchctl.describe import BMC_TLS_PORT

SECRETS = "compose/generated/secrets"
SSH_KEY = f"{SECRETS}/ssh/id_ed25519"


def login_rows(sim: dict, users: list[str], user: str, ssh_for) -> list[dict]:
    """One row per login node, the way its mode says to reach it (ADR 0023)."""
    ips, rows = addressing.plan(sim)["logins"], []
    for i, (name, mode) in enumerate(logins.nodes(sim)):
        title = "Login node (head node)" if name == "login" else f"Login node {name}"
        tsh = (f"tsh --proxy={teleport.proxy_addr(sim)} --insecure -i {SECRETS}/teleport/identity/{user} ssh {user}@{name}")
        port = compose.SSH_PORT + i
        if mode == "teleport":
            rows.append({"id": name, "name": f"{title}, Teleport only", "kind": "teleport", "address": f"via the Teleport proxy {teleport.proxy_addr(sim)}",
                         "command": tsh, "credential": f"{SECRETS}/teleport/identity/{user}",
                         "how": f"Users: {', '.join(users) or 'none'}. No published port and no key login: only a Teleport certificate gets in "
                                f"(a key is refused). An identity file per user, or `tsh login` after `benchctl add invite <user>`."})
            continue
        how = (f"Users: {', '.join(users) or 'none'}. Key login only, no password. Also on 127.0.0.1:{port}. "
               "`benchctl ssh-key install` copies the key to ~/.ssh/workbench_ed25519.")
        if mode == "both":
            how += f" It also accepts Teleport: `{tsh}`."
        rows.append({"id": name, "name": title + (", direct SSH and Teleport" if mode == "both" else ""), "kind": "ssh",
                     "address": f"{ips[name]}:22", "command": ssh_for(ips[name]), "credential": SSH_KEY, "how": how})
    return rows


def entities(sim: dict) -> list[dict]:
    """One row per entity: name, what it is, how to reach it, and where its credential is (or `None`)."""
    users = [u["name"] for u in sim.get("users", [])]
    user = users[0] if users else "alice"
    ip = addressing.plan(sim)["services"]
    first = logins.direct(sim)  # the nodes are reached from a login node that takes a key; with none, through Teleport
    login = addressing.plan(sim)["logins"][first[0][0]] if first else addressing.LOGIN_IP
    ssh = f"ssh -i {SSH_KEY} {user}@{login}"

    def ssh_for(ip: str) -> str:
        return f"ssh -i {SSH_KEY} {user}@{ip}"
    bmc, bcm, jira = ip["bmc-farm"], ip["bcm-mock"], ip["jira-mock"]
    return [
        *login_rows(sim, users, user, ssh_for),
        {"id": "nodes", "name": "Compute and GPU nodes", "kind": "ssh", "address": "10.141.0.x (compute), 10.141.100.x (GPU)",
         "command": f"{ssh} ssh <node> hostname" if first else f"tsh --proxy={teleport.proxy_addr(sim)} --insecure -i {SECRETS}/teleport/identity/{user} ssh {user}@<node>",
         "credential": SSH_KEY if first else f"{SECRETS}/teleport/identity/{user}",
         "how": "Same key and users, from the login node. The exact IPs: `benchctl describe node <node>`."},
        {"id": "teleport", "name": "Teleport (SSH through a proxy)", "kind": "https", "address": f"https://{teleport.proxy_addr(sim)}",
         "command": f"tsh --proxy={teleport.proxy_addr(sim)} --insecure -i {SECRETS}/teleport/identity/{user} ls",
         "credential": f"{SECRETS}/teleport/identity/{user}",
         "how": "Only when the Teleport part is on (`benchctl start --access teleport`). An identity file per user needs no password; for "
                "`tsh login` or the browser, `benchctl add invite <user>` prints a one-time link (the page cannot run it: no Docker, W-3). "
                f"Also {teleport.DNS_NAME} (a container alias; on this machine add `{teleport.proxy_ip(sim)} {teleport.DNS_NAME}` to /etc/hosts) and 127.0.0.1:{teleport.PROXY_PORT} as a fallback. Self-signed certificate, hence --insecure."},
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
