# Client test report: stress run against the real Jira (issues 301 and 303)

What was run, what was measured, what was seen, and what was **not** exercised. Date of the runs: 2026-11-05. Scenario: `SC-02 client-stress-real` ([scenarios/client-stress-real.yaml](../../scenarios/client-stress-real.yaml)). How to repeat it: [client-testing.md](client-testing.md#stress-test-everything-at-once-while-the-client-runs).

## Setup
| Part | Value |
|---|---|
| Workbench | default selection (4 compute nodes, login, Slurm, the mocks) plus `jira-real` (Jira Data Center 10.3.9 and Postgres, 3-hour test licence) |
| Client | a desktop Electron monitoring client in its dev mode, throwaway config directory, one cluster, one terminal session, SSH by key, Grafana viewer token, Jira Data Center with a personal access token (`alice`) |
| Client window | 900x670 at first, then full screen (the layout changes: see "What was not exercised") |
| Host free memory | 5.4 GB at level L0 during the runs |

## Results

### 1. Unit and repository checks (this change)
| Check | Result |
|---|---|
| `validate.sh` (privacy scan, compile, ruff, YAML/JSON, pytest, coverage floor 90%, governor smoke) | passed on every commit; last full pytest 833 passed, 126 skipped, total coverage 95% |
| New unit tests | `jira-real` faults (outage, restore, bad logins, clear logins, rate limit on and off, failure paths, unknown fault, host-less skip), scenario references (unique, load by `SC-02`/`sc-2`), scenario list sorted by name |
| `verify.sh` and `tests/real` | **not run** |

### 2. Live checks of each fault on the real Jira (probe, no client)
| Fault | Observed |
|---|---|
| `bad_logins` (6 wrong-password logins, user alice) | 401, then 403 with `X-Authentication-Denied-Reason: CAPTCHA_CHALLENGE`; a token (Bearer) was not affected; `clear_logins` unlocked the user at once |
| `outage` | calls timed out (curl code 000) while the container was paused; back to 200 after `restore` |
| `rate_limit` (capacity 3, refill 1 per second) | burst of 6: 3 x 200 and 3 x 429 with `X-RateLimit-Limit`, `-Remaining`, `-FillRate`, `-Interval-Seconds`, `Retry-After`; `rate_limit_off` back to all 200; each change takes 40 s to show (Jira reloads about once a minute); `Retry-After` was `0` on Jira's own 429 |
| Not counted by Jira's limiter | `myself`, `serverInfo`, `search`; counted: `project`, `field`, `issue` |

### 3. `SC-02 client-stress-real` with the client connected (probe of Jira every 10 s, 4 token calls to `field`)
| Time into the run | What the probe saw |
|---|---|
| 0 to about 100 s | 4 x 200 |
| about 100 to 170 s | 3 x 200 + 1 x 429 (the limiter is on) |
| 180 s | 4 x 200 (limiter off) |
| 190 to 217 s | all calls time out (Jira paused for 30 s) |
| 217 s on | 4 x 200 (restored; the CAPTCHA lock was cleared at 230 s) |

Slurm, GPU, BMC and BCM steps all reported `ok`; the run ended `scenario passed` (three runs, same result).

### 4. Client memory (measured with `benchctl measure app`, 460 to 500 MB range)
| Run | Views opened | Processes | Peak | Typical |
|---|---|---|---|---|
| Idle, 40 s | none | 7 | 460 MB | 458 MB |
| Stress, views not clicked (script found no buttons) | none | up to 10 | 472 MB | 464 MB |
| Stress, 6 of 10 views | Grafana, Slurm, Storage, Tickets and docs, Notes, Overview | up to 10 | 477 MB | 471 MB |
| Stress, 9 of 10 views (58 clicks, `Nodes` missed 5 times) | all above plus Jobs, Partitions, GPUs | up to 10 | **500 MB** | **483 MB** |

The earlier run of the same client against the mock Jira (an older build of the client, a different window size) gave 469 MB idle and 664 MB peak. The two are not comparable one to one; both stay under the 1024 MB default reserve, so the default stands. The saved local reserve (800 MB) was left as it was.

## What was not exercised
- **The client's Jira panel never showed live tickets.** The client's Tickets and docs card said "This cluster has no Jira account" (Not checked) for the whole run, although the client's own Jira center held a Data Center connection for the cluster and served the real issues. So how the client's Jira panel reacts to a 429, a hang and a CAPTCHA lock is **not verified**; only the polling of its Jira center ran against the faults. Issue to follow up: see below.
- **The `Nodes` view** (five missed clicks), more than one cluster, more than one terminal, file transfer, the Teleport path.
- **A real slow or 503 answer from Jira** and **tickets on the real Jira** (scenario steps file tickets only on the mock).
- Jira Cloud (the client's account form accepts a Cloud email and token only; Data Center is its "experimental" path).

## Findings
1. The client uses about 500 MB for one cluster with a terminal and nine views, and the memory reserve default (1024 MB) is enough for that; several clusters and terminals still need their own measurement.
2. A Data Center connection that the client's Jira center serves is shown as "no Jira account" on the Overview card. A suspected client defect; to be checked in the client's own project, not here.
3. Jira's own 429 sends `Retry-After: 0`, which a client that sleeps for that long will not back off on; the mock sends 1.
4. Setting up the client by hand (account, claim, cluster through its API, host key) is slow; scripting it would make this report repeatable.

## Open items
Filed as issues: see the list at the end of [client-testing.md](client-testing.md#open-items-after-the-real-jira-run).
