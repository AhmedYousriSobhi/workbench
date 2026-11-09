"""Where to reach every entity and where its credential lives (issue 115).

Addresses are the containers' fixed private IPs at their standard ports (`benchctl/addressing.py`, ADR 0018): the host
routes the Docker network, so a client on this machine connects to them directly. The ports published on 127.0.0.1 are
the fallback (`also on`). Credentials are named by path only, never read, so this page cannot leak a secret."""
from benchctl import (
    addressing,
    compose,
    controller,
    dcgm,
    jira_real,
    k8s,
    logins,
    real_gpu,
    teleport,
)
from benchctl.describe import BMC_TLS_PORT

SECRETS = "compose/generated/secrets"
SSH_KEY = f"{SECRETS}/ssh/id_ed25519"
# IdentitiesOnly: with other keys in ssh-agent, ssh offers them first and sshd ends the login after 6 ("Too many authentication failures")
SSH_OPTS = f"-i {SSH_KEY} -o IdentitiesOnly=yes"


SERVES = {"slurm": "Slurm tools only (s-login).", "k8s": "kubectl only (k-login): no Slurm configuration here.",
          "both": "Slurm tools, and kubectl when the Kubernetes part is on."}


def login_rows(sim: dict, users: list[str], user: str, ssh_for) -> list[dict]:
    """One row per login node, the way its mode says to reach it (ADR 0023)."""
    ips, rows, kinds = addressing.plan(sim)["logins"], [], logins.kinds(sim)
    for i, (name, mode) in enumerate(logins.nodes(sim)):
        title = "Login node (head node)" if name == "login" else f"Login node {name}"
        if kinds[name] != "both":  # an s-login or a k-login says so
            title += f", {logins.KIND_LABEL[kinds[name]]}"
        tsh = (f"tsh --proxy={teleport.proxy_addr(sim)} --insecure -i {SECRETS}/teleport/identity/{user} ssh {user}@{name}")
        port = compose.SSH_PORT + i
        if mode == "teleport":
            rows.append({"id": name, "name": f"{title}, Teleport only", "kind": "teleport", "address": f"via the Teleport proxy {teleport.proxy_addr(sim)}",
                         "command": tsh, "credential": f"{SECRETS}/teleport/identity/{user}",
                         "how": f"Users: {', '.join(users) or 'none'}. No published port and no key login: only a Teleport certificate gets in "
                                f"(a key is refused). An identity file per user, or `tsh login` after `benchctl add invite <user>`."})
            continue
        how = (f"Users: {', '.join(users) or 'none'}. {SERVES[kinds[name]]} Key login only, no password. Also on 127.0.0.1:{port}. "
               "`benchctl ssh-key install` copies the key to ~/.ssh/workbench_ed25519.")
        if mode == "both":
            how += f" It also accepts Teleport: `{tsh}`."
        rows.append({"id": name, "name": title + (", direct SSH and Teleport" if mode == "both" else ""), "kind": "ssh",
                     "address": f"{ips[name]}:22", "command": ssh_for(ips[name]), "credential": SSH_KEY, "how": how})
    return rows


# What a row may offer besides its command (the GUI-access registry, #273). Built here on the host; the page renders
# `kind` and never builds a URL itself, so nothing typed in the browser can become a link (W-3).
ACTION_KINDS = {"link": ("http", "https"), "copy": (), "uri": ("ssh",), "post": ()}
POST_ACTIONS = {"/api/jira-real/refresh"}  # a button that asks the governor; the page never builds the path itself
LOCAL = "127.0.0.1"  # links use the published localhost ports: they work without the Docker network route


def link(label: str, url: str) -> dict:
    return {"kind": "link", "label": label, "value": url}


def copy(label: str, command: str) -> dict:
    return {"kind": "copy", "label": label, "value": command}


def valid_action(a: dict) -> bool:
    """A known kind, a text label, and for link/uri an allowed scheme on localhost or a cluster address; never 0.0.0.0."""
    from urllib.parse import urlsplit

    if a.get("kind") not in ACTION_KINDS or not a.get("label") or not a.get("value"):
        return False
    if a["kind"] == "post":
        return a["value"] in POST_ACTIONS
    schemes = ACTION_KINDS[a["kind"]]
    if not schemes:
        return True
    u = urlsplit(a["value"])
    return u.scheme in schemes and bool(u.hostname) and u.hostname != "0.0.0.0" and not u.password


def actions_for(e: dict, sim: dict, user: str) -> list[dict]:
    """The one-click actions of a row, from its id and kind. A part that publishes no localhost port gets no link."""
    i, k = e["id"], e["kind"]
    if k == "ssh" and i == "nodes":
        return [copy("Copy benchctl ssh", f"benchctl ssh {user}@<node>")]  # benchctl adds the key and the jump (#275)
    if k == "ssh" and i in ("controller", "jump"):
        port = controller.SSH_PORT if i == "controller" else controller.JUMP_PORT
        return [copy("Copy ssh", f"ssh {SSH_OPTS} -p {port} {user}@{LOCAL}"), {"kind": "uri", "label": "Open in SSH app (needs a handler)", "value": f"ssh://{user}@{LOCAL}:{port}"}]
    if k == "ssh":
        port = compose.SSH_PORT + [n for n, _ in logins.nodes(sim)].index(i)
        # Copy first: it works everywhere; ssh:// needs a handler the desktop may not have registered (#281)
        return [copy("Copy benchctl ssh", f"benchctl ssh {user}@{i}"),
                {"kind": "uri", "label": "Open in SSH app (needs a handler)", "value": f"ssh://{user}@{LOCAL}:{port}"}]
    return {
        "teleport": [link("Open the Teleport web UI", f"https://{LOCAL}:{teleport.PROXY_PORT}")],
        "k8s": [copy("Copy kubectl over SSH", f"benchctl ssh {user}@{next((n for n, k in logins.kinds(sim).items() if k in ('k8s', 'both')), 'login')} kubectl get nodes")],
        "bmc": [link("Open Redfish API (JSON, certificate warning)", f"https://{LOCAL}:{BMC_TLS_PORT}/redfish/v1")],
        "jira": [link("Open jira-mock API (JSON)", f"http://{LOCAL}:8080")],
        "jira-real": [link("Open Jira (real)", f"http://{LOCAL}:{jira_real.PORT}"),
                      {"kind": "post", "label": "Refresh licence (restarts Jira)", "value": "/api/jira-real/refresh"}],
        "gpu": [link("Open the metrics", f"http://{LOCAL}:9400/metrics")],
        "dcgm": [link("Open the real DCGM metrics", f"http://{LOCAL}:{dcgm.PORT}/metrics")],
        "real-gpu": [link("Open the real GPU metrics", f"http://{LOCAL}:{real_gpu.PORT}/metrics")],
        "prometheus": [link("Open Prometheus", f"http://{LOCAL}:9090")],
        "grafana": [link("Open Grafana", f"http://{LOCAL}:3000")],
        "slurm": [copy("Copy sinfo", "benchctl exec slurmctld -- sinfo")],
        "ui": [link("Open this page", f"http://{LOCAL}:8088")],
    }.get(i, [])


def entities(sim: dict) -> list[dict]:
    """The rows of `_rows`, each with its `actions`."""
    users = [u["name"] for u in sim.get("users", [])]
    rows = _rows(sim)
    for e in rows:
        e["actions"] = actions_for(e, sim, users[0] if users else "alice")
    return rows


def _rows(sim: dict) -> list[dict]:
    """One row per entity: name, what it is, how to reach it, and where its credential is (or `None`)."""
    users = [u["name"] for u in sim.get("users", [])]
    user = users[0] if users else "alice"
    ip = addressing.plan(sim)["services"]
    first = logins.direct(sim)  # the nodes are reached from a login node that takes a key; with none, through Teleport
    login = addressing.plan(sim)["logins"][first[0][0]] if first else addressing.LOGIN_IP
    ssh = f"ssh {SSH_OPTS} {user}@{login}"
    kfirst = next((n for n, _ in logins.direct(sim) if logins.kinds(sim)[n] in ("k8s", "both")), None)  # kubectl runs on a login that serves Kubernetes
    kssh = f"ssh {SSH_OPTS} {user}@{addressing.plan(sim)['logins'][kfirst]}" if kfirst else ssh
    sfirst = next((n for n, _ in logins.direct(sim) if logins.kinds(sim)[n] in ("slurm", "both")), None)
    ssh = f"ssh {SSH_OPTS} {user}@{addressing.plan(sim)['logins'][sfirst]}" if sfirst else ssh  # the nodes are reached from an s-login

    def ssh_for(ip: str) -> str:
        return f"ssh {SSH_OPTS} {user}@{ip}"
    admin = (controller.allowed_users(sim) or [user])[0]
    bmc, bcm, jira = ip["bmc-farm"], ip["bcm-mock"], ip["jira-mock"]
    return [
        *login_rows(sim, users, user, ssh_for),
        {"id": "nodes", "name": "Compute and GPU nodes", "kind": "ssh", "address": "10.141.0.x (compute), 10.141.100.x (GPU)",
         "command": f"{ssh} ssh <node> hostname" if first else f"tsh --proxy={teleport.proxy_addr(sim)} --insecure -i {SECRETS}/teleport/identity/{user} ssh {user}@<node>",
         "credential": SSH_KEY if first else f"{SECRETS}/teleport/identity/{user}",
         "how": "Same key and users, from the login node. The exact IPs: `benchctl describe node <node>`."},
        {"id": "controller", "name": "Controller node (slurmctld, BCM head node)", "kind": "ssh", "address": f"{ip['slurmctld']}:22 (alias `controller`)",
         "command": f"ssh {SSH_OPTS} {admin}@{ip['slurmctld']} scontrol ping", "credential": SSH_KEY,
         "how": f"Only with the controller on (`benchctl up --controller`). Key login for admins only: {', '.join(controller.allowed_users(sim)) or 'whitelist in sim.yaml controller.allow (users or groups)'}; others are refused. "
                f"slurmctld runs here and `cmsh` lives here, not on the login nodes. Also 127.0.0.1:{controller.SSH_PORT}. Without it the login node acts as the head node."},
        {"id": "jump", "name": "Jump host (bastion)", "kind": "ssh", "address": f"{ip['jump']}:22",
         "command": f"ssh {SSH_OPTS} -o ProxyCommand='ssh {SSH_OPTS} -W %h:%p {user}@{ip['jump']}' {user}@{login}", "credential": SSH_KEY,
         "how": f"Only with the jump part on (`benchctl up --access jump`). A bare sshd with nothing else installed: use it as a jump host (`ssh -J`, or a `ProxyCommand` that carries the key as in the command here: `-J` does not pass `-i` or `-o` to the jump leg) to reach a key-login node or the controller. "
                f"Teleport nodes keep Teleport. Also 127.0.0.1:{controller.JUMP_PORT}."},
        {"id": "teleport", "name": "Teleport (SSH through a proxy)", "kind": "https", "address": f"https://{teleport.proxy_addr(sim)}",
         "command": f"tsh --proxy={teleport.proxy_addr(sim)} --insecure -i {SECRETS}/teleport/identity/{user} ls",
         "credential": f"{SECRETS}/teleport/identity/{user}",
         "how": "Only when the Teleport part is on (`benchctl start --access teleport`). An identity file per user needs no password; for "
                "`tsh login` or the browser, `benchctl add invite <user>` prints a one-time link (the page cannot run it: no Docker, W-3). "
                f"Also {teleport.DNS_NAME} (a container alias; on this machine add `{teleport.proxy_ip(sim)} {teleport.DNS_NAME}` to /etc/hosts) and 127.0.0.1:{teleport.PROXY_PORT} as a fallback. Self-signed certificate, hence --insecure."},
        {"id": "k8s", "name": "Kubernetes (kubectl)", "kind": "https", "address": f"https://{ip[k8s.API]}:{k8s.API_PORT}",
         "command": f"{kssh} kubectl get nodes", "credential": f"{SECRETS}/k8s/viewer.kubeconfig",
         "how": "Only when the Kubernetes part is on (`benchctl start --workload kubernetes`). On the login node every user has a read-only "
                "~/.kube/config (cluster-wide) and ~/.kube/limited.yaml (namespace team-a only), mode 600; the file here holds the same "
                f"token. The administrator's kubeconfig ({SECRETS}/k8s/admin.kubeconfig) is for you, never for a client. 127.0.0.1:{k8s.API_PORT} is the fallback. "
                "KWOK nodes: pods never really run, so there are no pod logs."},
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
        {"id": "jira-real", "name": "Jira (real, Data Center)", "kind": "http", "address": f"http://{LOCAL}:{jira_real.PORT}",
         "command": f"curl -s http://{LOCAL}:{jira_real.PORT}/status", "credential": str(jira_real.ENV),
         "how": "Only with `benchctl start --with jira-real` then `benchctl setup jira-real`. Sign in as admin or alice with the password in the file; a 3-hour test licence (docs/guides/jira-real.md)."},
        {"id": "slurm", "name": "Slurm REST (slurmrestd)", "kind": "http", "address": f"http://{ip['slurmrestd']}:6820",
         "command": "benchctl exec slurmctld -- scontrol token username=alice", "credential": None,
         "how": "JWT, minted per user with scontrol token (not root). Reachable on the cluster network only, not on 127.0.0.1."},
        {"id": "gpu", "name": "GPU exporter (DCGM metrics)", "kind": "http", "address": f"http://{ip['gpu-exporter-farm']}:9400/metrics",
         "command": f"curl -s http://{ip['gpu-exporter-farm']}:9400/metrics | head", "credential": None, "how": "No login. Also on 127.0.0.1:9400."},
        {"id": "dcgm", "name": "DCGM exporter (real, first GPU node)", "kind": "http", "address": f"http://{LOCAL}:{dcgm.PORT}/metrics",
         "command": f"curl -s http://{LOCAL}:{dcgm.PORT}/metrics | head", "credential": None,
         "how": "Only with `benchctl up --with dcgm`. The official dcgm-exporter on a mock NVML for the first GPU node (ADR 0025); no login, localhost only. A GPU a Slurm job holds reads busy; a cleared XID stays until the container restarts."},
        {"id": "real-gpu", "name": "Real GPU (this machine's NVIDIA card)", "kind": "http", "address": f"http://{LOCAL}:{real_gpu.PORT}/metrics",
         "command": f"curl -s http://{LOCAL}:{real_gpu.PORT}/metrics | head", "credential": None,
         "how": "Only with `benchctl up --with real-gpu` on a machine with an NVIDIA card, its driver and the container toolkit (SPEC C-23). Real series from the real card, labelled Hostname gpu-real; no login, localhost only. `docker exec workbench-real-gpu-1 dcgmi discovery -l` and `nvidia-smi` work in that container."},
        {"id": "prometheus", "name": "Prometheus", "kind": "http", "address": f"http://{ip['prometheus']}:9090",
         "command": f"curl -s http://{ip['prometheus']}:9090/-/ready", "credential": None, "how": "No login. Also on 127.0.0.1:9090."},
        {"id": "grafana", "name": "Grafana", "kind": "http", "address": f"http://{ip['grafana']}:3000",
         "command": f"cat {SECRETS}/grafana_viewer_token", "credential": f"{SECRETS}/grafana_viewer_token",
         "how": "A read-only viewer token for clients; it changes at every `benchctl up`. Also on 127.0.0.1:3000."},
        {"id": "ui", "name": "This page", "kind": "http", "address": "http://127.0.0.1:8088",
         "command": f"cat {SECRETS}/ui_token", "credential": f"{SECRETS}/ui_token", "how": "Local only; the token opens the page."},
    ]
