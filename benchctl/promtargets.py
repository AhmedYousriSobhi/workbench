"""What Prometheus scrapes, generated from the selection (issue 458, ADR 0073).

`benchctl up` writes, under `compose/generated/`, everything Prometheus needs beyond the static `observability/prometheus.yml`:

- `prometheus/targets/sim-nodes.json`: one file_sd target per simulated node, `instance` is the node (the farm answers
  `/metrics?node=NAME`), so `up{job="sim-nodes"}` says which node's exporter does not answer.
- `prometheus/scrape-parts.yml` (read through `scrape_config_files`): one job per selected part that has metrics: Slurm's own
  endpoint on the controller (four jobs, `slurm-jobs` ... `slurm-scheduler`), the real DCGM
  exporters, the real GPU card, the Kubernetes API server, etcd, scheduler, controller manager and KWOK, Teleport. A part that is
  not selected has no job, so Prometheus never lists a target that cannot exist.
- `secrets/prometheus/`: the Kubernetes CA certificate and the token of the `metrics` user, who may read `/metrics` and nothing
  else (k8s/fixtures/45-rbac-metrics.yaml). The admin and viewer tokens never reach Prometheus.

Nothing here is edited by hand; the files are rewritten on every `up`. A change to the scrape jobs needs a reload (`reload_needed`).
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from . import dcgm, hwinventory, k8s, real_gpu, teleport

GENERATED = Path("compose/generated")
DIR_IN_CONTAINER = "/etc/prometheus/generated"
SECRETS_IN_CONTAINER = "/etc/prometheus/secrets"
EXPORTER = "gpu-exporter-farm:9400"
K8S_TOKEN, K8S_CA = "k8s-metrics.token", "k8s-ca.crt"
# Slurm's own endpoint (MetricsType=metrics/openmetrics, benchctl/slurmconf.py) answers on the controller's port, one path per family.
# `jobs-users-accts` is left out: a series per user and account is the cardinality this workbench caps.
SLURM_PATHS = ("jobs", "nodes", "partitions", "scheduler")
SLURM_PORT = 6817
# kube components with a plain /metrics path (authorization-always-allow-paths in benchctl/k8s.py); etcd serves it on its own http port
K8S_JOBS = {"k8s-controller-manager": ("https", "k8s-controller-manager:10257"), "k8s-scheduler": ("https", "k8s-scheduler:10259"),
            "k8s-etcd": ("http", "k8s-etcd:2381"), "kwok-controller": ("https", "kwok-controller:10247")}


def node_targets(inventory: dict, exporter: str = EXPORTER) -> list[dict]:
    """One target per node: the farm is one process, so the node is a query parameter and the `instance` label."""
    return [{"targets": [exporter], "labels": {"job": "sim-nodes", "instance": n["name"], "__param_node": n["name"]}}
            for n in inventory["nodes"]]


def part_jobs(sim: dict, services: set[str], k8s_ready: bool = True) -> list[dict]:
    """The scrape jobs of the selected parts, in a fixed order (so an unchanged selection writes an unchanged file)."""
    jobs: list[dict] = []
    dcgm_targets = [{"targets": [f"{svc}:9400"], "labels": {"instance": n}} for n in dcgm.nodes(sim) if (svc := dcgm.service(sim, n)) in services]
    if dcgm_targets:
        jobs.append({"job_name": "dcgm-exporter", "static_configs": dcgm_targets})
    if real_gpu.SERVICE in services:
        jobs.append({"job_name": "real-gpu", "static_configs": [{"targets": [f"{real_gpu.SERVICE}:9400"], "labels": {"instance": real_gpu.HOSTNAME}}]})
    if "k8s-apiserver" in services and k8s_ready:
        jobs.append({"job_name": "k8s-apiserver", "scheme": "https", "metrics_path": "/metrics",
                     "tls_config": {"ca_file": f"{SECRETS_IN_CONTAINER}/{K8S_CA}", "server_name": "kubernetes"},
                     "authorization": {"credentials_file": f"{SECRETS_IN_CONTAINER}/{K8S_TOKEN}"},
                     "static_configs": [{"targets": [f"k8s-apiserver:{k8s.API_PORT}"]}]})
    for svc, (scheme, target) in K8S_JOBS.items():
        if svc in services:
            job: dict = {"job_name": svc, "static_configs": [{"targets": [target]}]}
            if scheme == "https":  # the component's own serving certificate is not signed by the workbench CA
                job.update({"scheme": "https", "tls_config": {"insecure_skip_verify": True}})
            jobs.append(job)
    if "slurmctld" in services:
        jobs += [{"job_name": f"slurm-{p}", "metrics_path": f"/metrics/{p}", "static_configs": [{"targets": [f"slurmctld:{SLURM_PORT}"]}]}
                 for p in SLURM_PATHS]
    if "teleport" in services:
        jobs.append({"job_name": "teleport", "static_configs": [{"targets": [f"teleport:{teleport.DIAG_PORT}"]}]})
    return jobs


def _write_if_changed(path: Path, text: str, mode: int | None = None) -> bool:
    old = path.read_text() if path.is_file() else None
    if old != text:
        path.write_text(text)
    if mode is not None:
        path.chmod(mode)
    return old != text


def prepare(sim: dict, services: set[str], compute_nodes: int | None = None, out: Path | None = None, k8s_dir: Path | None = None) -> bool:
    """Write the targets, the part jobs and the secrets Prometheus reads. True when the scrape jobs changed (Prometheus needs a
    reload; the per-node targets are re-read by Prometheus by itself)."""
    root = out or GENERATED
    (root / "prometheus" / "targets").mkdir(parents=True, exist_ok=True)
    secrets = root / "secrets" / "prometheus"
    secrets.mkdir(parents=True, exist_ok=True)
    secrets.chmod(0o755)  # Prometheus runs as nobody; this directory holds only the CA certificate and the metrics-only token
    inv = hwinventory.build(sim, compute_nodes)
    _write_if_changed(root / "prometheus" / "targets" / "sim-nodes.json", json.dumps(node_targets(inv), indent=2) + "\n")
    ready = False
    if "k8s-apiserver" in services:
        d = k8s_dir or k8s.SECRETS_DIR
        token, ca = k8s.metrics_token(d), d / "ca.crt"
        if token and ca.is_file():
            _write_if_changed(secrets / K8S_TOKEN, token + "\n", 0o644)
            _write_if_changed(secrets / K8S_CA, ca.read_text(), 0o644)
            ready = True
    text = "# GENERATED by `benchctl up` from the selected parts (benchctl/promtargets.py); do not edit.\n" + yaml.safe_dump(
        {"scrape_configs": part_jobs(sim, services, ready)}, sort_keys=False)
    return _write_if_changed(root / "prometheus" / "scrape-parts.yml", text)
