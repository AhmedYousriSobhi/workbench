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
import os
import re
import secrets
import ssl
import subprocess
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

import yaml

from .nodes import node_names

VERSION = "v1.36.1"            # Kubernetes (the control-plane images and kubectl); sim.yaml kubernetes.version overrides
ETCD_IMAGE = "registry.k8s.io/etcd:3.6.10-0"
KWOK_VERSION = "v0.8.0"
KWOK_IMAGE = f"registry.k8s.io/kwok/kwok:{KWOK_VERSION}"
SERVICES = ("k8s-etcd", "k8s-apiserver", "k8s-controller-manager", "k8s-scheduler", "kwok-controller")
API = "k8s-apiserver"
ENTRYPOINT = {"k8s-etcd": "etcd", "k8s-apiserver": "kube-apiserver", "k8s-controller-manager": "kube-controller-manager",
              "k8s-scheduler": "kube-scheduler", "kwok-controller": "kwok"}
API_PORT = 6443
# limits in MB (upper bounds for the planning estimate; measured idle 275 MiB, 310 MiB with 50 nodes and the fixtures)
LIMITS = {"k8s-etcd": 256, "k8s-apiserver": 384, "k8s-controller-manager": 128, "k8s-scheduler": 64, "kwok-controller": 64}
SECRETS_DIR = Path("compose/generated/secrets/k8s")   # PKI, tokens and kubeconfigs (gitignored)
STATE_DIR = Path("compose/generated/k8s")             # etcd data and the manifests generated from sim.yaml
SRC = Path("k8s")
CONTAINER_PKI = "/pki"
NETWORK = "workbench_default"
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
            "k8s-scheduler": f"registry.k8s.io/kube-scheduler:{v}", "kwok-controller": KWOK_IMAGE}


def kubectl_image(sim: dict) -> str:
    return f"registry.k8s.io/kubectl:{version(sim)}"


def node_groups(sim: dict, gpu_override: int | None = None) -> list[dict]:
    """The Kubernetes node groups: sim.yaml `kubernetes.nodes`, else the defaults. `gpu_override` (--k8s-nodes) sets the
    count of the group that has GPUs."""
    groups = [dict(g) for g in ((sim.get("kubernetes") or {}).get("nodes") or DEFAULT_NODES)]
    if gpu_override is not None:
        for g in groups:
            if g.get("gpus"):
                g["count"] = gpu_override
    return groups


def node_list(sim: dict, gpu_override: int | None = None) -> list[tuple[str, dict]]:
    """[(name, group)] in order: kcn001, kcn002, kgpu001, ..."""
    return [(f"{g['prefix']}{i:03d}", g) for g in node_groups(sim, gpu_override) for i in range(1, int(g.get("count", 0)) + 1)]


def problems(sim: dict) -> list[str]:
    out = []
    taken = {n for n, _ in node_names(sim)}
    for name, g in node_list(sim):
        if name in taken:
            out.append(f"Kubernetes node {name} has the name of a Slurm node; change the prefix in sim.yaml kubernetes.nodes")
        if g.get("gpus") and str(g.get("gpu_type", "")).lower() not in GPU_PRODUCT:
            out.append(f"unknown gpu_type {g.get('gpu_type')!r} for {name}; known: {', '.join(GPU_PRODUCT)}")
    return out


def node_manifest(name: str, g: dict) -> dict:
    """A KWOK node: the labels and extended resources a real GPU node advertises (GPU Feature Discovery), no taint."""
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
                         "annotations": {"node.alpha.kubernetes.io/ttl": "0", "kwok.x-k8s.io/node": "fake"}},
            "status": {"capacity": dict(cap), "allocatable": dict(cap),
                       "nodeInfo": {"architecture": "amd64", "kubeletVersion": f"kwok-{KWOK_VERSION}", "operatingSystem": "linux",
                                    "osImage": "Ubuntu 22.04.5 LTS", "kernelVersion": "5.15.0", "containerRuntimeVersion": "kwok://sim"}}}


def nodes_yaml(sim: dict, gpu_override: int | None = None) -> str:
    return "".join("---\n" + yaml.safe_dump(node_manifest(n, g), sort_keys=False) for n, g in node_list(sim, gpu_override))


def api_ip(ips: dict) -> str:
    return ips["services"][API]


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


def kubeconfig(server: str, user: dict, ca_b64: str | None = None, ca_file: str | None = None, namespace: str | None = None) -> str:
    cluster = {"server": server, **({"certificate-authority-data": ca_b64} if ca_b64 else {"certificate-authority": ca_file})}
    ctx = {"cluster": "workbench-k8s", "user": user["name"], **({"namespace": namespace} if namespace else {})}
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
            f.unlink()
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
    (st / "nodes.yaml").write_text(nodes_yaml(sim, gpu_override))


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
                     "--listen-peer-urls=http://127.0.0.1:2380", "--initial-advertise-peer-urls=http://127.0.0.1:2380",
                     "--initial-cluster=node0=http://127.0.0.1:2380"],
        "k8s-apiserver": ["--etcd-servers=http://k8s-etcd:2379", "--etcd-prefix=/registry", "--allow-privileged=true",
                          "--authorization-mode=Node,RBAC", "--bind-address=0.0.0.0", f"--secure-port={API_PORT}",
                          f"--tls-cert-file={CONTAINER_PKI}/apiserver.crt", f"--tls-private-key-file={CONTAINER_PKI}/apiserver.key",
                          f"--client-ca-file={CONTAINER_PKI}/ca.crt", f"--token-auth-file={CONTAINER_PKI}/tokens.csv",
                          f"--service-account-key-file={CONTAINER_PKI}/sa.pub", f"--service-account-signing-key-file={CONTAINER_PKI}/sa.key",
                          "--service-account-issuer=https://kubernetes.default.svc.cluster.local",
                          f"--proxy-client-cert-file={CONTAINER_PKI}/admin.crt", f"--proxy-client-key-file={CONTAINER_PKI}/admin.key"],
        "k8s-controller-manager": [kc, "--authorization-always-allow-paths=/healthz,/readyz,/livez,/metrics", "--bind-address=0.0.0.0",
                                   "--secure-port=10257", f"--root-ca-file={CONTAINER_PKI}/ca.crt",
                                   f"--service-account-private-key-file={CONTAINER_PKI}/sa.key", "--use-service-account-credentials=true",
                                   "--node-monitor-period=5s", "--node-monitor-grace-period=20s"],
        "k8s-scheduler": [kc, "--authorization-always-allow-paths=/healthz,/readyz,/livez,/metrics", "--bind-address=0.0.0.0", "--secure-port=10259"],
        "kwok-controller": [kc, "--manage-nodes-with-annotation-selector=kwok.x-k8s.io/node=fake", "--node-ip=127.0.0.1",
                            "--node-name=kwok-controller", "--node-port=10247", "--server-address=0.0.0.0:10247",
                            "--node-lease-duration-seconds=40", "--enable-crds=Stage",  # without it KWOK reads no Stage objects and exits ("no stages found")
                             f"--tls-cert-file={CONTAINER_PKI}/admin.crt", f"--tls-private-key-file={CONTAINER_PKI}/admin.key"],
    }
    for name in SERVICES:
        if name not in services:
            continue
        svc = services[name]
        svc.pop("build", None)
        svc.update({"image": img[name], "user": user, "entrypoint": [ENTRYPOINT[name]],
                    "command": flags[name]})
        svc["volumes"] = ["./k8s/etcd:/etcd-data"] if name == "k8s-etcd" else list(pki)
        if name != "k8s-etcd":
            svc["depends_on"] = ["k8s-etcd"] if name == API else [API]
    if API in services:
        services[API]["ports"] = [f"127.0.0.1:{API_PORT}:{API_PORT}"]  # the host's fallback; clients use the fixed IP


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
    r = kubectl(sim, "wait", "--for=condition=established", "--timeout=60s", "crd/stages.kwok.x-k8s.io", runner=runner)
    if r.returncode != 0:
        say(f"FAILED kubernetes: the stage definition did not become ready: {r.stderr.strip()[-200:]}")
        return False
    for path, what in (("/k8s/kwok/stage-fast.yaml", "default stages"), ("/k8s/kwok/stage-faults.yaml", "fault stages"),
                       ("/state/nodes.yaml", "nodes"), ("/k8s/fixtures/00-namespaces.yaml", "namespaces")):
        if not apply(path, what):
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
    return r.returncode == 0


# ---- scenario steps and expectations (target `k8s`, run by benchctl/scenario.py on the host) ----

FAULTS = ("crashloop", "oom", "imagepull", "jobfail")  # the pod stages in k8s/kwok/stage-faults.yaml (a node has fault-node-down)
STEP_KEYS = ("down", "up", "cordon", "uncordon", "pod", "delete_pod", "scale", "apply")
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
    for path in do.get("apply") or []:
        if not (isinstance(path, str) and not path.startswith("/") and ".." not in path.split("/")):
            out.append(f"k8s apply path {path!r} must be relative to the k8s/ directory")
    return out or ([] if do else ["k8s step does nothing"])


def pod_manifest(p: dict) -> str:
    """A pod for the scenario: with `fault`, labelled so the matching KWOK stage gives it the kubelet's failure status."""
    labels = {"app": p["name"], **({"workbench_fault": p["fault"]} if p.get("fault") else {})}
    res = {"limits": {"nvidia.com/gpu": str(p["gpus"])}} if p.get("gpus") else {}
    return yaml.safe_dump({"apiVersion": "v1", "kind": "Pod", "metadata": {"name": p["name"], "namespace": p["namespace"], "labels": labels},
                           "spec": {"restartPolicy": "Never" if p.get("fault") == "jobfail" else "Always",
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
        out.append(["-n", do["delete_pod"]["namespace"], "delete", "pod", do["delete_pod"]["name"], "--wait=false"])
    if "scale" in do:
        s = do["scale"]
        out.append(["-n", s["namespace"], "scale", f"deployment/{s['deploy']}", f"--replicas={int(s['replicas'])}"])
    out += [["apply", "--server-side", "--force-conflicts", "-f", f"/k8s/{path}"] for path in do.get("apply") or []]
    return out


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
