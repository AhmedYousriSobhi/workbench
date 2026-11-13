# ADR 0058: Stack keys and build stamps

Status: accepted (issue 576, V-3 and V-4).

## Context
Each isolated live module started from a stopped stack (about 17 cold `down` and `up` cycles) and every `up` ran `docker compose build` for the node base, the changed targets and `gpu-exporter-farm`. Measured, a cold start is 35 to 60 s, so the saving from sharing is real but smaller than the real-Jira module.

## Decision
- **Stack key** (`tests/real/stack_reuse.py`): sha256 of the canonical `sim` content, the profile name, the selection or enabled parts, and `WORKBENCH_TEST_TIMERS`. `helpers.bring_up` stops a running stack whose key differs (a different slurm.conf or selection never meets a running one, issue 535) and reuses one with the same key.
- **`STACK_REUSE = True`** in a module that is isolated only by its configuration; `STACK_GROUP` orders those modules so equal keys run side by side (after the shared modules, before the cold starts). Such a module ends with `release()`, not `stack.down`, and must leave the cluster as it found it. First converted: `test_nhc_real` and `test_slurmresilience_real` (group `timers`). Modules isolated for state (real governors, apply, the stack lifecycle, user mode, `test_real`) keep their cold start.
- **Build stamp** (`benchctl/stack.py`, `compose/generated/build-stamps.json`): per service, a hash of every file in its build context (plus the node base's hash for images built `FROM workbench/node-base`) and the image id. `up` skips the build when both still match, so a changed mock or entrypoint still rebuilds (issue 449). `WORKBENCH_FORCE_BUILD=1` builds everything; a failed build drops the stamp; the output says `images: N unchanged, built ... in S s`. The image id is in the stamp so a stand-in runner in a unit test can never leave a stamp a real `up` would trust.

## Consequences
- Each skipped build saves seconds, each shared stack 35 to 60 s; neither is the main cost (see ADR 0057).
- A stamp file that is missing or unreadable only means the next `up` builds.
