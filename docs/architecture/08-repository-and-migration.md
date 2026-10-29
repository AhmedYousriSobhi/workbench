# Repository layout and migration

[Architecture index](README.md) · [Project overview](../../README.md)

## 9. Repository layout

```
opti-sim/
├── README.md  SPEC.md  CLAUDE.md
├── sim.yaml                  # single source of truth: nodes, GPUs, users, partitions, profile budgets
├── profiles/                 # min.yaml std.yaml full.yaml jira-real.yaml mac-agent.yaml
├── simctl/                   # Python (Typer): up/down/scale/scenario/doctor/top
├── governor/                 # Python daemon: signals, ladder, actions, metrics, dry-run
├── systemd/                  # hpcsim-*.slice, hpcsim-governor.service, oomd drop-ins
├── images/
│   ├── node-base/            # shared base for all slurmd containers
│   └── slurm-ctl/ ...
├── components/
│   ├── bmc-farm/  bcm-mock/  jira-mock/  gpu-exporter-farm/
│   └── user-sim/  ticket-bot/  scenario-engine/
├── webui/                    # Starlette + one vanilla-JS page (ADR 0007, 0008)
├── compose/                  # static + generated compose files (cgroup_parent per class)
├── k8s/                      # k3d config, KWOK node/stage templates
├── fidelity-vm/Vagrantfile   # vagrant-libvirt, 2 GB cap, on-demand
├── observability/            # prometheus.yml (generated targets), dashboards, alert rules
├── scenarios/                # *.yaml
└── docs/adr/                 # decision records
```

## 10. Migration from what you have

| Existing | Becomes |
|---|---|
| Vagrant compute node | slurmd containers (`node-base`); keep one real VM only as the fidelity node |
| Vagrant Grafana node | Grafana + Prometheus containers in `core` |
| Vagrant K8s nodes | k3d/kind (1+1) + KWOK fake nodes |
| Jira container | `jira-mock` by default; the existing container becomes the `dc` mode of the `jira-real` profile |
