# Controller image: same Slurm build as node-base (slurmctld and slurmrestd live in /opt/slurm).
FROM workbench/node-base:dev
# python3 only for alloc_feed.py, which tells the GPU exporter which GPUs running jobs hold (slurmctld runs it).
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*
COPY alloc_feed.py /usr/local/bin/alloc_feed.py
CMD ["sh", "-c", "python3 /usr/local/bin/alloc_feed.py & exec slurmctld -D"]
