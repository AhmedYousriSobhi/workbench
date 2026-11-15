# ADR 0082: Ansible runs on the controller node

Status: accepted by the owner (2026-11-15, "choose the best recommended path"); issue 389. Not built yet.

## Context
Real DGX-style and BCM-managed clusters have one head or controller node, and the other nodes are changed from it, often with Ansible playbooks. ADR 0034 kept room for this and left it for a later issue. The simulation already has that node: with `controller.enabled`, the `slurmctld` container runs sshd, is the BCM head node and hosts the fake `cmsh` (SPEC C-20, `benchctl/controller.py`). The bcm-mock lists the nodes and their categories, but nothing changes a node by playbook.

## Decision
1. **Where Ansible runs:** on the controller node, the way sites run it. An administrator reaches it with `ssh controller` and runs `ansible-playbook` there. Not on the host, and not in a new container.
2. **What it targets:** the simulated Slurm nodes, over their own sshd (the same path an admin uses). The inventory is generated from `sim.yaml` and written into the controller's own file system, with groups by node category and by scheduler. Never hand-edited.
3. **What a playbook may change:** only what the simulation can change and show: a node's category, a health-check (NHC) parameter, a node-level setting. A playbook must not reach the host, the governor, or the controller itself. Plays that need privilege the node containers do not have (SPEC: no capabilities) fail with Ansible's own message; the simulation does not grant more.
4. **Install:** `ansible-core` goes into `images/node-base`'s single apt layer (ADR 0014 addendum), never a later `RUN apt-get`. Its memory is measured with `benchctl measure` before the controller's budget is raised; the figure goes into the memory table.
5. **First playbook:** set one NHC parameter on all compute nodes, with a live test that checks the parameter on the nodes and the node's health result.
6. **Playbooks live in the repo** (`ansible/playbooks/`), are validated with `ansible-playbook --syntax-check` in the unit tests, and are shown by `docs/guides/`.

## Alternatives rejected
- **Ansible on the host:** testers would need Ansible installed, and the host would hold the nodes' keys. The simulation exists to avoid both.
- **A separate Ansible container:** an extra container and address for something the controller already is.
- **A fake `ansible-playbook`:** ADR 0034's rule is real components, pinned and unmodified, never a re-implementation.

## Consequences
The controller image grows by the cost of `ansible-core` (to be measured; budget decided then). Without `controller.enabled` there is no Ansible, as there is no `cmsh` on a login node today. The client sees nodes change through the same BMC, BCM and Slurm surfaces as before, with no new endpoint.
