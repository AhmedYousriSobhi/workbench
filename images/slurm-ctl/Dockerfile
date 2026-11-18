# Controller image: same Slurm build as node-base (slurmctld and slurmrestd live in /opt/slurm).
# BASE_TAG is dev for the pinned Slurm and dev-<version> for another one (benchctl/slurmver.py, ADR 0079)
ARG BASE_TAG=dev
FROM workbench/node-base:${BASE_TAG}
# python3 (for alloc_feed.py, which tells the GPU exporter which GPUs running jobs hold) comes from node-base: an apt step
# here would re-run on every node-base change.
COPY alloc_feed.py node_watch.py /usr/local/bin/
# Slurm power saving (issue 545): one script, two names (SuspendProgram, ResumeProgram); no RUN step, so no layer to re-run.
COPY --chmod=755 power.sh /usr/local/sbin/power-off
COPY --chmod=755 power.sh /usr/local/sbin/power-on
# Config lab A/B restore (issue 554): puts a saved slurmctld state back before slurmctld starts; does nothing without one.
COPY --chmod=755 restore-state.sh /usr/local/sbin/restore-state
CMD ["sh", "-c", "/usr/local/sbin/restore-state || exit 1; python3 /usr/local/bin/alloc_feed.py & python3 /usr/local/bin/node_watch.py & exec slurmctld -D"]
