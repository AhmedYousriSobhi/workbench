# Choose the workbench to build

You do not have to edit `sim.yaml`. Say what you want, see what it costs, and build it only if the machine has room.
Example: 2 CPU nodes, 4 GPU nodes with Jira.

## 1. Plan (changes nothing)

```bash
./scripts/benchctl plan --cpu-nodes 2 --gpu-nodes 4 --with jira
```

It prints the nodes and parts, the memory limits by item and the total, then one verdict line:

- `fits: needs 1720 MB, 5340 MB free, 3620 MB would remain (floor 1024 MB)`: exit code 0.
- `does not fit: ... short by 273 MB ... Try: drop load (100 MB); drop accounting (360 MB); 5 fewer node(s)`: exit code 1,
  and it says what to remove.

The total is the sum of the container memory limits plus Prometheus and Grafana: an upper bound, the same figure the
memory governor admits against. Real use is lower.

## 2. Build

```bash
./scripts/benchctl up --cpu-nodes 2 --gpu-nodes 4 --with jira
```

`up` prints the same plan, asks the governor for admission, and starts the nodes and parts. It refuses (and starts
nothing) when the estimate does not fit. Check: `benchctl get nodes`, `benchctl get workbench`.

## Flags

| Flag | Meaning |
|---|---|
| `--cpu-nodes N` / `--gpu-nodes N` | how many of each (0 to 64 and 0 to 16; at least one node) |
| `--gpus-per-node N` | GPUs on each GPU node, 1 to 8 |
| `--with PART` / `--without PART` | add or remove a part (repeat the flag): `jira`, `bmc`, `bcm`, `accounting`, `scenarios`, `load` |
| `--k8s-nodes N` | Kubernetes nodes: not built yet, so any value above 0 is refused |
| `--name NAME` | name of the workbench (also the cluster's name in an exported client profile) |
| `-f FILE` | read the selection from a workbench file; flags override it |
| `--save FILE` (plan) | write the selection to a file |

Flags you leave out come from `sim.yaml`. Default parts: `jira`, `bmc`, `bcm`, `accounting`, `scenarios` (accounting, about 360 MB, is what makes `sacct` work). Always present: Slurm, the
login node, Prometheus and Grafana.

## A workbench file

```yaml
# bench.yaml
name: lab
cpu_nodes: 2
gpu_nodes: 4
gpus_per_node: 8
parts: [jira, bmc, scenarios]
```

```bash
./scripts/benchctl plan -f bench.yaml
./scripts/benchctl apply -f bench.yaml        # same as: up -f bench.yaml
```

An unknown field in the file is an error, so a typo cannot silently build the wrong thing.

## What follows the selection

Slurm (`slurm.conf`, partitions: a partition with no nodes is left out), the BMC farm, the GPU metrics (and how many
GPUs each node shows), `benchctl get nodes`, `describe`, the web page, and `benchctl export profile` all use the nodes
you chose, not the numbers in `sim.yaml`.

## From the web page

**☰ Menu → ▦ Cluster architecture** in the top bar of the dashboard opens a panel with the same form (steppers for the counts, toggles for the parts) and a memory bar. Editing the numbers only estimates. **Apply to the
running workbench** asks the governor to rebuild it to the plan: new nodes start, nodes you no longer want are removed,
and Slurm's node list is reloaded. Progress shows in the entity list, the traffic diagram and *Governor activity* (in the Governor panel). It needs the governor
started by `./scripts/benchctl start` (it has the repository path) and calm memory (L0-L1); otherwise the page says why
in one line. You can always copy the command shown and run it in a terminal instead.

## Going back

- `benchctl up --profile min` returns to a size preset and forgets the workbench (`benchctl get profiles`).
- `benchctl scale N` works on presets only; with a workbench built, run `up` again with the new count.
- `benchctl down` stops everything; the workbench is remembered for `get`, `describe` and `export` until the next `up`.

## If it fails

- **`REFUSED ... does not fit`**: follow the `Try:` hint, or close browsers and the IDE (they use several GB) and plan again.
- **`invalid: ...`**: every problem with the flags is listed at once; fix them all and rerun.
- **`unknown field(s) in the workbench file`**: check the spelling against the example above.

## Add accounting to a cluster that is already running

`benchctl start` uses the `min` profile, which has no accounting, so `sacct` says accounting storage is disabled. You do
not need to stop anything. `start --with accounting` does it and leaves the page running (or starts the page if it is
not up); `up` alone only builds the cluster and never starts the page:

```bash
./scripts/benchctl start --with accounting   # same as the next line, plus the page and the governor
./scripts/benchctl up --with accounting      # starts mariadb and slurmdbd, then restarts slurmctld (about a minute)
./scripts/benchctl exec login -- sacct       # no longer "accounting storage is disabled"
```

Or open **▦ Cluster architecture** on the page, switch *Accounting* on and press Apply twice. Running jobs on the controller are lost
when it restarts. The node counts stay as they are because a selection takes them from `sim.yaml` unless you pass flags.
