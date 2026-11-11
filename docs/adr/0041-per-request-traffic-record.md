# ADR 0041: A per-request traffic record in the mocks, and the analysis that reads it

Status: accepted (owner decision of 2026-11-11 on the caller label). Issue 501, epic 491; extends #475.

## Context
Redesign step 5 (the Traffic analysis screen) needs more than the per-shape counts that `GET /sim/paths` gives: who asked, how it ended
(2xx, 4xx, 5xx), how long it took, and when, so it can draw a request river, a probe, repeated-request periods and a list of odd things.
The rule of #475 stays: record only method and path shape, never a value or a secret.

## Decision
- **The record.** The three mocks (bmc-farm, bcm-mock, jira-mock; one identical block, checked by `tests/test_path_shapes.py`) keep per stream
  (caller, shape): counts by status class, a latency histogram (edges 5, 10, 25, 50, 100, 250, 500, 1000 ms and one bucket above) and the
  sum of latencies, plus a ring of the last 400 requests `[time, caller, shape, status, ms]`. `GET /sim/traffic[?since=T]` serves it;
  `/sim/paths` and `/sim/stats` are unchanged. Probes and `/sim/*` stay excluded, so reading the record is not traffic.
- **The caller label** is the User-Agent's product token only (a letter, then letters, digits, `_`, `.`, `-`; stops at the first slash or space;
  lower-cased; at most 24 characters; `unknown` otherwise). No version, no other header, no address. Owner decision.
- **Latency** is measured from reading the request line to sending the answer (`parse_request` starts the clock), so a keep-alive wait is not
  counted and a slow-failure scenario (`/sim/failure slow`) is. It measures the mock, not the network.
- **`/api/traffic`** (`webui/traffic.py`) merges the three, tagging each row with its service, and says per service whether it answered
  (true), did not (false) or answered without a record (null, an older image). No polling of its own: it runs only when asked.
- **The analysis is pure JavaScript** (`webui/static/js/trafficanalysis.js`, tested with `node --test`): request rates from two samples at
  least 5 s apart; percentiles as bucket edges (never an exact figure); river bands whose width follows the square root of the rate, with
  "slower than p95" taken out of the ok share (the histogram does not split by status); period detection by autocorrelation of counts per
  0.25 s bin, per stream, at least 8 requests and three repeats, harmonics folded into the shorter period; and three findings (no retry after
  server errors, several calls fired in one half second, an unidentified caller repeating on a timer).
- **Not measured says so.** A rate before two samples, a period without three repeats, a service without a record: null, shown as "not
  measured", never 0.

## Consequences
- Two timers inside one stream make beat patterns that look like periods; the screen therefore detects per stream.
- A client with no User-Agent is `unknown`, and its repeated requests are flagged as an unidentified timer, which is useful, not an error.
- The ring is 400 rows of about 80 bytes per mock; the memory budgets do not change.
- Message capture for one wire (Slurm RPC types, the Kubernetes audit) and the screen itself are step 5b.
