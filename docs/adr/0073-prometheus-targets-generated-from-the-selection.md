# ADR 0073: Prometheus targets generated from the selection, one per node

Status: accepted by the owner on 2026-11-14 (issue 458, epic 557). Built; the live runs are pending (see below).

## Context
Prometheus scraped the whole GPU exporter farm as one target, so `instance` was the farm, not the node, and `up` could not say which node's exporter was down. Its static file scraped only the farm and the governor, so a real DCGM exporter, the real GPU, the Kubernetes control plane and Teleport were never scraped even when selected. `observability/targets/sim-nodes.json` was a gitignored file nobody generated.

## Decision
- **One target per node.** The farm is one process on one port, so it serves `GET /metrics?node=NAME` (the whole-farm `GET /metrics` is unchanged; an unknown node is 404; a powered-off node answers 503, as a dead host does). The target carries `instance: NAME` and `__param_node: NAME`. `up{job="sim-nodes"}` is now per node, and the `ExporterDown` rule fires for the node, not for the farm.
- **Generated at every `up`** by `benchctl/promtargets.py` into `compose/generated/prometheus/`: `targets/sim-nodes.json` (file_sd, re-read by Prometheus by itself) and `scrape-parts.yml` (read through `scrape_config_files`, so the static `observability/prometheus.yml` stays small). A change to the scrape jobs sends `SIGHUP` to a running Prometheus; no lifecycle endpoint is enabled.
- **One job per selected part, none for a part that is off:** the real DCGM exporters (`dcgm-exporter`, one target per node), `real-gpu`, the Kubernetes API server, etcd (its own plain `--listen-metrics-urls` port 2381), kube-scheduler, kube-controller-manager and KWOK, and Teleport (diagnostics port). The jira-mock job of ADR 0072 stays in the static file.
- **Credentials stay minimal.** The API server job authenticates as the `metrics` user, who may read `/metrics` and nothing else (`k8s/fixtures/45-rbac-metrics.yaml`); only that token and the public CA certificate are copied to `compose/generated/secrets/prometheus/` and mounted read-only. The admin and viewer tokens never reach Prometheus. The scheduler, controller manager and KWOK serve `/metrics` without a token and their own certificates, so those jobs skip certificate verification.
- The whole-farm series keep their `node` and `Hostname` labels, so dashboards and rules that group by `node` are unchanged.

## Not verified yet
The live runs: `./scripts/verify.sh --unit prom_targets` (every node up, one node down alone), and the control-plane and Teleport jobs in `test_k8s_real` and `test_teleport_real`. The KWOK job in particular assumes it serves HTTPS because it is given a certificate; the live test shows whether that is right.

## Consequences
- Six scrapes (one per node) every 15 s instead of one; the farm renders only the asked node, so the work per cycle is about the same.
- Per-node scrapes of the exporter count as requests to the farm, so its request counter grows with the node count.
- A selection change needs a Prometheus reload, which `up` does itself.
