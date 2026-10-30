# Roadmap, open decisions and risks

[Architecture index](README.md) · [Project overview](../../README.md)

## 11. Implementation roadmap

| Phase | Deliverable | Exit test |
|---|---|---|
| 0 Baseline | Measure idle + loaded RAM of each current piece (`docker stats`, `smem`, `virsh dommemstat`); fill real numbers into [section 5](04-memory-and-profiles.md) | Budget table replaced with measured values |
| 1 Governor | Host tuning script, slices, governor with dry-run and the ladder, `simctl top` | A synthetic memory hog gets throttled, frozen, then stopped in order, and recovers |
| 2 Slurm | `node-base`, ctld + restd, N slurmd from `sim.yaml`, Prometheus + Grafana | `benchctl scale compute 16` works; app sees the nodes |
| 3 Mocks | bmc-farm, bcm-mock, gpu-exporter-farm, jira-mock + Toxiproxy | App's BMC/BCM/Jira integrations pass against mocks |
| 4 People + chaos | user-sim, ticket-bot, scenario engine, 5 starter scenarios | `gpu-xid-storm` produces tickets end to end |
| 5 Kubernetes | k3d + KWOK, fake GPU nodes, node-fault scenarios | App shows mixed real/fake K8s nodes |
| 6 Fidelity VM | vagrant-libvirt VM + sushy-tools power control | Redfish power-off really stops the VM |
| 7 Web UI + profiles | Web UI (canvas, dashboard, orchestrator, budget manager), profile pre-flight, `jira-real` swap | One click from cold start to a populated, busy cluster |
| 8 macOS + CI | External Mac agent profile; headless scenario runs with assertions | Scenario suite runs unattended |

## 12. Open decisions (they change the build)

1. **How does the app read Slurm?** CLI (needs a login-style node with Slurm client tools) vs `slurmrestd` (HTTP). The sim supports both, but this decides what must be fully faithful.
2. **BCM surface:** CMDaemon REST, `pythoncm`, or the JSON API? Pick the one the app calls so `bcm-mock` stays small.
3. **Jira auth/features needed:** Cloud API tokens and webhooks vs DC; JQL depth; Service Management features. Decides how much `jira-mock` must implement and when `cloud` mode is required.
4. **Real GPUs:** if you ever attach a real GPU node, it becomes an external target like the Mac, outside the 16 GB budget.
5. **Mac access:** which of the three external options is realistic for you.
6. **Teleport access (issue 39):** which access types the app uses (SSH, Kubernetes, web, DB), real Teleport community binary vs mock, auth/cert TTL behaviour, and one agent per node vs one agent in front of sshd. The app must reach the cluster through Teleport, so this is a required component.

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Mocks drift from real APIs | Contract tests; record real responses (Jira Cloud free site, a real BCM/BMC if you have one) as fixtures |
| Governor flaps or overreacts | Hysteresis, min dwell, dry-run tuning, reversible actions first |
| Freeze doesn't free RAM | Ladder pairs freeze with `memory.reclaim` and falls through to stop/save |
| Per-container overhead adds up at 32+ nodes | Shared base image, tiny limits, later multiple-slurmd-per-container |
| Estimates wrong | Phase 0 measurement gate before committing to profiles |
| Real Jira DC starves the host | Exclusive `jira-real` profile; governor treats it as P6 |
