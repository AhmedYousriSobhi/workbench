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
