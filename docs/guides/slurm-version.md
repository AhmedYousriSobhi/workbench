# Choose the Slurm version

The workbench runs the Slurm release it pins (26.05.5). To test a client against another release, pick one from the list.

```bash
benchctl up --cpu-nodes 2 --slurm-version 25.11.9
benchctl exec slurmctld -- slurmctld -V      # slurm 25.11.9
```

Or in `sim.yaml` (the flag wins): `slurm: {version: 25.11.9}`. The choice is saved with the rest of the selection, so `benchctl up` again starts the same release.

| Version | Note |
|---|---|
| 26.05.5 | the default pin |
| 26.05.6 | the newest 26.05 patch |
| 25.11.9 | the newest of the previous release |
| 25.05.9 | the newest of the one before |

- **The first start of a release compiles it** (a few minutes: 25.11.9 took 3 min 38 s in all, 141 s of it compiling, at 4 build jobs on a busy 16-core machine; its node image is 700 MB beside the pinned one's 737 MB). The images are tagged per release (`workbench/node-base:dev-25.11.9`, `slurm-ctl`, `login`) and kept, so going back and forth only rebuilds once. The pinned release keeps the plain `:dev` tags.
- **A release not in the list is refused** with one message. Each listed release has its SHA256 in `benchctl/slurmver.py`, computed from SchedMD's tarball (the download server publishes none, ADR 0002, ADR 0079). Adding a release outside the list is not supported yet.
- **Memory** does not change: the same daemons, one release at a time.
- **Changing the release of a running cluster** means `benchctl up --slurm-version X` again, which recreates the Slurm containers (running jobs end). Upgrading in place while a job runs is the scenario `slurm-upgrade-mid-job` (below).

`./scripts/verify.sh --unit slurmver --clean` checks the daemons, the tools and a job on 25.11.9.

## Upgrade in place under a running job

```bash
benchctl up --with accounting --slurm-version 25.11.9
benchctl scenario run slurm-upgrade-mid-job --trace
```

A real job starts on `cn001`; then each part is upgraded to 26.05.5 in SchedMD's order: `slurmdbd`, `slurmctld` and `slurmrestd`, every `slurmd`, last the login node. For each one the step copies that release's `/opt/slurm` out of its image into the running container and restarts only that daemon (the container is never recreated; `slurmdbd` and `slurmctld` are the container's main process, so their container restarts and keeps its disk, a node's `slurmd` is restarted by the node's supervisor). The job and its `slurmstepd` must stay running through every stage. Each step prints `upgraded NAME: old -> new`.

- The release to upgrade to is the `to:` in `scenarios/slurm-upgrade-mid-job.yaml`; copy the file and change it (and the node list) for another pair. Both releases must be in the table, and the older one is the release the cluster was started with. SchedMD supports upgrading from the previous three major releases.
- The first run builds the target release's image if it is missing (a few minutes, kept afterwards). Building competes for memory with the cluster: run it on a quiet machine.
- Needs the accounting part (`slurmdbd`). Host CLI only: the web page has no Docker access.

`./scripts/verify.sh --unit slurmupgrade --clean` checks the versions after each stage and that the job and `slurmstepd` survive.
