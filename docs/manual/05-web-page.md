# 5. The web page

One dashboard over everything: governor, cluster, scenarios.

```bash
./scripts/opti start                     # starts it with the cluster; or by hand:
.venv/bin/python -m webui [port]         # default 127.0.0.1:8088; prints http://127.0.0.1:8088/login?token=...
./scripts/opti open                      # print the login link again
```

Open the link once: it sets a cookie. The token lives in `compose/generated/secrets/ui_token`; anyone with the link on
this machine can use the page, so keep it private. The page listens on `127.0.0.1` only.

## Layout

| Area | Shows |
|---|---|
| **Top strip** | governor level, free memory, healthy services and nodes, the running scenario, theme switch (auto, light, dark) |
| **Left: governor** | level banner, memory and pressure with two-minute trends |
| **Middle: cluster** | service chips, the traffic diagram, one card per node |
| **Right** | *Needs attention*, the scenario list and the governor's log |

On a phone the panels stack. The page has a skip link and landmarks, and honours reduced motion.

## Traffic diagram

A round dot travels from *Clients* to a service for each real request; a diamond travels from *Fault injector* when you
apply a fault. *Pause animation* stops them. They stop by themselves at governor L3 and under reduced motion, and the
live feed runs only while the page is visible.

## Node cards and fault actions

Open *Fault actions* on a node card to **Power off**, **Power on**, **Overheat (92 °C)**, **Fan failure**, **GPU error
(XID 79)** (GPU nodes only) or **Clear faults**. The card changes state and a diamond shows the injection. Nothing here
opens a shell on a node; use [`docker exec`](04-nodes-and-shells.md).

## Scenarios

In the right panel pick a scenario and a speed, press **Play**, and watch each step turn ok, skipped or failed. **Stop**
aborts. Steps that need Docker (the `slurm` target: drain and resume) are **skipped on this page**, because the page has no
Docker access by design; run those with `./scripts/simctl scenario run NAME`.

## One message per condition

If the cluster is down the page shows one notice and one *Needs attention* entry, hides data that cannot exist and names
the recovery command, which never needs `sudo`. If the governor is down it says so the same way.

## Limits

- The Profile and Budget manager (edit `sim.yaml` in the page) is **not built**; edit the file ([chapter 8](08-configuration.md)).
- Backend memory stays under 50 MB. Starlette and one static page, no framework, no CDN.
- Browser debugging for developers: `.venv/bin/python scripts/ui_shots.py` ([reference](10-reference.md)).
