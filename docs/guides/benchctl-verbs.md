# benchctl verbs: what each does, and what it does not

`benchctl VERB [RESOURCE] [NAME]` follows the conventions of tools people already know. This page lists every verb with
its limits, so you do not find them by surprise, and what to use in a script.

## Rules that hold for every verb

| Rule | Why (the tool it follows) |
|---|---|
| Data goes to **stdout**; messages, refusals and errors go to **stderr**. | A pipe (`benchctl get nodes -o json \| jq`) gets only data, and an error still reaches you. (CLI guidelines) |
| Exit code **0** ok, **1** failed or refused, **2** bad usage (unknown verb or flag). | Scripts and CI branch on it. |
| Tables are for people; **`-o json`** is for scripts. Column names, order and widths of a table may change. | `kubectl get -o json` for scripts, `kubectl describe` for people; Slurm's `--json`. |
| A mistyped verb gets a hint (`Did you mean 'down'?`) and exit 2; it is never run for you, and abbreviations (`benchctl do`) are not accepted. | Guessing could run the wrong command; accepting prefixes blocks future verbs. |
| `benchctl VERB --help` works anywhere in the line. | Deprecate by warning, not by breaking. |
| A step that ends shells or removes nodes (`down`, shrinking `scale`) asks first **when you are at a terminal**; `-y/--yes` skips the question and `-n/--dry-run` shows what would happen. A script (no terminal) is never asked, so `benchctl stop` and CI keep working. | Confirm destructive actions for people, stay scriptable; dry run before change. (CLI guidelines, `kubectl --dry-run`) |
| No secret is ever a flag or printed. Tokens live in `compose/generated/secrets/`. | Secrets in flags leak through `ps` and shell history. |

## Verbs

| Verb | Does | Limits and cautions | For scripts |
|---|---|---|---|
| `start [--profile P]` | machine check, venv, governor (as you), cluster, web page; prints the login link | first run builds images (minutes); refuses when the governor says memory is low; default profile `min` | exit 1 when anything fails |
| `stop` | stops the web page, the cluster and the governor **it started** | does not touch a system governor service, images, or `compose/generated/` | |
| `open` | prints the web page link, opens a browser if there is a display | the page must be running (`start` first) | prints the link on stdout |
| `up [--profile P]` | pre-flight, governor admission, ordered start of the cluster alone | safe to repeat (starts only what is missing); default profile `std`, so pass `--profile min` on 16 GB; no web page, no governor | exit 1 when refused or a part fails to start |
| `down [-n] [-y]` | stops and removes the `workbench` project | open shells on nodes end; your other containers are untouched; secrets and generated files stay; asks first in a terminal when containers run | `-n` lists what would stop |
| `scale N [-n] [-y]` | changes the compute node count of a preset | removes nodes when shrinking (their shells and jobs end) and asks first in a terminal; restarts `slurmctld`; growing asks the governor first and is refused at L2 or above | `-n` says which nodes would start or stop |
| `get nodes\|services\|profiles\|scenarios\|workbench` | lists what exists | read-only; `absent` means no container yet, not an error; `-l key=value` / `key!=value` filters (repeat or comma = AND), `--sort-by COLUMN`, `-w` redraws every `--interval` seconds (at least 1: each redraw asks Docker); a bad selector or column exits 2 and lists the valid columns; `get workbench` prints the saved selection (YAML, or `-o json`) and takes no selector; no OR, regex or custom columns | `-o json`, or `-o name` for one name per line (`for n in $(benchctl get nodes -l kind=gpu -o name)`) |
| `plan [selection]` | estimates the memory a workbench needs against free memory; changes nothing | the figure is the sum of container limits plus observability, an upper bound, and free memory is a snapshot (like Terraform, a plan is a prediction: `up` decides again); `--k8s-nodes` above 0 is refused; `--save FILE` writes the selection | exit 0 fits, 1 does not fit or invalid input |
| `up --cpu-nodes N --gpu-nodes N [--with P] [--without P] [-f FILE]` | estimates, asks the governor, builds the selection | cannot be mixed with `--profile`; remembers the workbench only after a successful start; a plain `up --profile` forgets it; `scale` is refused while one is built | exit 1 when refused or a part fails |
| `apply -f FILE` | builds the workbench a file describes (same as `up -f FILE`) | an unknown field in the file is an error; flags override the file; no `--prune` or diff against what runs, it only starts what is missing | exit 1 on refusal |
| `describe node NAME [-o json]` | everything known about one node: container, Slurm state and jobs, GPUs busy/idle, BMC readings, ssh hint | text is for people (like `kubectl describe`): its lines may change, so scripts use `-o json`; a source that is down is one `Note:` line, not an error; the web page's Details shows the same without the Docker and Slurm lines; an unknown node exits 2 with a `Did you mean` hint | `-o json` |
| `exec NODE [-u USER] [-- CMD]` | a shell, or one command, on a node | the node must be running; no terminal is allocated when a command is given; `-u` must be a simulated user (`alice`, `bob`) | command's own stdout; exit code is Docker's |
| `logs NODE [-f] [--tail N]` | the container log | service or node names only (`slurmctld`, `login`, `cn001`); no time filter; `-f` runs until Ctrl-C | |
| `scenario list` / `scenario run NAME [--speed N] [--dry-run]` | lists scenarios; plays one against the running stack | needs the stack up; `--dry-run` only validates and prints the timeline; Kubernetes and ticket-bot steps are skipped; `--speed` compresses waits, not step order | exit 1 when a step or expectation fails |
| `completion install\|remove\|bash\|zsh\|fish` | turns Tab completion on or off for your shell, or prints the script | no sudo; edits one line in `~/.bashrc` or `~/.zshrc` (fish: a file in `~/.config/fish/completions`) and links `~/.local/bin/benchctl`; completes names from files only, so it works with the stack down; `start` asks once in a terminal | |
| `ssh-key install\|remove` | copies the login key to `~/.ssh/workbench_ed25519` (mode 600) or deletes it | never overwrites a file that is not ours; `remove` deletes only our own key | |
| `export profile [-o FILE] [--bmc] [--user U] [--name N] [--format ID]` | writes the workbench as a client profile file | needs the client's format ID once (`.git/export-format`); holds no password, token or key path; Kubernetes and BCM are not exported; writes the file, never sends it | `-o -` prints the JSON on stdout |
| `status` | governor level, then whether the cluster and web page run | text for people; exits 1 when the governor is unreachable (it still prints the cluster lines) | use `get services -o json` for the cluster |
| `check` | machine check before building (OS, RAM, disk, Docker, ports) | read-only; works before the venv exists via `./scripts/check-machine.sh` | exit 1 when not ready |
| `doctor` | host prerequisites and container safety | read-only; exit 1 on any failure | exit code |
| `preflight --profile P` | would this profile fit in free memory right now | a snapshot: free memory changes; the governor decides at `up` | exit 1 when it would not fit |
| `diag [--out F]` | one masked tarball of host, Docker and governor state | masks secrets, but read it before sharing | prints the path |
| `compose --profile P` | writes `compose/generated/docker-compose.yml` | only writes the file; never edit the output by hand | |
| `measure run\|report` | samples memory (read-only), then suggests budgets | `run` takes `--duration` seconds; results are gitignored | |

## How this compares with the tools it follows

| Our verb | Like | Difference to know |
|---|---|---|
| `get` | `kubectl get`, `sinfo -N` | `-l`, `--sort-by`, `-w` and `-o name` like kubectl; `-o` has no wide, jsonpath or custom-columns; the columns are fixed (name, kind, state), unlike `sinfo -o` |
| `describe node` | `kubectl describe node`, `scontrol show node` | merges container, Slurm, GPU and BMC views in one place; Slurm's own `--json` covers only Slurm |
| `exec` / `logs` | `kubectl exec` / `kubectl logs` | wraps `docker exec` / `docker logs`; no `--previous` or container choice |
| `up` / `down` | `docker compose up/down` | admission by the governor first; only the `workbench` project; `down` asks in a terminal and has `--dry-run` |
| `plan` / `apply -f` | `terraform plan` / `apply`, `kubectl apply -f` | declarative file in, no state diff or delete of what is no longer listed; `plan` has no saved-plan file |
| `scenario run --dry-run` | `kubectl --dry-run`, `terraform plan` | validates and prints; it does not simulate the outcome |
| `export profile` | `kubectl config view` | a settings-only file for another tool to import; secrets are left out on purpose |
| `status`, `check` | `kubectl cluster-info`, `scontrol ping` | local checks, not a cluster API call |
| `scenario run` steps for Slurm | `scontrol update` | drain/resume only, never a hardware or `slurm.conf` change (scontrol cannot do that either; it needs `reconfigure`) |

## Sources

- [Command Line Interface Guidelines](https://clig.dev/): stdout and stderr, exit codes, typo suggestions, deprecation warnings, dry runs and confirmation.
- [kubectl conventions](https://kubernetes.io/docs/reference/kubectl/conventions/) and [quick reference](https://kubernetes.io/docs/reference/kubectl/quick-reference/): machine-readable output for scripts; `get`, `describe`, `apply` roles.
- [scontrol](https://slurm.schedmd.com/scontrol.html) and [sinfo](https://slurm.schedmd.com/sinfo.html): `show`/`update` grammar, `--json`, folded host lists, and the advice not to run `sinfo` in a tight loop.
- [terraform plan](https://developer.hashicorp.com/terraform/cli/commands/plan): a plan is a prediction, not a guarantee.
