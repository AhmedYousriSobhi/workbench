# Repository layout and migration

[Architecture index](README.md) · [Project overview](../../README.md)

## 9. Repository layout

```
workbench/
├── README.md  SPEC.md  CLAUDE.md
├── sim.yaml                  # single source of truth: nodes, GPUs, users, partitions, profile budgets
├── profiles/                 # min.yaml std.yaml full.yaml jira-real.yaml mac-agent.yaml
├── benchctl/                   # Python (Typer): up/down/scale/scenario/doctor/top
├── governor/                 # Python daemon: signals, ladder, actions, metrics, dry-run
├── systemd/                  # workbench-*.slice, workbench-governor.service, oomd drop-ins
├── images/
│   ├── node-base/            # shared base for all slurmd containers
│   └── slurm-ctl/ ...
├── components/
│   └── bmc-farm/  bcm-mock/  jira-mock/  gpu-exporter-farm/  dcgm-exporter/
├── webui/                    # Starlette + a vanilla-JS page of native ES modules, static/js/ (ADR 0007, 0008)
├── compose/                  # static + generated compose files (cgroup_parent per class)
├── k8s/                      # KWOK node/stage templates and fixtures
├── fidelity-vm/Vagrantfile   # vagrant-libvirt, 2 GB cap, on-demand
├── observability/            # prometheus.yml (generated targets), dashboards, alert rules
├── scenarios/                # *.yaml
├── scripts/                  # validate.sh (static), verify.sh (live gates), impact.py (which live modules a change reaches)
├── tests/                    # hermetic unit tests (stand-ins for docker, systemctl, ...)
│   └── real/                 # live tests; impact.yaml (change -> modules), stack_reuse.py (stack keys, order), verify_times.py (timing)
└── docs/adr/                 # decision records
```

### Verification: how a change is checked (issue 576, ADR 0057, 0058)

```
 git diff origin/dev ──► scripts/impact.py ◄── tests/real/impact.yaml     (an unmatched path selects ALL)
                              │
                              ▼
   verify.sh --changed ──► validate.sh (background) + real-test.sh <selected modules> --failed-first --maxfail=1
   verify.sh --full  [--with-jira] ──► every live module (real Jira only with --with-jira)
                              │
       tests/real/conftest.py │ order: shared stack ► STACK_REUSE (same key, one stack) ► cold starts
                              ▼
        helpers.bring_up: stack key differs? down + up : reuse ──► stack.up: unchanged image? skip the build (build stamp)
                              │
                              ▼
                compose/generated/verify-times.json  +  SLOW line
```

| Gate | Command | Live modules |
|---|---|---|
| Before a PR to `dev` | `verify.sh --changed` | only those the change can reach, per `impact.yaml` |
| Before `dev` to `main`, and nightly | `verify.sh --full --with-jira` (nightly: `--install-nightly`) | all, including the real Jira (about 7 minutes) |

The live modules never run in parallel: the stack uses fixed addresses and ports (ADR 0018) and 16 GB allows one stack. Measured on 2026-11-13: a plain `--full` takes about 14 minutes (22 before: the real Jira alone was 7).

## 10. Migration from what you have

| Existing | Becomes |
|---|---|
| Vagrant compute node | slurmd containers (`node-base`); keep one real VM only as the fidelity node |
| Vagrant Grafana node | Grafana + Prometheus containers in `core` |
| Vagrant K8s nodes | real control plane + KWOK fake nodes (ADR 0034) |
| Jira container | `jira-mock` by default; the existing container becomes the `dc` mode of the `jira-real` profile |
