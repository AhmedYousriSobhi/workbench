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
- **Changing the release of a running cluster** means `benchctl up --slurm-version X` again, which recreates the Slurm containers (running jobs end). Upgrading in place while a job runs is the scenario `slurm-upgrade-mid-job` (issue 221, next step of the same issue).

`./scripts/verify.sh --unit slurmver --clean` checks the daemons, the tools and a job on 25.11.9.
