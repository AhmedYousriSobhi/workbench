# 5. The web page

One dashboard over everything: governor, cluster, scenarios.

```bash
./scripts/benchctl start                     # starts it with the cluster; or by hand:
.venv/bin/python -m webui [port]         # default 127.0.0.1:8088; prints http://127.0.0.1:8088/login?token=...
./scripts/benchctl open                      # print the login link again
```

Open the link once: it sets a cookie. The token lives in `compose/generated/secrets/ui_token`; anyone with the link on
this machine can use the page, so keep it private. The page listens on `127.0.0.1` only.

![The dashboard: governor on the left, services and the traffic diagram in the middle, needs-attention and scenarios on the right](img/web-overview.png)

*The dashboard with a healthy cluster: L0 Normal, 4 of 4 services answering, 6 of 6 nodes healthy, "All clear" under Needs attention.*

## Layout

| Area | Shows |
|---|---|
| **Top strip** | governor level, free memory, healthy services and nodes, the running scenario, theme switch (auto, light, dark) |
| **Left: governor** | level banner, memory and pressure with two-minute trends |
| **Middle: cluster** | service chips, the traffic diagram, one card per node |
| **Right** | *Needs attention*, the scenario list and the governor's log |

On a phone the panels stack (see the [phone view](img/web-phone.png)). The page has a skip link and landmarks, and honours reduced motion.

## Traffic diagram

A round dot travels from *Clients* to a service for each real request; a diamond travels from *Fault injector* when you
apply a fault. *Pause animation* stops them. They stop by themselves at governor L3 and under reduced motion, and the
live feed runs only while the page is visible.

## Node cards and fault actions

Open *Fault actions* on a node card to **Power off**, **Power on**, **Overheat (92 °C)**, **Fan failure**, **GPU error
(XID 79)** (GPU nodes only) or **Clear faults**. The card changes state and a diamond shows the injection. Nothing here
opens a shell on a node; use [`docker exec`](04-nodes-and-shells.md).

![A node card with its Fault actions expanded: Power off, Power on, Overheat, Fan failure, Clear faults](img/web-fault-actions.png)

*A node card with Fault actions open.*

Press **Details** on a card to see everything known about that node (state of its BMC, GPUs busy and idle, how to `ssh` in): the same text as `benchctl describe node NAME`, minus the Docker and Slurm lines because this page has no Docker access.

![cn002 shown as Powered off with BCM DOWN; the top strip says Nodes 5/6 healthy](img/web-node-powered-off.png)

*After Power off on cn002: the card is marked "Powered off" in words as well as colour, BCM shows DOWN, and the strip counts 5 of 6 nodes healthy. Press Clear faults to undo.*

## Scenarios

In the right panel pick a scenario and a speed, press **Play**, and watch each step turn ok, skipped or failed. **Stop**
aborts. Steps that need Docker (the `slurm` target: drain and resume) are **skipped on this page**, because the page has no
Docker access by design; run those with `./scripts/benchctl scenario run NAME`.

![Run panel showing node-down, step 2 of 6, with each step marked ok or pending and a Stop button](img/web-scenario-running.png)

*A scenario playing (node-down, step 2 of 6). The strip at the top also shows it running; Stop aborts.*

## One message per condition

If the cluster is down the page shows one notice and one *Needs attention* entry, hides data that cannot exist and names
the recovery command, which never needs `sudo`. If the governor is down it says so the same way.

![The dashboard with the stack stopped: one notice in the middle, one entry under Needs attention, services shown offline, no node cards](img/web-stack-down.png)

*The cluster is stopped: one notice with the command to start it, services shown offline with dashed outlines, and no node cards. The memory figures are the host's, taken at the time of the screenshot.*

## Help inside the page

**? Help** in the header opens this manual (and the guides, the architecture notes and the decisions) as linked pages at
`/help/`: a sidebar to jump between pages, a search box, and **← Dashboard** to go back. It needs the same login as the
page. Start with [Which command do I use?](11-which-command.md) when the commands are new.

![The help pages: the manual's sidebar on the left and the "Which command do I use?" table on the right](img/web-help.png)

## Limits

- The Profile and Budget manager (edit `sim.yaml` in the page) is **not built**; edit the file ([chapter 8](08-configuration.md)).
- Backend memory stays under 50 MB. Starlette and one static page, no framework, no CDN.
- Browser debugging for developers: `.venv/bin/python scripts/ui_shots.py` ([reference](10-reference.md)).
