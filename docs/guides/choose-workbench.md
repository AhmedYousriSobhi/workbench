# Choose the workbench to build

You do not have to edit `sim.yaml`. Say what you want, see what it costs, and build it only if the machine has room.
Example: 2 CPU nodes, 4 GPU nodes with Jira.

## 1. Plan (changes nothing)

```bash
./scripts/benchctl plan --cpu-nodes 2 --gpu-nodes 4 --with jira
```

It prints the nodes and parts, the memory by item (expected use and the cap), the reserve for the app under test (1 GB until you run `benchctl measure app --match NAME --save`, see [memory](memory.md)) and the total it needs, then one verdict line:

- `fits: needs 2092 MB (1068 MB expected + 1024 MB for the app (assumed, not measured)), 8000 MB free, 5908 MB would remain (floor 1024 MB)`: exit code 0.
- `does not fit: ... short by 273 MB ... Try: drop load (100 MB); drop accounting (360 MB); 5 fewer node(s)`: exit code 1,
  and it says what to remove. When the workbench alone fits and only the app's reserve does not, it names the largest reserve that does (`--app-mb 700 if the app needs less than 1024 MB`); start the app after the workbench.

**Expected** is what each part was measured to use plus 15 percent (never above its cap); **cap** is the container limit the
governor enforces. Admission compares the expected use plus the app's reserve with free memory, so a start that fits is no longer
refused because caps are generous. Services of the workbench that already run are left out of what the plan needs, for the same reason (the free figure no longer contains them). If the app under test already runs, its memory is already out of the free figure too: add
`--app-mb 0` (to `plan`, `up`, `apply` or `start`) or set `WORKBENCH_APP_MB=0`. The numbers and how to re-measure them:
[memory contract](../architecture/04-memory-and-profiles.md); the per-part table is in [how much memory it uses](memory.md).

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
| `--with PART` / `--without PART` | add or remove a part (repeat the flag): `jira`, `bmc`, `bcm`, `accounting`, `teleport`, `scenarios`, `load` |
| `--ticketing jira`, `--hardware bmc,bcm`, `--access teleport`, ... | the same parts by category, see below |
| `--addons` | every optional part (`--without teleport` still removes one) |
| `--login ssh,teleport,both` | the login nodes, one auth mode each (`login`, `login2`, ...; 1 to 8); a Teleport mode turns Teleport on. See [login nodes](../manual/04-nodes-and-shells.md#login-nodes-how-many-and-how-each-one-lets-you-in) |
| `--bmc-groups LIST` | node groups that get a BMC: `slurm-cpu`, `slurm-gpu`, `k8s-cpu`, `k8s-gpu` (default all); each BMC is about 32 MB; `--bmc-nodes` then leaves out single nodes |
| `--bcm-k8s` / `--no-bcm-k8s` | whether BCM manages the Kubernetes nodes (`sim.yaml` `bcm.manage_kubernetes`, default on); off: `cmsh -c "device; list"` and the CMDaemon API show no Kubernetes nodes and no `k8s-cpu`/`k8s-gpu` categories, the cluster itself is unchanged |
| `--k8s-nodes N`, `--k8s-cpu-nodes N` | Kubernetes GPU nodes and CPU nodes (KWOK); need `--with k8s`; without them the counts in `sim.yaml` apply (2 and 2) |
| `--name NAME` | name of the workbench (also the cluster's name in an exported client profile) |
| `-f FILE` | read the selection from a workbench file; flags override it |
| `--save FILE` (plan) | write the selection to a file |

Flags you leave out come from `sim.yaml`. Default parts: `jira`, `bmc`, `bcm`, `accounting`, `scenarios` (accounting, about 360 MB, is what makes `sacct` work). Always present: Slurm, the
login node, Prometheus and Grafana.

## Parts by category

Name the parts by what they are for. Values are comma separated or repeated, `all` takes every item of the category, and Tab completion offers the flags and their items (also after a comma):

| Flag | Items | Turns on |
|---|---|---|
| `--workload` | `slurm` (always on) | Kubernetes is not built yet; asking for it is refused with one message |
| `--accounting` | `slurmdbd` | slurmdbd and MariaDB (`sacct`, job history) |
| `--monitoring` | `prometheus`, `grafana` | always on; accepted so a command can say what it wants |
| `--ticketing` | `jira` | the Jira mock |
| `--hardware` | `bmc`, `bcm` | the BMC farm, the BCM mock |
| `--access` | `teleport` | Teleport auth and proxy |
| `--simulation` | `scenarios`, `load` | the scenario engine, the user and ticket simulators |

```bash
./scripts/benchctl start --ticketing jira --hardware bmc,bcm --access teleport
./scripts/benchctl plan --simulation all --accounting slurmdbd   # what it would cost
./scripts/benchctl start --addons                                 # everything optional
./scripts/benchctl start --addons --without teleport              # everything except one
```

Category flags add to the default parts (`jira`, `bmc`, `bcm`, `accounting`, `scenarios`); `--without` removes. A typo gets a hint: `unknown ticketing item 'jirra'. Did you mean 'jira'?`. A plain `benchctl start` with no flags is the `min` preset, which has none of the optional parts. `start` never reuses the workbench you last built: its first line names what it starts and, if one was saved, what it replaces (`Starting the min size preset. This replaces the workbench last built (2 compute + 3 GPU nodes, parts ...)`). `benchctl start --profile min` is the plain default, spelled out, and `start` with parts builds the default workbench plus those parts.

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

**left nav → ⚙ Cluster architecture** in the top bar of the dashboard opens a panel with the same form (steppers for the counts, toggles for the parts) and a memory bar. Editing the numbers only estimates. **Apply to the
running workbench** asks the governor to rebuild it to the plan: new nodes start, nodes you no longer want are removed,
and Slurm's node list is reloaded. Progress shows in the entity list, the Infrastructure architecture and *Governor activity* (in the Governor panel). It needs the governor
started by `./scripts/benchctl start` (it has the repository path) and calm memory (L0-L1); otherwise the page says why
in one line. **GB needed** on the page is the number admission compares with free memory (expected use, minus what already runs, no app reserve), not the sum of the caps; hover it for the parts. `benchctl plan` shows the same number plus the 4096 MB app reserve, so the CLI figure is higher unless you pass `--app-mb 0`. The page counts the app under test as already running (no 4 GB reserve, `--app-mb 0` in the command it shows and in the rebuild). You can always copy the command shown and run it in a terminal instead.

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
