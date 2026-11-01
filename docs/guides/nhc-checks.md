# NHC checks: add, change and remove what the node health check looks at

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

Open **✚ Health checks** in the top strip. Drag a block by its handle to reorder (or use the ↑ ↓ buttons, which work with
the keyboard), tick a block to switch the check on or off, edit a parameter and leave the field to save it, and ✕ removes a
check. To add one, drag a template from the bottom of the sheet into the list (or press it), name it and fill in its
parameters. A refused change shows one line saying why and the list goes back to what was saved. The page adds checks
from templates only; for a script of your own use the command line or a file.

## Undo

The files are tracked: `git diff images/node-base/nhc.d` shows what changed and `git checkout images/node-base/nhc.d`
followed by `./scripts/benchctl reload checks` restores the shipped checks.
