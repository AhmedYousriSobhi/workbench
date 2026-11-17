"""Kubernetes part: a real control plane with KWOK nodes (ADR 0034, SPEC C-2, issue 232).

Five upstream images run as compose services (etcd, kube-apiserver, kube-controller-manager, kube-scheduler and the
KWOK controller), unprivileged, memory-limited and with a fixed address like every other part. KWOK plays the nodes and
pods; there is no kubelet, so a pod that should fail is given the status a kubelet would report by a stage
(`k8s/kwok/stage-faults.yaml`, selected by the `workbench_fault` label). Everything the monitoring client reads is the
real API server's: RBAC, `auth can-i`, `/readyz`, events, describe.

This module generates what the services need (a PKI, kubeconfigs, the node manifests), wires the compose services and
applies the cluster content once the API server answers. It runs `kubectl` from the official image, so no tool needs
installing on the host."""
import base64
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

import yaml

from . import addressing, dra, kubeconfigs, kueue, volcano
from .nodes import node_names

VERSION = "v1.36.1"            # Kubernetes (the control-plane images and kubectl); sim.yaml kubernetes.version overrides
ETCD_IMAGE = "registry.k8s.io/etcd:3.6.10-0"
KWOK_VERSION = "v0.8.0"
KWOK_IMAGE = f"registry.k8s.io/kwok/kwok:{KWOK_VERSION}"
METRICS_SERVER = "k8s-metrics-server"  # the metrics API (issue 382): kwokctl v0.8.0 tests 0.8.1; 0.9.1 is the latest release
METRICS_SERVER_VERSION = "v0.9.1"
METRICS_SERVER_IMAGE = f"registry.k8s.io/metrics-server/metrics-server:{METRICS_SERVER_VERSION}"
METRICS_SERVER_PORT = 10250  # the upstream manifest's secure port; k8s/metrics/metrics-server.yaml names the same
SERVICES = ("k8s-etcd", "k8s-apiserver", "k8s-controller-manager", "k8s-scheduler", "kwok-controller", METRICS_SERVER)
API = "k8s-apiserver"
ENTRYPOINT = {"k8s-etcd": "etcd", "k8s-apiserver": "kube-apiserver", "k8s-controller-manager": "kube-controller-manager",
              "k8s-scheduler": "kube-scheduler", "kwok-controller": "kwok", METRICS_SERVER: "/metrics-server"}
API_PORT = 6443
TELEPORT_USER = "teleport"  # Teleport's Kubernetes Service registers the cluster with this token user; it may impersonate, nothing else (k8s/fixtures/47-rbac-teleport.yaml, ADR 0083)
CONTEXT = "workbench-k8s"  # the kubeconfig context name: Teleport names the registered cluster after it (`tsh kube ls`)
METRICS_USER = "metrics"  # reads /metrics only (k8s/fixtures/45-rbac-metrics.yaml); the page uses it for request counts
# limits in MB (upper bounds for the planning estimate; measured idle 275 MiB, 310 MiB with 50 nodes and the fixtures)
# metrics-server: a planning cap, not measured (benchctl/memory.py NOT_MEASURED); the upstream manifest requests 200 MiB for a real cluster
LIMITS = {"k8s-etcd": 256, "k8s-apiserver": 384, "k8s-controller-manager": 128, "k8s-scheduler": 64, "kwok-controller": 64, METRICS_SERVER: 128}
SECRETS_DIR = Path("compose/generated/secrets/k8s")   # PKI, tokens and kubeconfigs (gitignored)
STATE_DIR = Path("compose/generated/k8s")             # etcd data and the manifests generated from sim.yaml
SRC = Path("k8s")
CONTAINER_PKI = "/pki"
NETWORK = "workbench_default"
KWOK = "kwok-controller"
KWOK_PORT = 10247   # the fake nodes' kubelet port: logs and the resource metrics are served here (issue 872)
KWOK_LOGS = "/logs"  # where k8s/kwok/logs is mounted in kwok-controller (the files the Logs and ClusterLogs objects name)
# KWOK's own objects, applied after their definitions are established: what `kubectl logs` answers, the kubelet /metrics/resource
# endpoint per node, and pod usage from annotations (issue 872, ADR 0084 addendum P1). KWOK reads them because --enable-crds names them.
KWOK_CRDS = ("Stage", "Logs", "ClusterLogs", "Metric", "ClusterResourceUsage")
KWOK_OBJECTS = (("/k8s/kwok/logs.yaml", "KWOK logs"), ("/k8s/kwok/metrics-resource.yaml", "KWOK node metrics"),
                ("/k8s/kwok/usage-from-annotation.yaml", "KWOK pod usage"))
METRICS_API_FILE = "/k8s/metrics/metrics-server.yaml"  # the APIService and the ExternalName Service that routes to METRICS_SERVER
RESOURCE_METRICS_PATH = "metrics.k8s.io/resource-metrics-path"  # metrics-server 0.7.0+ reads a node's kubelet metrics path from it
# Node Feature Discovery / GPU Feature Discovery product label per sim.yaml gpu type
GPU_PRODUCT = {"h200": "NVIDIA-H200", "h100": "NVIDIA-H100-80GB-HBM3", "a100": "NVIDIA-A100-SXM4-80GB",
               "l40s": "NVIDIA-L40S", "v100": "Tesla-V100-SXM2-32GB"}
DEFAULT_NODES = [{"prefix": "kcn", "count": 2, "cpus": 64, "memory_gb": 256, "pool": "cpu"},
                 {"prefix": "kgpu", "count": 2, "cpus": 128, "memory_gb": 1024, "gpus": 8, "gpu_type": "h200", "pool": "gpu"}]


class K8sError(Exception):
    pass


def version(sim: dict) -> str:
    return str((sim.get("kubernetes") or {}).get("version") or VERSION)


def images(sim: dict) -> dict[str, str]:
    v = version(sim)
    return {"k8s-etcd": ETCD_IMAGE, "k8s-apiserver": f"registry.k8s.io/kube-apiserver:{v}",
            "k8s-controller-manager": f"registry.k8s.io/kube-controller-manager:{v}",
            "k8s-scheduler": f"registry.k8s.io/kube-scheduler:{v}", "kwok-controller": KWOK_IMAGE, METRICS_SERVER: METRICS_SERVER_IMAGE}


def kubectl_image(sim: dict) -> str:
    return f"registry.k8s.io/kubectl:{version(sim)}"


def node_groups(sim: dict, gpu_override: int | None = None, cpu_override: int | None = None) -> list[dict]:
    """The Kubernetes node groups: sim.yaml `kubernetes.nodes`, else the defaults. `gpu_override` (--k8s-nodes) sets the
    count of the group that has GPUs, `cpu_override` (--k8s-cpu-nodes) the count of the one that has none."""
    groups = [dict(g) for g in ((sim.get("kubernetes") or {}).get("nodes") or DEFAULT_NODES)]
    for g in groups:
        over = gpu_override if g.get("gpus") else cpu_override
        if over is not None:
            g["count"] = over
    return groups


def node_list(sim: dict, gpu_override: int | None = None, cpu_override: int | None = None) -> list[tuple[str, dict]]:
    """[(name, group)] in order: kcn001, kcn002, kgpu001, ..."""
    return [(f"{g['prefix']}{i:03d}", g) for g in node_groups(sim, gpu_override, cpu_override) for i in range(1, int(g.get("count", 0)) + 1)]


def problems(sim: dict) -> list[str]:
    out = []
    taken = {n for n, _ in node_names(sim)}
    for name, g in node_list(sim):
        if name in taken:
            out.append(f"Kubernetes node {name} has the name of a Slurm node; change the prefix in sim.yaml kubernetes.nodes")
        if g.get("gpus") and str(g.get("gpu_type", "")).lower() not in GPU_PRODUCT:
            out.append(f"unknown gpu_type {g.get('gpu_type')!r} for {name}; known: {', '.join(GPU_PRODUCT)}")
    return out


def node_manifest(name: str, g: dict, kubelet_version: str = VERSION) -> dict:
    """A KWOK node: the labels and extended resources a real GPU node advertises (GPU Feature Discovery), no taint.
    `kubelet_version` is the API server's version, as on a cluster whose kubelets match its control plane: a client reads the
    major and minor from a `vX.Y` prefix to judge version skew, and KWOK's own default (`kwok-v0.8.0`) has none (ADR 0084 addendum)."""
    cpus, mem = int(g.get("cpus", 8)), int(g.get("memory_gb", 32))
    gpus = int(g.get("gpus", 0))
    labels = {"kubernetes.io/hostname": name, "kubernetes.io/os": "linux", "kubernetes.io/arch": "amd64",
              "node.kubernetes.io/instance-type": "gpu" if gpus else "cpu", "type": "kwok", "pool": str(g.get("pool", "cpu"))}
    cap = {"cpu": str(cpus), "memory": f"{mem}Gi", "pods": "110", "ephemeral-storage": "1Ti"}
    if gpus:
        labels.update({"nvidia.com/gpu.present": "true", "nvidia.com/gpu.count": str(gpus),
                       "nvidia.com/gpu.product": GPU_PRODUCT[str(g.get("gpu_type", "h200")).lower()],
                       "nvidia.com/mig.strategy": "none", "nvidia.com/cuda.driver.major": "550"})
        cap["nvidia.com/gpu"] = str(gpus)
    return {"apiVersion": "v1", "kind": "Node",
            "metadata": {"name": name, "labels": labels,
                         "annotations": {"node.alpha.kubernetes.io/ttl": "0", "kwok.x-k8s.io/node": "fake",
                                         RESOURCE_METRICS_PATH: f"/metrics/nodes/{name}/metrics/resource"}},
            "status": {"capacity": dict(cap), "allocatable": dict(cap),
                       "nodeInfo": {"architecture": "amd64", "kubeletVersion": kubelet_version, "operatingSystem": "linux",
                                    "osImage": "Ubuntu 22.04.5 LTS", "kernelVersion": "5.15.0", "containerRuntimeVersion": "kwok://sim"}}}


def nodes_yaml(sim: dict, gpu_override: int | None = None) -> str:
    return "".join("---\n" + yaml.safe_dump(node_manifest(n, g, version(sim)), sort_keys=False) for n, g in node_list(sim, gpu_override))


def api_ip(ips: dict) -> str:
    return ips["services"][API]


def kwok_ip() -> str:
    """The address every KWOK node advertises as InternalIP: the kwok-controller's own fixed one, which serves the fake kubelets'
    logs and metrics. The API server and metrics-server dial it; 127.0.0.1 would be the caller's own container (issue 872)."""
    return addressing.service_ip(KWOK)


def api_sans(ips: dict) -> list[str]:
    return ["DNS:" + d for d in (API, "kubernetes", "kubernetes.default", "kubernetes.default.svc", "localhost")] + \
        ["IP:127.0.0.1", "IP:" + api_ip(ips), "IP:10.0.0.1"]


# ---- PKI and kubeconfigs (openssl; written once, then kept) ----

def _openssl(*args: str, cwd: Path) -> None:
    r = subprocess.run(["openssl", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise K8sError(f"openssl {args[0]} failed: {r.stderr.strip()[-200:]}")


def _cert(d: Path, name: str, subj: str, ca: str = "ca", sans: list[str] | None = None, client: bool = False) -> None:
    _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", f"{name}.key", cwd=d)
    _openssl("req", "-new", "-key", f"{name}.key", "-subj", subj, "-out", f"{name}.csr", cwd=d)
    ext = d / f"{name}.ext"
    ext.write_text("basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nauthorityKeyIdentifier=keyid\n"
                   + ("extendedKeyUsage=clientAuth\n" if client else "extendedKeyUsage=serverAuth,clientAuth\n")
                   + (f"subjectAltName={','.join(sans)}\n" if sans else ""))
    _openssl("x509", "-req", "-in", f"{name}.csr", "-CA", f"{ca}.crt", "-CAkey", f"{ca}.key", "-CAcreateserial",
             "-days", "3650", "-extfile", f"{name}.ext", "-out", f"{name}.crt", cwd=d)
    for junk in (f"{name}.csr", f"{name}.ext"):
        (d / junk).unlink(missing_ok=True)


def _b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


# The API audit log (issue 505, ADR 0042): metadata only (who, verb, object, time, status; never a request or a response body) and only for
# what a node's wire carries: node objects, leases (the heartbeat) and pods. Every other request, secrets included, is not logged at all.
# It goes to the API server's stdout, so the on-demand capture reads it with `docker logs`; Docker bounds the file (see `wire`).
AUDIT_POLICY = """apiVersion: audit.k8s.io/v1
kind: Policy
omitStages: [RequestReceived]
rules:
- level: Metadata
  resources:
  - {group: "", resources: [nodes, nodes/status, pods, pods/status, pods/binding]}
  - {group: coordination.k8s.io, resources: [leases]}
- level: None
"""


def kubeconfig(server: str, user: dict, ca_b64: str | None = None, ca_file: str | None = None, namespace: str | None = None) -> str:
    cluster = {"server": server, **({"certificate-authority-data": ca_b64} if ca_b64 else {"certificate-authority": ca_file})}
    ctx = {"cluster": CONTEXT, "user": user["name"], **({"namespace": namespace} if namespace else {})}
    return yaml.safe_dump({"apiVersion": "v1", "kind": "Config", "current-context": "workbench-k8s",
                           "clusters": [{"name": "workbench-k8s", "cluster": cluster}],
                           "users": [{"name": user["name"], "user": user["auth"]}],
                           "contexts": [{"name": "workbench-k8s", "context": ctx}]}, sort_keys=False)


def prepare(sim: dict, ips: dict, secrets_dir: Path | None = None, state_dir: Path | None = None,
            gpu_override: int | None = None) -> None:
    """Make what the services mount: PKI, tokens and kubeconfigs (kept between runs), the etcd directory and the
    generated node manifests (rewritten every time). Files are ours (mode 600), so the unprivileged containers read them."""
    d, st = secrets_dir or SECRETS_DIR, state_dir or STATE_DIR
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    (st / "etcd").mkdir(parents=True, exist_ok=True)
    ips_stamp = d / "api-ip"
    if not (d / "ca.crt").exists() or not ips_stamp.exists() or ips_stamp.read_text() != api_ip(ips):  # a new address needs a new cert
        for f in d.iterdir():
            shutil.rmtree(f) if f.is_dir() else f.unlink()  # the kubeconfigs fixtures are a directory (issue 767)
        _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "ca.key", cwd=d)
        _openssl("req", "-x509", "-new", "-key", "ca.key", "-subj", "/CN=workbench-k8s-ca", "-days", "3650",
                 "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                 "-addext", "subjectKeyIdentifier=hash", "-out", "ca.crt", cwd=d)  # Python 3.13+ verifies strictly
        _cert(d, "apiserver", "/CN=kube-apiserver", sans=api_sans(ips))
        _cert(d, "admin", "/O=system:masters/CN=workbench-admin", client=True)
        _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "sa.key", cwd=d)
        _openssl("ec", "-in", "sa.key", "-pubout", "-out", "sa.pub", cwd=d)
        tokens = {"viewer": secrets.token_hex(24), "limited": secrets.token_hex(24)}
        (d / "tokens.csv").write_text("".join(f"{t},{u},{u}-uid\n" for u, t in tokens.items()))
        (d / "tokens.csv").chmod(0o600)
        ca = _b64(d / "ca.crt")
        internal = f"https://{api_ip(ips)}:{API_PORT}"
        (d / "component.kubeconfig").write_text(kubeconfig(
            f"https://{API}:{API_PORT}", {"name": "admin", "auth": {"client-certificate": f"{CONTAINER_PKI}/admin.crt", "client-key": f"{CONTAINER_PKI}/admin.key"}},
            ca_file=f"{CONTAINER_PKI}/ca.crt"))
        (d / "admin.kubeconfig").write_text(kubeconfig(  # for the host (certificates by path)
            f"https://127.0.0.1:{API_PORT}", {"name": "admin", "auth": {"client-certificate": str(d.resolve() / "admin.crt"), "client-key": str(d.resolve() / "admin.key")}},
            ca_file=str(d.resolve() / "ca.crt")))
        (d / "viewer.kubeconfig").write_text(kubeconfig(internal, {"name": "viewer", "auth": {"token": tokens["viewer"]}}, ca_b64=ca))
        (d / "limited.kubeconfig").write_text(kubeconfig(internal, {"name": "limited", "auth": {"token": tokens["limited"]}}, ca_b64=ca, namespace="team-a"))
        ips_stamp.write_text(api_ip(ips))
        for f in d.iterdir():
            f.chmod(0o600)
    for extra in (METRICS_USER, TELEPORT_USER):
        if _token(extra, d) is None:  # a cluster made before this user existed gets it without a new PKI (the API server reads it on its next start)
            with (d / "tokens.csv").open("a") as f:
                f.write(f"{secrets.token_hex(24)},{extra},{extra}-uid\n")
    (d / "audit-policy.yaml").write_text(AUDIT_POLICY)  # always rewritten: a cluster made before this existed gets it on its next start
    (d / "audit-policy.yaml").chmod(0o600)
    kubeconfigs.write(d, f"https://{api_ip(ips)}:{API_PORT}", _b64(d / "ca.crt"), _token("viewer", d) or "")  # the negative and exec-plugin files for the k-login (issue 767)
    (st / "nodes.yaml").write_text(nodes_yaml(sim, gpu_override))
    (st / "50-dra.yaml").write_text(dra.slices_yaml(node_list(sim, gpu_override)))  # the DeviceClass and a ResourceSlice per GPU node (ADR 0076)


# ---- compose wiring ----

def wire(services: dict, sim: dict) -> None:
    """Commands, mounts and ordering for the five services, in place (called by compose.generate)."""
    img = images(sim)
    user = f"{os.getuid()}:{os.getgid()}"
    pki = [f"./secrets/k8s:{CONTAINER_PKI}:ro"]
    kc = f"--kubeconfig={CONTAINER_PKI}/component.kubeconfig"
    flags = {
        "k8s-etcd": ["--name=node0", "--auto-compaction-retention=1", "--quota-backend-bytes=536870912", "--data-dir=/etcd-data",
                     "--listen-client-urls=http://0.0.0.0:2379", "--advertise-client-urls=http://k8s-etcd:2379",
                     "--listen-metrics-urls=http://0.0.0.0:2381",  # /metrics for Prometheus on its own plain port (benchctl/promtargets.py)
                     "--listen-peer-urls=http://127.0.0.1:2380", "--initial-advertise-peer-urls=http://127.0.0.1:2380",
                     "--initial-cluster=node0=http://127.0.0.1:2380"],
        "k8s-apiserver": ["--etcd-servers=http://k8s-etcd:2379", "--etcd-prefix=/registry", "--allow-privileged=true",  # kept on: the team-b privileged-pod fixture (k8s/fixtures/20-signals.yaml) is a security signal and would be rejected (#466)
                          "--authorization-mode=Node,RBAC", "--bind-address=0.0.0.0", f"--secure-port={API_PORT}",
                          f"--tls-cert-file={CONTAINER_PKI}/apiserver.crt", f"--tls-private-key-file={CONTAINER_PKI}/apiserver.key",
                          f"--client-ca-file={CONTAINER_PKI}/ca.crt", f"--token-auth-file={CONTAINER_PKI}/tokens.csv",
                          f"--service-account-key-file={CONTAINER_PKI}/sa.pub", f"--service-account-signing-key-file={CONTAINER_PKI}/sa.key",
                          "--service-account-issuer=https://kubernetes.default.svc.cluster.local",
                          f"--proxy-client-cert-file={CONTAINER_PKI}/admin.crt", f"--proxy-client-key-file={CONTAINER_PKI}/admin.key",
                          f"--audit-policy-file={CONTAINER_PKI}/audit-policy.yaml", "--audit-log-path=-", "--audit-log-format=json",
                          # dial a node's InternalIP first, as kubeadm does; the default (Hostname first) depends on the Hostname
                          # address resolving. KWOK asks for no client certificate (v0.8.0 server.go: ServeTLS without ClientAuth);
                          # the certificate is passed as on a real cluster, where the kubelet does ask (issue 872)
                          "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
                          f"--kubelet-client-certificate={CONTAINER_PKI}/admin.crt", f"--kubelet-client-key={CONTAINER_PKI}/admin.key"],
        "k8s-controller-manager": [kc, "--authorization-always-allow-paths=/healthz,/readyz,/livez,/metrics", "--bind-address=0.0.0.0",
                                   "--secure-port=10257", f"--root-ca-file={CONTAINER_PKI}/ca.crt",
                                   f"--service-account-private-key-file={CONTAINER_PKI}/sa.key", "--use-service-account-credentials=true",
                                   "--node-monitor-period=5s", "--node-monitor-grace-period=20s"],
        "k8s-scheduler": [kc, "--authorization-always-allow-paths=/healthz,/readyz,/livez,/metrics", "--bind-address=0.0.0.0", "--secure-port=10259"],
        # --node-ip: the address every fake node advertises; kwokctl v0.8.0 passes the controller's own (issue 872).
        # --enable-crds: without Stage KWOK reads no stages and exits ("no stages found"); the others serve logs and metrics
        KWOK: [kc, "--manage-nodes-with-annotation-selector=kwok.x-k8s.io/node=fake", f"--node-ip={kwok_ip()}",
               f"--node-name={KWOK}", f"--node-port={KWOK_PORT}", f"--server-address=0.0.0.0:{KWOK_PORT}",
               "--node-lease-duration-seconds=40", f"--enable-crds={','.join(KWOK_CRDS)}",
                             f"--tls-cert-file={CONTAINER_PKI}/admin.crt", f"--tls-private-key-file={CONTAINER_PKI}/admin.key"],
        # kwokctl v0.8.0's container-mode arguments (pkg/kwokctl/components/metrics_server.go), with the upstream manifest's
        # --cert-dir=/tmp (a self-signed serving certificate on a tmpfs) instead of the admin certificate. --kubelet-insecure-tls:
        # KWOK serves the admin certificate, which names no node address; the aggregator does not verify this one either
        # (insecureSkipTLSVerify in the APIService, as upstream)
        METRICS_SERVER: [kc, f"--authentication-kubeconfig={CONTAINER_PKI}/component.kubeconfig",
                         f"--authorization-kubeconfig={CONTAINER_PKI}/component.kubeconfig",
                         "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname", "--kubelet-use-node-status-port",
                         "--kubelet-insecure-tls", "--metric-resolution=15s", "--cert-dir=/tmp", "--bind-address=0.0.0.0",
                         f"--secure-port={METRICS_SERVER_PORT}"],
    }
    for name in SERVICES:
        if name not in services:
            continue
        svc = services[name]
        svc.pop("build", None)
        svc.update({"image": img[name], "user": user, "entrypoint": [ENTRYPOINT[name]],
                    "command": flags[name]})
        svc["volumes"] = ["./k8s/etcd:/etcd-data"] if name == "k8s-etcd" else list(pki)
        if name == KWOK:  # the canned kubelet log files (k8s/kwok/logs.yaml names them); compose runs from compose/generated
            svc["volumes"].append(f"../../{SRC}/kwok/logs:{KWOK_LOGS}:ro")
        if name == METRICS_SERVER:  # its self-signed serving certificate, as the upstream manifest's emptyDir; counted in the cap
            svc["tmpfs"] = ["/tmp:size=4m,mode=1777"]
        if name != "k8s-etcd":
            svc["depends_on"] = ["k8s-etcd"] if name == API else [API]
    if API in services:
        services[API]["ports"] = [f"127.0.0.1:{API_PORT}:{API_PORT}"]  # the host's fallback; clients use the fixed IP
        services[API]["logging"] = {"driver": "json-file", "options": {"max-size": "5m", "max-file": "2"}}  # the audit log goes to stdout: Docker keeps at most 10 MB of it


# ---- running the cluster ----

def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


def kubectl(sim: dict, *args: str, kubeconfig_name: str = "component.kubeconfig", runner: Callable = _run,
            mounts: list[tuple[Path, str]] | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    """kubectl from the official image on the cluster network, as the admin (default). Mounts: (host path, container path)."""
    cmd = ["docker", "run", "--rm", "--network", NETWORK, "--user", f"{os.getuid()}:{os.getgid()}", "--memory", "128m",
           "-v", f"{SECRETS_DIR.resolve()}:{CONTAINER_PKI}:ro"]
    for host, ctr in mounts or []:
        cmd += ["-v", f"{host.resolve()}:{ctr}:ro"]
    if stdin is not None:
        cmd.append("-i")
    cmd += [kubectl_image(sim), f"--kubeconfig={CONTAINER_PKI}/{kubeconfig_name}", *args]
    return runner(cmd, input=stdin) if stdin is not None else runner(cmd)


def api_ready(ips: dict, timeout: float = 90, sleep: Callable[[float], None] = time.sleep) -> bool:
    """True when /readyz answers ok (checked from the host with the admin certificate, no Docker needed)."""
    ctx = ssl.create_default_context(cafile=str(SECRETS_DIR / "ca.crt"))
    ctx.load_cert_chain(SECRETS_DIR / "admin.crt", SECRETS_DIR / "admin.key")
    ctx.check_hostname = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"https://{api_ip(ips)}:{API_PORT}/readyz", context=ctx, timeout=3) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        sleep(1)
    return False


def _token(user: str, d: Path | None = None) -> str | None:
    """`user`'s token from the file the API server reads (the page uses only read-only users, never the admin)."""
    try:
        for line in ((d or SECRETS_DIR) / "tokens.csv").read_text().splitlines():
            tok, _, rest = line.partition(",")
            if rest.startswith(f"{user},"):
                return tok
    except OSError:
        pass
    return None


def teleport_kubeconfig(ips: dict, d: Path | None = None) -> str:
    """The static kubeconfig Teleport's Kubernetes Service reads: the `teleport` token user (impersonation only) against the API server's
    fixed address, the CA embedded, so it holds no host path. Written by `teleport.prepare` into the Teleport secrets folder."""
    d = d or SECRETS_DIR
    tok = _token(TELEPORT_USER, d)
    if tok is None:
        raise K8sError("no token for the teleport user: run `benchctl up` with the k8s part so the credentials are made")
    return kubeconfig(f"https://{api_ip(ips)}:{API_PORT}", {"name": TELEPORT_USER, "auth": {"token": tok}}, ca_b64=_b64(d / "ca.crt"))


def viewer_token() -> str | None:
    return _token("viewer")


def metrics_token(d: Path | None = None) -> str | None:
    return _token(METRICS_USER, d)


def api_up(timeout: float = 1.5) -> bool:
    """Whether the API server answers /readyz on the published localhost port (anonymous: the default RBAC allows it; nothing is sent)."""
    ctx = ssl.create_default_context(cafile=str(SECRETS_DIR / "ca.crt")) if (SECRETS_DIR / "ca.crt").exists() else None
    if ctx is None:
        return False
    ctx.check_hostname = False  # the certificate names the cluster addresses; 127.0.0.1 is the published fallback
    try:
        with urllib.request.urlopen(f"https://127.0.0.1:{API_PORT}/readyz", context=ctx, timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def api_json(path: str, timeout: float = 2.0, tok: str | None = None, raw: bool = False):
    """GET one API path on the published localhost port as `viewer` (or `tok`); None when the API or the token is not there."""
    tok = tok or viewer_token()
    if not tok or not (SECRETS_DIR / "ca.crt").exists():
        return None
    ctx = ssl.create_default_context(cafile=str(SECRETS_DIR / "ca.crt"))
    ctx.check_hostname = False  # the certificate names the cluster addresses, and 127.0.0.1 is the published fallback
    req = urllib.request.Request(f"https://127.0.0.1:{API_PORT}{path}", headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            return r.read().decode() if raw else json.load(r)
    except (OSError, ValueError):
        return None


def api_requests() -> int | None:
    """Every request the API server has served since it started (`apiserver_request_total`, all callers: kubectl, the
    scheduler, the controllers, KWOK), read as the `metrics` user, who may read /metrics and nothing else. None when it cannot be read."""
    text = api_json("/metrics", tok=metrics_token(), raw=True)
    if not isinstance(text, str):
        return None
    name = "apiserver_request_total"
    vals = [float(ln.rsplit(" ", 1)[1]) for ln in text.splitlines() if ln.startswith(name) and ln[len(name):len(name) + 1] in (" ", "{")]
    return int(sum(vals)) if vals else None


def node_rows(sim: dict, fetch: Callable = api_json) -> list[dict]:
    """The Kubernetes nodes as the page's node cards: `[{name, kind: "k8s", gpus, state, label}]`, state from the live API
    (Ready, NotReady, cordoned) or `unknown` when it does not answer. Nodes come from sim.yaml, so a missing one is shown."""
    live = {}
    got = fetch("/api/v1/nodes")
    for item in (got or {}).get("items", []) if isinstance(got, dict) else []:
        conds = {c.get("type"): c.get("status") for c in item.get("status", {}).get("conditions", [])}
        live[item["metadata"]["name"]] = (conds.get("Ready"), bool(item.get("spec", {}).get("unschedulable")))
    rows = []
    for name, g in node_list(sim):
        ready, cordon = live.get(name, (None, False))
        state, label = (("unknown", "No data") if not got else ("down", "Not in the cluster") if name not in live else
                        ("fault", "NotReady") if ready != "True" else ("warn", "Cordoned (no new pods)") if cordon else ("ok", "Ready"))
        rows.append({"name": name, "kind": "k8s", "gpus": int(g.get("gpus", 0)), "state": state, "label": label})
    return rows


def node_addresses() -> dict:
    """The status patch that gives a node the addresses KWOK's node-initialize stage writes (InternalIP from --node-ip, Hostname
    from --node-name) and its kubelet port."""
    return {"status": {"addresses": [{"type": "InternalIP", "address": kwok_ip()}, {"type": "Hostname", "address": KWOK}],
                       "daemonEndpoints": {"kubeletEndpoint": {"Port": KWOK_PORT}}}}


def kwok_crd_names() -> list[str]:
    """The definitions KWOK watches (--enable-crds), by their CRD names: Stage -> stages.kwok.x-k8s.io, Logs -> logs.kwok.x-k8s.io."""
    plural = {"Logs": "logs", "ClusterLogs": "clusterlogs", "Metric": "metrics"}
    return [f"{plural.get(k, k.lower() + 's')}.kwok.x-k8s.io" for k in KWOK_CRDS]


def bootstrap(sim: dict, ips: dict, say: Callable[[str], None] = print, runner: Callable = _run, ready: Callable = api_ready,
              retries: int = 10, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Once the API server answers: the KWOK CRDs, the stages, the nodes, then the fixtures and RBAC. Idempotent."""
    if not ready(ips):
        say("FAILED kubernetes: the API server did not become ready (docker logs workbench-k8s-apiserver-1)")
        return False
    src = [(SRC.resolve(), "/k8s"), (STATE_DIR.resolve(), "/state")]

    def apply(path: str, what: str) -> bool:
        r = kubectl(sim, "apply", "--server-side", "--force-conflicts", "-f", path, runner=runner, mounts=src)
        if r.returncode != 0:
            say(f"FAILED kubernetes {what}: {(r.stderr or r.stdout).strip()[-300:]}")
        return r.returncode == 0

    if not apply("/k8s/kwok/crds.yaml", "KWOK definitions"):
        return False
    r = kubectl(sim, "wait", "--for=condition=established", "--timeout=60s", *[f"crd/{c}" for c in kwok_crd_names()], runner=runner)
    if r.returncode != 0:
        say(f"FAILED kubernetes: the KWOK definitions did not become ready: {r.stderr.strip()[-200:]}")
        return False
    for path, what in (("/k8s/kwok/stage-fast.yaml", "default stages"), ("/k8s/kwok/stage-faults.yaml", "fault stages"), *KWOK_OBJECTS,
                       ("/state/nodes.yaml", "nodes"), ("/k8s/fixtures/00-namespaces.yaml", "namespaces")):
        if not apply(path, what):
            return False
    # The addresses KWOK would give a new node, written through the status subresource: etcd keeps the nodes between `down`
    # and `up` (compose/generated/k8s/etcd), and KWOK's stages keep an existing node's addresses, so a cluster made with
    # --node-ip=127.0.0.1 would keep that address for good (issue 872). Idempotent; one call for every node in the file.
    r = kubectl(sim, "patch", "-f", "/state/nodes.yaml", "--subresource=status", "--type=merge", "-p", json.dumps(node_addresses()),
                runner=runner, mounts=src)
    if r.returncode != 0:
        say(f"FAILED kubernetes node addresses: {(r.stderr or r.stdout).strip()[-300:]}")
        return False
    # the fixtures (workloads, signals, RBAC). A new namespace has no default service account until the controller manager
    # makes one, and a pod is refused until then; wait for them, then apply once (a retry of a half-applied set would meet
    # the ResourceQuota fixture).
    for ns in ("team-a", "team-b"):
        for _ in range(retries):
            if kubectl(sim, "-n", ns, "get", "serviceaccount", "default", runner=runner).returncode == 0:
                break
            sleep(2)
    r = kubectl(sim, "apply", "--server-side", "--force-conflicts", "-f", "/k8s/fixtures", runner=runner, mounts=src)
    if r.returncode != 0:
        say(f"FAILED kubernetes fixtures: {(r.stderr or r.stdout).strip()[-300:]}")
        return False
    if not apply("/state/50-dra.yaml", "DRA devices"):
        return False
    if not bootstrap_kueue(sim, say, runner, src, apply):
        return False
    if not bootstrap_volcano(sim, say, runner, src, apply):
        return False
    # last: until metrics-server (started in the next stage) has scraped every node twice the APIService answers 503, and while
    # an aggregated API is unavailable, discovery calls (`kubectl api-resources`) exit non-zero
    return apply(METRICS_API_FILE, "metrics API")


def bootstrap_kueue(sim: dict, say: Callable[[str], None], runner: Callable, src: list, apply: Callable) -> bool:
    """Kueue without a controller (issue 469): the definitions, the objects, then each status through the status subresource."""
    if not apply("/k8s/kueue/crds.yaml", "Kueue definitions"):
        return False
    r = kubectl(sim, "wait", "--for=condition=established", "--timeout=60s", *[f"crd/{c}" for c in kueue.CRDS], runner=runner)
    if r.returncode != 0:
        say(f"FAILED kubernetes: the Kueue definitions did not become ready: {r.stderr.strip()[-200:]}")
        return False
    if not apply("/k8s/kueue/queues.yaml", "Kueue queues"):
        return False
    for args in kueue.status_calls():
        r = kubectl(sim, *args, runner=runner, mounts=src)
        if r.returncode != 0:
            say(f"FAILED kubernetes Kueue status: {(r.stderr or r.stdout).strip()[-300:]}")
            return False
    return True


def bootstrap_volcano(sim: dict, say: Callable[[str], None], runner: Callable, src: list, apply: Callable) -> bool:
    """Volcano without a controller (issue 765): the definitions, the objects, then each Queue's status through the status subresource."""
    if not apply("/k8s/volcano/crds.yaml", "Volcano definitions"):
        return False
    r = kubectl(sim, "wait", "--for=condition=established", "--timeout=60s", *[f"crd/{c}" for c in volcano.CRDS], runner=runner)
    if r.returncode != 0:
        say(f"FAILED kubernetes: the Volcano definitions did not become ready: {r.stderr.strip()[-200:]}")
        return False
    if not apply("/k8s/volcano/queues.yaml", "Volcano queues"):
        return False
    for args in volcano.status_calls():
        r = kubectl(sim, *args, runner=runner, mounts=src)
        if r.returncode != 0:
            say(f"FAILED kubernetes Volcano status: {(r.stderr or r.stdout).strip()[-300:]}")
            return False
    return True


# ---- scenario steps and expectations (target `k8s`, run by benchctl/scenario.py on the host) ----

FAULTS = ("crashloop", "oom", "imagepull", "jobfail")  # the pod stages in k8s/kwok/stage-faults.yaml (a node has fault-node-down)
STEP_KEYS = ("down", "up", "cordon", "uncordon", "pod", "delete_pod", "scale", "apply", "remove", "drain", "queue", "metrics_server", "usage", "volcano")
_NAME = r"[a-z0-9]([-a-z0-9]*[a-z0-9])?"
SCENARIO_POD = "scenario-pod.yaml"


def _ok_name(x) -> bool:
    return isinstance(x, str) and len(x) <= 63 and re.fullmatch(_NAME, x) is not None


def step_problems(do: dict, nodes: set[str]) -> list[str]:
    """What is wrong with a `k8s` step, without touching the cluster."""
    out = [f"unknown k8s key {k!r}; keys: {', '.join(STEP_KEYS)}" for k in do if k not in STEP_KEYS]
    for key in ("down", "up", "cordon", "uncordon"):
        out += [f"unknown Kubernetes node {n!r}" for n in do.get(key) or [] if n not in nodes]
    if "pod" in do:
        p = do["pod"] if isinstance(do["pod"], dict) else {}
        if not (_ok_name(p.get("namespace")) and _ok_name(p.get("name"))):
            out.append("k8s pod needs a namespace and a name (lower-case letters, digits, '-')")
        if p.get("fault") not in (None, *FAULTS):
            out.append(f"k8s pod fault must be one of {', '.join(FAULTS)}")
        if not isinstance(p.get("gpus", 0), int) or isinstance(p.get("gpus", 0), bool) or p.get("gpus", 0) < 0:
            out.append("k8s pod gpus must be a whole number")
    for key in ("delete_pod", "scale"):
        if key in do and not (isinstance(do[key], dict) and _ok_name(do[key].get("namespace"))
                              and _ok_name(do[key].get("name" if key == "delete_pod" else "deploy"))):
            out.append(f"k8s {key} needs a namespace and a {'name' if key == 'delete_pod' else 'deploy'}")
    if "scale" in do and not (isinstance(do["scale"], dict) and isinstance(do["scale"].get("replicas"), int)):
        out.append("k8s scale needs replicas: a whole number")
    if "drain" in do:
        d = do["drain"] if isinstance(do["drain"], dict) else {}
        if d.get("node") not in nodes:
            out.append(f"k8s drain needs a known Kubernetes node, not {d.get('node')!r}")
        if not isinstance(d.get("timeout", 30), int) or isinstance(d.get("timeout", 30), bool) or not 1 <= d.get("timeout", 30) <= 600:
            out.append("k8s drain timeout is whole seconds, 1 to 600")
        if d.get("selector") is not None and not re.fullmatch(r"[A-Za-z0-9._/-]+(=|!=)[A-Za-z0-9._-]+", str(d["selector"])):
            out.append("k8s drain selector is one label test such as app=inference")
    if "pod" in do and isinstance(do["pod"], dict):
        p = do["pod"]
        if p.get("priority_class") is not None and not _ok_name(p["priority_class"]):
            out.append("k8s pod priority_class must be a PriorityClass name")
        if p.get("node") is not None and p["node"] not in nodes:
            out.append(f"unknown Kubernetes node {p['node']!r}")
    if "queue" in do:
        out += kueue.queue_problems(do["queue"])
    if "volcano" in do:
        out += volcano.step_problems(do["volcano"])
    if "metrics_server" in do and do["metrics_server"] not in ("stop", "start"):
        out.append("k8s metrics_server is stop or start")
    if "usage" in do:
        u = do["usage"] if isinstance(do["usage"], dict) else {}
        if not (_ok_name(u.get("namespace")) and _ok_name(u.get("deploy"))):
            out.append("k8s usage needs a namespace and a deploy (a Deployment name)")
        if not re.fullmatch(r"[0-9]+(m|)", str(u.get("cpu", ""))):
            out.append("k8s usage cpu is CPU per pod such as 400m or 2")
    for path in [*(do.get("apply") or []), *(do.get("remove") or [])]:
        if not (isinstance(path, str) and not path.startswith("/") and ".." not in path.split("/")):
            out.append(f"k8s apply path {path!r} must be relative to the k8s/ directory")
    return out or ([] if do else ["k8s step does nothing"])


def pod_manifest(p: dict) -> str:
    """A pod for the scenario: with `fault`, labelled so the matching KWOK stage gives it the kubelet's failure status."""
    labels = {"app": p["name"], **({"workbench_fault": p["fault"]} if p.get("fault") else {})}
    res = {"limits": {"nvidia.com/gpu": str(p["gpus"])}} if p.get("gpus") else {}
    return yaml.safe_dump({"apiVersion": "v1", "kind": "Pod", "metadata": {"name": p["name"], "namespace": p["namespace"], "labels": labels},
                           "spec": {"restartPolicy": "Never" if p.get("fault") == "jobfail" else "Always",
                                    **({"priorityClassName": p["priority_class"]} if p.get("priority_class") else {}),
                                    **({"nodeSelector": {"kubernetes.io/hostname": p["node"]}} if p.get("node") else {}),
                                    "containers": [{"name": "c", "image": "registry.k8s.io/pause:3.10", **({"resources": res} if res else {})}]}})


def step_args(do: dict) -> list[list[str]]:
    """The kubectl calls (arguments only) a validated step makes, in order. A `pod` step needs `write_pod` first."""
    out: list[list[str]] = []
    out += [["label", "node", n, "workbench_fault=down", "--overwrite"] for n in do.get("down") or []]  # the fault-node-down stage: Ready=False
    out += [["label", "node", n, "workbench_fault-"] for n in do.get("up") or []]                      # node-initialize makes it Ready again
    out += [["cordon", n] for n in do.get("cordon") or []]
    out += [["uncordon", n] for n in do.get("uncordon") or []]
    if "pod" in do:
        out.append(["apply", "-f", f"/state/{SCENARIO_POD}"])
    if "delete_pod" in do:
        out.append(["-n", do["delete_pod"]["namespace"], "delete", "pod", do["delete_pod"]["name"], "--wait=false", "--ignore-not-found"])  # the scheduler may have preempted it already (#835)
    if "scale" in do:
        s = do["scale"]
        out.append(["-n", s["namespace"], "scale", f"deployment/{s['deploy']}", f"--replicas={int(s['replicas'])}"])
    if "usage" in do:  # what metrics-server reports per pod changes with the pod template, so the real HPA controller reacts (issue 764)
        u = do["usage"]
        patch = {"spec": {"template": {"metadata": {"annotations": {"kwok.x-k8s.io/usage-cpu": str(u["cpu"])}}}}}
        out.append(["-n", u["namespace"], "patch", f"deployment/{u['deploy']}", "--type=merge", "-p", json.dumps(patch)])
    out += [["apply", "--server-side", "--force-conflicts", "-f", f"/k8s/{path}"] for path in do.get("apply") or []]
    out += [["delete", "--ignore-not-found", "-f", f"/k8s/{path}"] for path in do.get("remove") or []]
    if "volcano" in do:  # a Queue closes (or a PodGroup goes Pending) and back; no controller, so the status is written
        out += volcano.calls(do["volcano"])
    if "queue" in do:  # a ClusterQueue and its LocalQueues go inactive (or back); no controller, so the status is written
        out += kueue.queue_calls(do["queue"])
    if "drain" in do:  # cordons, then evicts through the eviction API, so a PodDisruptionBudget can refuse; the timeout ends a blocked drain
        d = do["drain"]
        # With a selector only those pods are evicted and there is no --force: `drain --force` would also delete the fixture's bare pods
        # (dra-train, gpu-train, sized) for good, as nothing recreates them (#749).
        out.append(["drain", d["node"], "--ignore-daemonsets", "--delete-emptydir-data",
                    f"--pod-selector={d['selector']}" if d.get("selector") else "--force", f"--timeout={int(d.get('timeout', 30))}s"])
    return out


def host_commands(do: dict) -> list[list[str]]:
    """The Docker calls a step makes before its kubectl calls: `metrics_server: stop` stops the metrics-server container, so the metrics
    API answers 503 (ServiceUnavailable) as when metrics-server is down on a real cluster; `start` brings it back (issue 382)."""
    if do.get("metrics_server") in ("stop", "start"):
        return [["docker", do["metrics_server"], f"workbench-{METRICS_SERVER}-1"]]
    return []


def write_pod(do: dict, state_dir: Path | None = None) -> None:
    if "pod" in do:
        d = state_dir or STATE_DIR
        d.mkdir(parents=True, exist_ok=True)
        (d / SCENARIO_POD).write_text(pod_manifest(do["pod"]))


def current_sim() -> dict:
    """The simulation the running cluster was built from (the copy `up` wrote), else the repository's."""
    for p in (Path("compose/generated/sim.yaml"), Path("sim.yaml")):
        try:
            return yaml.safe_load(p.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
    return {}


def kubectl_cmd(sim: dict, *args: str, kubeconfig_name: str = "component.kubeconfig", stdin: bool = False) -> list[str]:
    """The `docker run` that calls kubectl from the official image on the cluster network (as admin unless a kubeconfig is named)."""
    cmd = ["docker", "run", "--rm", "--network", NETWORK, "--user", f"{os.getuid()}:{os.getgid()}", "--memory", "128m",
           "-v", f"{SECRETS_DIR.resolve()}:{CONTAINER_PKI}:ro", "-v", f"{SRC.resolve()}:/k8s:ro", "-v", f"{STATE_DIR.resolve()}:/state:ro"]
    return cmd + (["-i"] if stdin else []) + [kubectl_image(sim), f"--kubeconfig={CONTAINER_PKI}/{kubeconfig_name}", *args]


def node_ready(node: str, runner: Callable) -> bool | None:
    """True/False for the node's Ready condition, None when it cannot be read."""
    r = runner(kubectl_cmd(current_sim(), "get", "node", node, "-o", r'jsonpath={.status.conditions[?(@.type=="Ready")].status}'))
    return None if r.returncode else r.stdout.strip() == "True"


def pod_reasons(namespace: str, name: str, runner: Callable) -> str:
    """Everything a client would call the pod's reason: phase, status reason, container waiting and terminated reasons."""
    jp = ("jsonpath={.status.phase} {.status.reason} {.status.containerStatuses[*].state.waiting.reason} "
          "{.status.containerStatuses[*].state.terminated.reason} {.status.containerStatuses[*].lastState.terminated.reason}")
    r = runner(kubectl_cmd(current_sim(), "-n", namespace, "get", "pod", name, "-o", jp))
    return "" if r.returncode else r.stdout.strip()


def metrics_api_served(runner: Callable) -> bool:
    """Whether the metrics API answers a node list (as admin): False while metrics-server is stopped (503) or not registered (404)."""
    return runner(kubectl_cmd(current_sim(), "get", "--raw", "/apis/metrics.k8s.io/v1beta1/nodes")).returncode == 0


def pods_on_node(node: str, namespace: str, selector: str, runner: Callable) -> int | None:
    """How many Running pods matching `selector` sit on `node` (None when unreadable)."""
    r = runner(kubectl_cmd(current_sim(), "-n", namespace, "get", "pods", "-l", selector, "--field-selector",
                           f"spec.nodeName={node},status.phase=Running", "-o", "jsonpath={.items[*].metadata.name}"))
    return None if r.returncode else len(r.stdout.split())
