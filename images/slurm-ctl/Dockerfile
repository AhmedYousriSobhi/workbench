# Controller image: same Slurm build as node-base (slurmctld and slurmrestd live in /opt/slurm).
FROM workbench/node-base:dev
# python3 (for alloc_feed.py, which tells the GPU exporter which GPUs running jobs hold) comes from node-base: an apt step
# here would re-run on every node-base change.
COPY alloc_feed.py node_watch.py /usr/local/bin/
CMD ["sh", "-c", "python3 /usr/local/bin/alloc_feed.py & python3 /usr/local/bin/node_watch.py & exec slurmctld -D"]
