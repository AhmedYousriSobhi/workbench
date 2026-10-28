# Reuse assessment: legacy VM labs

Review of two earlier labs (`../hgate-testlab`, `../hgate-k8slab`) for what opti-sim can reuse. Based on their READMEs, Vagrantfiles, `bin/` and `provision/` scripts and the Jira lab docs; the Teleport and Azure material was only skimmed.

## Summary

Nothing is reused as-is: both labs are VM-based, which is exactly what opti-sim moves away from. Their value is in the **fixtures, metrics ideas and lessons**, which port directly into containers and scenarios.

| Legacy piece | What it is | Use in opti-sim | Target |
|---|---|---|---|
| k8slab `Vagrantfile`, `provision/*` | 2 libvirt VMs (1 GB each) running k3s server + agent | Replace with k3d/kind (1+1) plus KWOK. Keep the node-ip/flannel-iface and DNS notes only if the fidelity VM needs k3s. | `k8s/` |
| k8slab `bin/fixtures.sh` | Idempotent namespaces and pods: `team-a/gpu-train` (2 GPUs), `team-b/gpu-waiting` (8 GPUs, unschedulable), `badimage`, `crashloop`, `oom`, `web` | Port as k8s fixtures and the `pod-crashloop` / `k8s-*` scenarios. High value, low effort. | `k8s/fixtures/`, `scenarios/` |
| k8slab `bin/fixtures-signals.sh` | Fake GPU Feature Discovery labels, GPU node without GPUs, stalled Deployment, failed Job, Pending PVC, short DaemonSet, full quota, privileged pod | Port as KWOK node templates and extra scenarios. | `k8s/`, `scenarios/` |
| k8slab fake `nvidia.com/gpu: 4` patch | Extended-resource capacity patched on a node | Same trick for KWOK fake GPU nodes. | `k8s/` |
| k8slab `kc-viewer.yaml` | Service account that can only list pods/events in `team-a` | Keep as the "limited user" RBAC fixture. | `k8s/fixtures/` |
| k8slab `bin/up.sh` | Refuses to start under about 3 GB free memory, bounded `timeout` on every step | Direct precursor of the `simctl up` pre-flight and governor admission control (SPEC GV-9). | `simctl/` |
| testlab `provision/cluster-node.sh` | node_exporter plus synthetic `hpc_jobs_running/pending` through the textfile collector, jittered by a timer | Same mechanism as SPEC C-11/C-6. Seed for gpu-exporter-farm and the fake-mount metrics. | `components/gpu-exporter-farm/` |
| testlab `provision/grafana-node.sh` | Prometheus + Grafana, provisioned datasource, `hpc-overview` dashboard, service-account token, no image renderer | Reuse the provisioning layout and dashboard as the first provisioned dashboard. Keep the "no renderer" choice (RAM). | `observability/` |
| testlab `Vagrantfile`, `bin/up.sh` | vagrant-libvirt, `generic/ubuntu2204`, DHCP-only networking, retry loop for a flaky IP detection | Basis for the single on-demand fidelity VM. Keep the retry logic and the "no static IP" finding. | `fidelity-vm/` |
| testlab `jira-dc/` | Real Jira DC 10.3 in Docker, 3-hour test licence, workflow validators that refuse transitions until fields are set, demo users | Becomes the `dc` mode of the `jira-real` profile. The refusal behavior is also a requirement for `jira-mock`: transitions can fail with per-field errors. | `components/jira-mock/`, profile `jira-real` |
| testlab `azure-fake/` | Fake `az` forwarding to a node | Out of scope for now; keep in mind if the app gains Azure tunnels. | none |
| testlab Teleport files | Teleport node and guides | Out of scope. | none |

## Findings that change the design

1. **Jira mock must model refused transitions.** Jira's API does not reveal workflow validators; they only show in the refusal. `jira-mock` needs a configurable "required fields per transition" rule, and the scenario catalog should gain `jira-transition-refused`.
2. **Fidelity VM networking.** The testlab found vagrant-libvirt IP detection fails intermittently and static IPs did not fix it. Put the retry (destroy and recreate, up to 4 attempts) in `fidelity-vm/` from the start.
3. **Guest DNS.** The k8slab needed IPv4-only plain `resolv.conf` or k3s could not install. Apply the same in any VM provisioning.
4. **Memory measurements for Phase 0.** Both labs pin VMs at 0.5-1 GB each; those numbers, plus `docker stats` of the replacements, give a real before/after for the budget table.

## Cautions

- `hgate-testlab/jira-dc/lab.env` contains personal access tokens, and the directory holds a licence file and Atlassian jars. **Do not copy these into this repo.** Generate `lab.env` at runtime and git-ignore it. (Those tokens were displayed in this session's terminal output; rotate them if the lab Jira is reachable beyond localhost.)
- The Jira DC test licence lasts 3 hours; `jira-real` must handle re-seeding.
- Copy ideas and small scripts, not VM images or `.vagrant/` state.

## Suggested follow-ups

1. Port `fixtures.sh` and `fixtures-signals.sh` to YAML manifests under `k8s/fixtures/` (feature branch).
2. Add the textfile-metrics generator and the `hpc-overview` dashboard in the observability phase.
3. Add the refused-transition rule to the `jira-mock` spec in SPEC.md C-5.
