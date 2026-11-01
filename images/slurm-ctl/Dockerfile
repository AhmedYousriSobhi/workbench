# Controller image: same Slurm build as node-base (slurmctld and slurmrestd live in /opt/slurm).
FROM workbench/node-base:dev
# python3 only for alloc_feed.py, which tells the GPU exporter which GPUs running jobs hold (slurmctld runs it).
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*
COPY alloc_feed.py node_watch.py /usr/local/bin/
CMD ["sh", "-c", "python3 /usr/local/bin/alloc_feed.py & python3 /usr/local/bin/node_watch.py & exec slurmctld -D"]
