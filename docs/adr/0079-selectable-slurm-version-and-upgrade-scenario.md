# ADR 0079: A selectable Slurm version and a live upgrade scenario

Status: accepted by the owner in conversation (2026-11-14); issue 221, epic 557. Builds on ADR 0002 (Slurm built from source, pinned version and SHA256).

## Context
Operators upgrade live clusters (`slurmdbd`, then `slurmctld`, then `slurmd`) while jobs run, and a client must cope with a cluster on either side of an upgrade. A mock cannot show that; a second real build can. Until now the workbench had one pinned Slurm (26.05.5).

## Decision
1. **The Slurm version is selectable.** `benchctl up --slurm-version X` or `slurm.version: X` in `sim.yaml`; nothing given means the pin (26.05.5), so a default cluster is unchanged. It is saved with the selection like other options.
2. **Only versions in a table are accepted** (`benchctl/slurmver.py`), each with its SHA256, computed from a download of the tarball on `download.schedmd.com/slurm/` on 2026-11-14:

   | Version | SHA256 |
   |---|---|
   | 25.05.9 | `7be633820df896df819ac2e9f2ebd0824de509515e5fb78ba1c19da2453b39ed` |
   | 25.11.9 | `d238138c6c029045f3764b75f142171266992d07f47970e089fae386b1031cdb` |
   | 26.05.5 (the pin) | `4680cafc82b0689865c885eb703441e7ebf764896c40fd03a50f2fcb60664f68` (the same value ADR 0002 pinned) |
   | 26.05.6 | `386a0d624fa7c730cf4dfa9171bf6dc66b6816b5683242c5271879999a54a9a7` |

   Neither the download directory nor the GitHub release page publishes a checksum, so, as in ADR 0002, the value comes from the same origin as the tarball: it guards against corruption, not against a compromised mirror. A version outside the table is refused with one message. How to add a version that is not listed is a later decision (the owner asked to leave it).
3. **One image set per version, built only when asked for.** The pinned version keeps the tags `workbench/node-base:dev`, `slurm-ctl:dev` and `login:dev`. Any other version builds the same three images with the tags `:dev-<version>`, through `docker compose build --build-arg SLURM_VERSION=... --build-arg SLURM_SHA256=...` (the Dockerfiles already take both) and, for the two images built `FROM node-base`, `BASE_TAG`. The build-stamp hash includes the version, so switching versions rebuilds exactly once and each version's image stays cached. Cost: one more image set on disk per version used (the Slurm tree and the compile step; measured when built), no extra container at run time. The first start of a new version compiles Slurm (a few minutes with a cold ccache; measured below).
4. **The upgrade scenario `slurm-upgrade-mid-job`** (a later commit of this issue): the cluster starts on the `from` version with a job running; for each daemon in SchedMD's order (`slurmdbd`, `slurmctld`, then each `slurmd`) the scenario copies the `to` version's Slurm tree out of that version's image into the running container, switches the daemon to it and restarts only that daemon. Containers are never recreated, because that would kill the job. A log line per stage records the daemon versions and the job state.

## What SchedMD says (https://slurm.schedmd.com/upgrades.html, read 2026-11-14)
Order: `slurmdbd`, `slurmctld`, `slurmd` (preferably with `slurmctld`). Direct upgrades are supported from the previous three major releases (so 25.05, 25.11 and 26.05 are all inside one window). "Upgrades will not interrupt running jobs as long as SlurmdTimeout is not reached"; jobs started before keep the old `slurmstepd`. `slurmdbd` must be the same or a higher major release than `slurmctld`. Back up the database after shutting `slurmdbd` down. The page does not say whether jobs survive a `slurmctld` upgrade: the live test shows it, it is not assumed.

## Not covered
cgroup behaviour (ADR 0002); a version outside the table; downgrades; the REST plugin version skew beyond what the live test shows.

## Not verified yet
Everything: no non-default version has been built or started, and the upgrade has not been run.

## Measured (2026-11-14, issue 221)
`workbench/node-base:dev-25.11.9` built from the Dockerfile with `--build-arg SLURM_VERSION=25.11.9 --build-arg SLURM_SHA256=...` (4 build jobs, low priority, cold ccache; 3 min 38 s in all, 141 s of it compiling): it succeeded with the existing dependencies and contains `slurmd` and `slurmctld` that report `slurm 25.11.9`. The image is 700 MB beside 737 MB for the pinned release. The other releases in the table were not built; they share the same configure line, and the live unit builds 25.11.9 only.

## Addendum: how the upgrade scenario swaps a release (issue 221)

Decided with the owner: the scenario target `slurm-upgrade` (`benchctl/slurmupgrade.py`) copies `/opt/slurm` from the target release's image (`docker create` + `docker cp`) into the running container beside the old tree, renames it into place (running processes keep their old files open), runs `ldconfig` and restarts only the daemon. Containers are never recreated, so the job's `slurmstepd` keeps running as in a package upgrade. A missing target image is built by the step itself, from the same checked table. Order: slurmdbd, slurmctld and slurmrestd, nodes, login (SchedMD). The scenario is `slurm-upgrade-mid-job` (SC-44). Rejected: recreating containers from the new image (kills job steps, tests nothing about an in-place upgrade). Not verified live yet.
