# ADR 0019: NHC checks are files, managed from the CLI, by hand and from the page

Status: accepted (issue 170)

## Context
The node health check (C-15) was one shell script with four checks inside it. Adding, changing or removing a check meant
editing bash and rebuilding the node image, nothing validated the edit, and the Web UI could not touch it. The monitoring
client needs varied `NHC:` drain reasons to test against, so the set of checks has to be easy to change, and a broken
check must never reach the nodes.

## Decision
- **One source of truth:** `images/node-base/nhc.d/`. Each check is a file `NAME.sh`; `checks.conf` lists them in run
  order, `NAME on|off [key=value ...]`. It is plain text because the runner is bash on a minimal image (no YAML parser).
  `nhc.d/templates/` holds parameterised templates (`metric-above`, `metric-below`).
- **The runner stays small** (`images/node-base/nhc`): it reads `checks.conf` and runs each enabled check in its own
  subshell with a 10 s timeout. A check prints one line per problem and nothing when healthy; it cannot drain, exit the
  runner or stop the other checks. Parameters arrive as `$P_<key>`; a check that cannot run is skipped, like an unreachable
  exporter (never drain on a guess).
- **One module, three ways in** (`benchctl/nhc.py`):
  1. CLI, in the `VERB RESOURCE NAME` grammar (ADR 0015): `get checks`, `add check`, `set check`, `remove check`,
     `validate checks`, `reload checks`;
  2. editing the files, then `benchctl reload checks` (validate, then deliver);
  3. the page's **Health checks** sheet: drag to reorder, toggle, edit parameters, remove, add from a template, with
     ↑ ↓ buttons as the keyboard path (W-9).
- **Validation gates every change.** The change is made on a copy and written only if the whole set passes: names and
  `key=value` grammar, parameters against the file's `# params:` line, `bash -n`, no `scontrol`/`sudo` in a check (the
  runner drains, not the check), a size limit, and a smoke run of the real runner on healthy sample metrics: a check that
  reports a problem there, or takes 10 s, is refused because it would drain every healthy node. A refusal changes nothing.
- **Delivery without a rebuild.** `sync` copies the validated set to `compose/generated/nhc/`, which every node mounts
  read-only at `/etc/workbench/nhc`; the runner reads it at each run, so a change lands within one health-check
  interval (30 s). Files are replaced in place, never the directory (a bind mount would keep the old one). The image also
  carries a copy at `/usr/local/lib/nhc.d` for a node started without the mount. `benchctl compose`/`up` run the sync; if
  the files are invalid they warn and the nodes keep the last good set.
- **The page may not author scripts.** The page can reorder, toggle, set parameters, remove and add from a template;
  custom shell is added only with `benchctl add check --script` or by editing a file. The values the page and the CLI
  accept are limited to letters, digits and `_ . : , / -`, so nothing a shell or awk could read as code reaches a check.

## Consequences
- The page writes two files in the checkout (the source and the delivered copy) as the user that runs it. That is not
  root, Docker or libvirt access (W-3), but it is a write the page did not have before; it goes through the same
  validation as the CLI and needs the CSRF header like every other page write. No governor round trip is needed because
  a check change does not start or stop containers or use memory, so the governor's admission rules do not apply.
- The shipped checks live in a tracked directory, so a GUI or CLI change shows in `git status`. That is intended:
  `git diff` is the review and `git checkout images/node-base/nhc.d` the reset.
- A custom check runs inside the node container with the container's limits and no extra privilege (the containers
  stay unprivileged); the smoke run executes it on the host once, as the user, like any local script you add.

## Addendum: a vanished real card (issue 709)
The runner skipped every check when the exporter had no series for the node ("never drain on a guess"), so a real card that
disappeared completely (no series for `rgpu001`) was never checked. Now a node whose environment sets `NHC_METRICS_URL`
(its own exporter, ADR 0037) runs its checks even with an empty series set, so `real-gpu-health` sees the missing
`nvidia-smi` card and drains. Unchanged: an unreachable exporter still skips everything, and a simulated node (no
`NHC_METRICS_URL`) with no series is still not judged. The simulated checks on such a node see an empty series set and
`expected` 0, so they report nothing.

## Addendum: evaluation of upstream LBNL NHC (issue 228)
Evaluation only: no code changed, nothing decided. Sources read: the upstream README, the `nhc` driver, `helpers/node-mark-offline`,
`LICENSE`, and the check libraries `lbnl_hw.nhc`, `lbnl_net.nhc`, `lbnl_nv.nhc`, `lanl_nvgpu.nhc` (github.com/mej/nhc, master; latest
release named on the page: 1.4.3). Not read: the second half of the README, `node-mark-online`, the Slurm prolog documentation.

| Aspect | Stand-in runner (`images/node-base/nhc`) | Upstream NHC |
|---|---|---|
| Size and language | 65-line bash runner plus one `.sh` per check | Bash driver plus about 16 `*.nhc` check libraries; needs Bash 4.3+ |
| Licence | ours | BSD 3-clause; fits a public repository, copyright notices must be kept |
| Config format | `checks.conf`: `NAME on\|off key=value`, order = run order, name = file | `nhc.conf`: `target \|\| check_fn args`; targets are globs, regex, node ranges, commands, negation; no on/off flag (comment the line out) |
| Check names | `gpu-count`, `gpu-xid`, `ib-ports`, `nic-flap`, `real-gpu-health`, templates `metric-above/below` | `check_hw_*`, `check_fs_*`, `check_ps_*`, `check_net_*`, `check_nv_healthmon`, `check_nvgpu_smi`, `check_cmd_*`. Not found upstream: a GPU count check, an XID check, a NIC-flap check, a metric-from-exporter check |
| Inputs | the exporter farm's metrics (simulated hardware) | the real node: `/proc`, `/sys/class/infiniband`, `nvidia-smi`, `mcelog`, mounts, processes. In a container there is no IB sysfs or GPU, so the hardware checks would have nothing to read |
| Parameters | `P_<key>` from `key=value` | positional arguments of the check function |
| State | `$state` scratch file between runs (carrier counter) | Not found as a built-in scratch file; `DETACHED_MODE` stores the last result in `$RUNDIR/<name>.status` |
| Timeout | 10 s per check, runner not bounded | a watchdog, 30 s for the whole run (`-t`), 5 s default for command checks |
| Failure behaviour | runs all checks, joins every problem into one reason | stops at the first failure unless `-a`; with `-a` the exit code is the number of failures |
| Drain reason | `NHC: <problem; problem>` | `scontrol update State=DRAIN NodeName=H Reason="NHC: <note>"` by default (leader `NHC:`, `NHC (perm):` with `-p`); keeps a foreign reason it finds instead of replacing it |
| Resume | resumes only a drain whose reason starts `NHC: ` | on success calls `node-mark-online`; how it limits itself to its own marks was not read (README says it only clears notes it set) |
| Prolog/epilog | one script, `periodic`/`prolog`/`epilog` modes; epilog exits 0, prolog exits 1 to requeue | README describes only `HealthCheckProgram`/`HealthCheckInterval` for Slurm; a prolog or epilog recipe was not found in the pages read |
| Memory, CPU | not measured here | not found in the pages read; it forks many subprocesses per run, not measured |
| Testability | `NHC_METRICS_FILE`, `NHC_SCONTROL` hooks, smoke run on healthy metrics | `-e` evaluates one check line; `make test` in the source; no metrics-file hook |
| Management | `benchctl` validates on a copy, writes only if valid, page edits templates | no equivalent; `benchctl/nhc.py` would need a new parser, a new validator (`bash -n` on libraries, a smoke run under the real driver) and a new template model |
| Install | copied in the image | RPM or tarball (`./configure && make install`); no Debian package found in the pages read, so the Ubuntu node image would build it from a pinned, hashed tarball like Slurm (ADR 0002) |

### What a monitoring client sees
Slurm's view is the same in both cases: a drained node with `Reason=NHC: ...` and, for the periodic run, the node returning to idle.
The text after the prefix would change. Today it is our own messages (for example the GPU count and the missing series); with upstream it
would be the library's messages for the checks that can run in a container (memory, filesystems, processes, ping, command checks). Whether
the client parses the text after `NHC:` is not stated in this repository, so how much a change of wording matters is unknown.

### Findings
- Upstream's value is genuine check semantics and wording for host checks. Its hardware checks read what a container does not have, and
  none of the simulated faults (GPU count, XID, IB port down, NIC flap, vanished real card) has an upstream check, so those would stay
  as `check_cmd_*` wrappers or custom `.nhc` files over the exporter. The result would be upstream's driver around our own checks.
- Adoption changes the config format, the parameter model, the first-failure behaviour (needs `-a` to keep joined reasons), and the
  validation in `benchctl/nhc.py`, `tests/` and the page's Health checks sheet; an estimate in days was not made.
- The drain prefix `NHC:` and Slurm's `HealthCheckProgram` integration are compatible with the current scenarios.

### Options (for the owner to choose)
1. **Keep the stand-in** (no cost). The README and `client-testing.md` keep calling it a stand-in.
2. **Add upstream beside it** as an optional check source: run `nhc -a` for host checks (`check_fs_*`, `check_ps_*`, `check_hw_physmem`),
   keep the runner for the simulated checks and the drain. Extra image build, two config files.
3. **Replace the runner with upstream's driver** and port the five checks to `.nhc` files over the exporter; rewrite `benchctl/nhc.py`
   validation. Largest change.

### Questions for the owner
- Does the monitoring client read the text after `NHC:`, or only the prefix and the node state? (unknown from the repository)
- Is genuine upstream wording a goal (SPEC), given the simulated hardware faults have no upstream equivalent?
- Is a built-from-source upstream tarball acceptable in `node-base` (image size and build time were not measured)?

No recommendation to adopt is made. If a recommendation is wanted: option 1 now, revisit option 2 only if the client needs upstream wording
for host checks, because options 2 and 3 do not replace the checks this simulation exists to fail.
