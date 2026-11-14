# ADR 0072: Alertmanager as a core service, with alerts opening Jira mock tickets

Status: accepted by the owner on 2026-11-14 (issue 460). Built.

## Context
SPEC C-7 lists Alertmanager in the observability stack, but none existed: Prometheus evaluated rules (ADR 0071) and nothing received them. The config lab (epic 557) also reads firing state and wants the rules a lab report needs. Alertmanager only sends a webhook; it cannot create a Jira ticket.

## Decision
- **Core service, always on with Prometheus** (`compose/observability.yml`, `prom/alertmanager:v0.27.0`, fixed address 10.141.255.23, port 127.0.0.1:9093, class P1, memory limit 64 MB, disposable tmpfs for silences). It is not an optional part: alerts always have somewhere to go. Prometheus reaches it through `alerting` in `observability/prometheus.yml`.
- **One webhook receiver, on the Jira mock.** `observability/alertmanager.yml` posts every alert group to `http://jira-mock:8080/sim/alerts`. The mock opens one ticket per alert fingerprint (project OPS, labels `alert`, `alertname-NAME`, `alert-FINGERPRINT`, priority High for severity `page`), comments on the open ticket when it fires again, and comments (never closes) when it resolves. No new container or process. With the Jira part off, Alertmanager logs one failed delivery and nothing else happens. `/sim/*` and `/metrics` are never counted as client traffic.
- **Six rules**, the lab's names where they exist: `GpuXidCritical`, `GpuTempHigh`, `InfinibandPortDown`, `NodeDown`, `ExporterDown`, `JiraErrors`. `GpuXid` and `GpuOverheat` of ADR 0071 were renamed. Each has a `promtool test rules` case in `tests/rules/rules_test.yml`.
- **`GET /metrics` on the Jira mock** (`jira_mock_requests_total`, `jira_mock_errors_total`, text format, stdlib only), scraped as job `jira-mock`, feeds `JiraErrors`. Scrapes never count as client requests.
- Alertmanager timings are short (group wait 5 s, interval 15 s, repeat 1 h) so a simulated fault reaches a ticket in about half a minute.

## Not done
- `NodeDrained` (a drained Slurm node): Prometheus has no Slurm node state until Slurm metrics exist (issue 459). Added with them.
- The 20 MB memory figure for Alertmanager is an estimate; `benchctl measure` replaces it.

## Consequences
- Every `up` starts one more container (64 MB limit, about 20 MB used). The observability budget rises accordingly.
- The monitored app can read the alert history at Alertmanager `127.0.0.1:9093` and the tickets from the Jira mock; the app's own alerting is untouched.
- The rule names are part of what agents read (`get alerts`); a rename is a docs change in the recipes and the verbs guide.
