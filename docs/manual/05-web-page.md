# 5. The web page

One dashboard over everything: governor, cluster, scenarios. It fits one screen (1280 × 800 and up) with no page scrolling.

```bash
./scripts/benchctl start                     # starts it with the cluster; or by hand:
.venv/bin/python -m webui [port]         # default 127.0.0.1:8088; prints http://127.0.0.1:8088/login?token=...
./scripts/benchctl open                      # print the login link again
```

Open the link once: it sets a cookie. The token lives in `compose/generated/secrets/ui_token`; anyone with the link on
this machine can use the page, so keep it private. The page listens on `127.0.0.1` only.

![The dashboard (screenshot from before the redesign: traffic and the list were arranged differently)](img/web-overview.png)

*A healthy cluster: L0 Normal, 4 of 4 services answering, 6 of 6 nodes healthy, "All clear" under Needs attention. The
screenshots in this chapter predate the redesign described below; regenerate them with `scripts/ui_shots.py`.*

## Layout

| Area | Shows |
|---|---|
| **Top strip** | governor level, free memory, healthy services and nodes, the running scenario; then **☰ Menu**, help and the theme switch. The strip is for status only. **☰ Menu** lists the actions in three groups, each with a one-line hint: *Run* (**▶ Scenarios**), *Configure* (**▦ Cluster architecture**: nodes, GPUs per node, optional parts and the plan; **✚ Health checks**) and *Share* (**⇩ Export**). Esc closes it. Pressing the level or memory chip opens the **Governor** panel |
| **Tabs and Needs attention** | the tabs *Nodes* and *Connections*, and one slim bar next to them with every item that needs you, or "All clear" |
| **Stage** (Nodes tab) | the **traffic diagram** on the left and the **entity list** on the right |

Three panels open on top of the page from the top strip or its **☰ Menu** and close with **✕ Close**, Esc or a click outside: **Scenarios**,
**Plan** and **Governor**. A node is different: it opens *in place of the list* (below), so the traffic diagram stays in view.

On a phone, and under 1100 px, the traffic diagram and the list stack in one column. The page has a skip link and
landmarks, and honours reduced motion.

![The dashboard on a phone, panels stacked (older layout; today traffic and the list stack in one column)](img/web-phone.png)

## The entity list

A plain list, one card per line, in three groups: *Compute nodes*, *GPU nodes* and *Services*. Each heading counts what
is healthy ("4 of 4 healthy"). A card shows the status in words and an icon (✓ healthy, ▲ warning, ✗ fault, ⏻ off) and
the quick facts:

| Card | Quick facts |
|---|---|
| GPU node | a utilisation meter, the percent, busy GPUs out of total, temperature, BCM state |
| Compute node | temperature, BCM state |
| Service | requests per minute and the total seen |

Healthy cards are quiet; warning, fault and off cards are tinted and bold, so what needs you stands out. Slurm node
states and CPU load are not shown, because the page has no Docker access by design; use `benchctl describe node NAME`.

## Traffic diagram

The **Traffic** panel draws the *Clients* (monitoring app, Prometheus, curl), the *Faults* box (scenarios and buttons)
and the *Cluster*: the four mock services and **every node as a tile** with its state. A round dot travels from the
clients to a service for each real request; a diamond travels from *Faults* to the **node it hits** and a ring flashes on
that tile. Lines light up while a service is being used, and each service shows requests per minute. *Pause animation*
stops the dots. They stop by themselves at governor L3 and under reduced motion, and the live feed runs only while the
page is visible and the Nodes tab is open.

## A node: actions and info

Click a node card. Its panel **replaces the list in place** (it is not a pop-up), so you can watch the traffic diagram
while you act. The panel has two cards and a header with **← All nodes** (Esc does the same), the node's name and its
live state.

- **Actions**: the faults first, **Power off**, **Overheat (92 °C)**, **Fan failure** and **GPU error (XID 79)** (GPU
  nodes only), then **Recover**: **Power on** and **Clear faults**. The result appears under the buttons, the card
  changes state and a diamond shows the injection. Nothing here opens a shell on a node; use
  [`docker exec`](04-nodes-and-shells.md).
- **Info**: how to reach the node (SSH command, key file, BMC login, how to get its IP) and everything known about it
  (BMC state, GPUs busy and idle): the same text as `benchctl describe node NAME`, minus the Docker and Slurm lines
  because this page has no Docker access. The container IP needs Docker, so run `./scripts/benchctl describe node cn001`
  (it prints an `IP:` line).

![The fault actions as they looked on a node card: Power off, Power on, Overheat, Fan failure, Clear faults (older layout; they are now in the node panel's Actions card)](img/web-fault-actions.png)

*The same actions, from before the node panel.*

![cn002 shown as Powered off with BCM DOWN (older layout)](img/web-node-powered-off.png)

*After Power off on cn002: the node is marked "Powered off" in words as well as colour, BCM shows DOWN, and the strip counts
5 of 6 nodes healthy. Press Clear faults to undo.*

## Plan: change the workbench

**▦ Cluster architecture** opens a panel with steppers for CPU nodes, GPU nodes and GPUs per node, toggles for the optional parts, grouped by category as in the `--ticketing`, `--hardware` flags (accounting, Jira, BMC, BCM, Teleport, scenarios, load; each shows the memory limit it adds, and the toggles show what is built now), and a
memory bar ("1.7 GB needed of 4.5 GB free"). It shows the change against what runs now ("GPU nodes 2 → 3 (+1)") and the
`benchctl up ...` command for a terminal. Editing the numbers only estimates. **Apply to the running workbench** (press
twice: the second press confirms) asks the governor to rebuild it; new nodes start and nodes you no longer want are
removed. See [choose the workbench](../guides/choose-workbench.md).

## Scenarios

Press **▶ Scenarios**, type in *Filter scenarios* if the list is long, pick a speed (one setting for all), press
**▶ Play**, and watch each step turn ok, skipped or failed. **Stop** aborts. While one runs, the top strip shows it and
pressing that chip reopens the sheet. Steps that need Docker (the `slurm` target: drain and resume) are **skipped on this
page**, because the page has no Docker access by design; run those with `./scripts/benchctl scenario run NAME`.

![Run panel showing node-down, step 2 of 6, with each step marked ok or pending and a Stop button (older layout)](img/web-scenario-running.png)

*A scenario playing (node-down, step 2 of 6). Stop aborts.*

## Governor

Press the level chip (for example **✓ L0 Normal**) or the memory chip. The panel shows the level and what it means, free
memory and pressure with two-minute trends, and the governor's log (*Governor activity*, newest first). If the governor
is offline, or memory is tight (L2 and up), the same is also listed under *Needs attention*.

## Connections

The **Connections** tab lists every part (login node, nodes, BMC, BCM, Jira, Slurm REST, GPU exporter, Prometheus,
Grafana, this page) with its address, a command you can copy, and the **file that holds its credential**. The page shows
paths, never secrets.

## One message per condition

If the cluster is down the page shows one notice and one *Needs attention* entry, hides data that cannot exist (the entity
list) and names the recovery command, which never needs `sudo`. If the governor is down it says so the same way.

![The dashboard with the stack stopped (older layout)](img/web-stack-down.png)

*The cluster is stopped: one notice with the command to start it, services shown offline with dashed outlines, and no node
list. The memory figures are the host's, taken at the time of the screenshot.*

## Help inside the page

**? Help** in the header opens this manual (and the guides, the architecture notes and the decisions) as linked pages at
`/help/`: a sidebar to jump between pages, a search box, and **← Dashboard** to go back. It needs the same login as the
page. Start with [Which command do I use?](11-which-command.md) when the commands are new.

![The help pages: the manual's sidebar on the left and the "Which command do I use?" table on the right](img/web-help.png)

## Limits

- The Profile and Budget manager (edit `sim.yaml` in the page) is **not built**; edit the file ([chapter 8](08-configuration.md)).
- Backend memory stays under 50 MB. Starlette and one static page, no framework, no CDN.
- Browser debugging for developers: `.venv/bin/python scripts/ui_shots.py` ([reference](10-reference.md)).

## Health checks: what drains a node

**☰ Menu → Configure → ✚ Health checks** opens the list of checks every node's health check runs, in run order. Drag a block by its handle to
reorder it (the ↑ ↓ buttons do the same from the keyboard), tick it to switch the check on or off, edit a parameter and
leave the field to save it, ✕ removes it. To add one, drag a template (`metric-above`, `metric-below`) into the list or press
it, then name it and fill in its parameters. Each change is checked before it is saved (syntax, parameters, a run on healthy
sample metrics); a refusal shows one line and the list returns to what was saved. Nodes use the change within 30 s, with no
rebuild. The page adds checks from templates only; your own script goes in with `benchctl add check NAME --script FILE`. See
[NHC checks](../guides/nhc-checks.md).

## Export: a profile file for the monitoring client

**☰ Menu → Share → ⇩ Export** downloads `bench-profile.json` for the cluster on disk (the workbench last built, else the
profile `up` started); tick *One BMC target per node* to include the BMC farm. The file never holds secrets, so after a
successful download the sheet lists what to set by hand: the SSH key (`benchctl ssh-key install`), the Grafana token
(`compose/generated/secrets/grafana_viewer_token`), the BMC password if asked for, and that Jira needs no token. It also
warns when Slurm accounting is missing (`sacct` would fail). See [export a profile](../guides/export-profile.md).

**Power off and Slurm.** Power off sets the BMC and BCM state, makes the GPU exporter stop publishing the node (its GPU series vanish from Grafana), and
asks the governor to **freeze the node's container**. Its `slurmd` then stops answering, exactly as on a dead host, and
`slurmctld` notices by itself: `sinfo` shows the node `down*` (reason *Not responding*) after about 30 s (`SlurmdTimeout=15`, `MessageTimeout=5`).
Power on thaws it, and with `ReturnToService=1` Slurm returns it on its own after its next answer. This needs the governor
started with `--workdir` (the launcher does) and level L0-L1; otherwise the page says the node was not frozen and names
`benchctl set node NAME off`. The fault buttons include **InfiniBand port down** and **NIC flapping**; the node card shows them as a warning and the health check drains the node. The node panel also has a **Workload** card: *Run 1 CPU, 5 min* (GPU nodes: 1 GPU or all GPUs, 5 or 30 min) submits a job on that node through the governor (`benchctl submit`), so `squeue` lists it, the node shows allocated and a GPU job shows its GPUs busy. `benchctl set node NAME down|resume` sets the Slurm state by hand instead.

A node set `down` by hand (`benchctl set node NAME down`) stays down until `resume`; one that went down by not answering returns
by itself (`ReturnToService=1`). A cluster built before this setting keeps the old behaviour until its `slurm.conf` is regenerated
and reloaded: `./scripts/benchctl up`, then `./scripts/benchctl exec slurmctld -- scontrol reconfigure`.

**The traffic diagram is clickable.** A node tile opens that node's panel; a service box (BMC farm, BCM mock, Jira mock,
GPU exporter) or the *Monitoring app* and *Prometheus* chips open the Connections tab with that part's row marked. Tab to
a shape and press Enter or Space for the same.

**BMCs per node.** In **▦ Cluster architecture**, switching *BMC* on shows the nodes that get a BMC address (all by default). Each
BMC is its own small container with its own address (`10.148.x.y:443`) and costs about 32 MB, shown in the memory bar; switch off
the nodes you do not need. A node panel shows its BMC address, or "none for this node". Apply rebuilds as usual and removes
the BMCs you switched off.
