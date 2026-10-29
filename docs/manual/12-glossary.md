# 12. Glossary

Words used across the manual and the web page.

| Term | Meaning |
|---|---|
| **Profile** | a named size of the cluster (`min`, `std`, `full`) in `profiles/`; sets which parts run and how many nodes |
| **Stack** | everything `up` starts: the Slurm cluster, the mocks, observability, the login node. Docker project name `hpcsim` |
| **Node** | a simulated compute or GPU node: a container running `slurmd` (and `sshd`), not a machine |
| **Login node** | the container you ssh into (127.0.0.1:2222), like a real cluster's front door; from it you reach the nodes |
| **Mock** | a stand-in service with the real one's API: `bcm-mock` (BCM), `jira-mock` (Jira), `bmc-farm` (Redfish BMCs), `gpu-exporter-farm` (DCGM metrics) |
| **Scenario** | a file in `scenarios/` with timed steps (`at`, `target`, `do`) that injects faults |
| **Step** | one line of a scenario: at a time, apply a change to a target |
| **Target** | what a step acts on: `bmc-farm`, `gpu-exporter`, `jira`, `slurm`, `ssh`, `bcm-mock` |
| **Fault** | a deliberate failure: a node powered off, a fan failing, a login node that hangs |
| **Dry run** | `--dry-run`: print a scenario's steps without applying them |
| **Governor** | the host program that watches memory and pauses or stops containers so the machine stays alive |
| **Level (L0 to L5)** | how worried the governor is: L0 normal, L1 warn, L2 throttle, L3 freeze (pause), L4 stop, L5 emergency |
| **Priority class (P0 to P6)** | how important a container is; the governor stops the highest number first |
| **Admission** | the governor's yes or no when `up` or `scale` asks to start more |
| **Preflight** | a check that a profile fits in the memory available now |
| **Rootless mode** | the governor running as you, using only `docker pause` and `stop` (no sudo) |
| **Host key** | the login node's SSH identity; a client pins it on first use and must refuse a changed one |
| **Hop** | `ssh cn001` from the login node to a node |
| **Web page** | the dashboard on 127.0.0.1:8088; it talks only to the governor socket, never to Docker |
| **Token** | the login secret for the web page, in `compose/generated/secrets/ui_token` (printed by `opti open`) |
| **Client** | the desktop monitoring application you are testing against opti-sim |
| **ADR** | architecture decision record in `docs/adr/`: why a design choice was made |
