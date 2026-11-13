<p align="center"><img src="../assets/art/guide-nhc-checks-hero.svg" alt="A row of check files feeding a node that gets drained when a check fails" width="720"></p>

# NHC checks: add, change and remove what the node health check looks at

**In 30 seconds:** each node runs a health check every 30 s. You can add, change or remove the checks with one command, by editing files or from the web page. Every change is validated first, and nodes pick it up within 30 s.

## Do it

```bash
./scripts/benchctl get checks            # the checks, in run order
./scripts/benchctl validate checks       # check everything, change nothing
```

Every node runs a health check (`nhc`) every 30 s, before each job and after it. It drains the node with the reason
`NHC: ...` when a check finds a problem ([client testing](client-testing.md#node-health-check-what-drains-a-node) lists the
four checks that ship). You can change that list three ways. All three use the same files and the same validation.

**The files** are in `images/node-base/nhc.d/`:

| File | Is |
|---|---|
| `checks.conf` | the checks in the order they run, one per line: `NAME on|off [key=value ...]` |
| `NAME.sh` | one check: a shell snippet that prints **one line per problem**, nothing when the node is healthy |
| `templates/*.sh` | checks with parameters you fill in (`metric-above`, `metric-below`) |

A change is refused, with the reason and nothing changed, when a name or parameter is malformed, a file has a syntax error,
a parameter is missing, the check calls `scontrol` or `sudo` (the runner drains, not the check), or it reports a problem on
**healthy sample metrics** or takes 10 s or longer (it would drain every healthy node). Running nodes pick up a change at
their next health check, within 30 s, with no rebuild or restart.

## 1. From the command line

```bash
./scripts/benchctl get checks                       # order, on/off, parameters
./scripts/benchctl add check gpu-hot --template metric-above --set metric=DCGM_FI_DEV_GPU_TEMP --set max=85
./scripts/benchctl set check gpu-hot --set max=90   # change a parameter
./scripts/benchctl set check gpu-xid off            # switch off, keep the file (on to switch back)
./scripts/benchctl set check gpu-hot --position 1   # run it first
./scripts/benchctl remove check gpu-hot             # delete the check and its file
./scripts/benchctl validate checks                  # check everything, change nothing
```

Your own check, from a file (`--position N` also works here):

```bash
cat > /tmp/my-check.sh <<'SH'
# check: the node has fewer than 2 GPU temperature series
n=$(printf '%s\n' "$mine" | grep -c '^DCGM_FI_DEV_GPU_TEMP{')
[ "$expected" -gt 0 ] && [ "$n" -lt 2 ] && add "only $n GPU temps"
return 0
SH
./scripts/benchctl add check few-temps --script /tmp/my-check.sh
```

Inside a check you have `$node`, `$mine` (this node's exporter lines), `$expected` (GPUs), `$expected_ib` (IB ports), `$state`
(a scratch file path for remembering a value between runs), the parameters as `$P_<name>`, and `add "reason"` to report a
problem. Declare parameters with a `# params: name other` comment line so they are required and checked.

## 2. By editing the files

Edit `images/node-base/nhc.d/checks.conf` (reorder lines, change `on`/`off`, add `key=value`) or any `NAME.sh`, then:

```bash
./scripts/benchctl reload checks
```

It validates everything first and delivers only a valid set. A new file must also be listed in `checks.conf`
(`NAME on`), otherwise `validate` tells you. `benchctl up` delivers the files too, and warns instead of delivering when
they are invalid.

## 3. In the web page

Open **☰ Menu → ✚ Health checks** in the top strip. Drag a block by its handle to reorder (or use the ↑ ↓ buttons, which work with
the keyboard), tick a block to switch the check on or off, edit a parameter and leave the field to save it, and ✕ removes a
check. To add one, drag a template from the bottom of the sheet into the list (or press it), name it and fill in its
parameters. A refused change shows one line saying why and the list goes back to what was saved. The page adds checks
from templates only; for a script of your own use the command line or a file. Changes made at the same moment (two tabs, or the page and
`benchctl set check`) are applied one after the other, so none is lost.

## Undo

The files are tracked: `git diff images/node-base/nhc.d` shows what changed and `git checkout images/node-base/nhc.d`
followed by `./scripts/benchctl reload checks` restores the shipped checks.

## Under the hood

<p align="center"><img src="../assets/art/guide-nhc-checks-diagram.svg" alt="Three ways of changing a check go through one validation step to the node files, read within 30 seconds" width="720"></p>

> [!HOOD]
> The runner (`images/node-base/nhc`) reads `checks.conf` and runs each enabled check in its own subshell with a 10 s timeout. A check prints one line per problem and nothing when healthy; it cannot drain the node, exit the runner or stop the other checks. A check that cannot run (for example an unreachable exporter) is skipped: the node is never drained on a guess.
> All three ways in use `benchctl/nhc.py`, which makes the change on a copy, validates the whole set, and writes only when it passes. The files are then synced to `compose/generated/nhc`, which the nodes mount read-only.

Source: `images/node-base/nhc`, `benchctl/nhc.py`, [ADR 0019](../adr/0019-nhc-checks-are-files-managed-three-ways.md), SPEC C-15 and W-16

## Tips

> [!WARNING]
> Never edit `compose/generated/nhc`. Change `images/node-base/nhc.d/` and run `benchctl reload checks`.

## If it goes wrong

| Symptom | Try |
|---|---|
| A change is refused | the message says why; nothing was changed |
| A new file is ignored | list it in `checks.conf` (`NAME on`); `validate checks` tells you |
| A healthy node drains | your check reports a problem on healthy metrics; the validator should have refused it, so open an issue |

## Learn more

- [Client testing: what drains a node](client-testing.md#node-health-check-what-drains-a-node)
- [The web page](../manual/05-web-page.md)
