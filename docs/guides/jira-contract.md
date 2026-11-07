# Jira contract: compare jira-mock with real Jira Cloud

jira-mock must answer the way real Jira does for the calls the client makes (SPEC NF-2). This records the **shape** of real Jira Cloud
responses once (keys and value types, never values) and a test fails when the mock drifts from them. Issue 229.

## Record (needs your own free Jira Cloud site)

1. Create a free site at atlassian.com, a project with at least one issue, and an API token at
   https://id.atlassian.com/manage-profile/security/api-tokens.
2. Put the three values in your shell, never in a file in this repository:

```bash
export WORKBENCH_JIRA_CLOUD_URL=https://YOURSITE.atlassian.net
export JIRA_EMAIL=you@example.com
export JIRA_API_TOKEN=...
```

3. Record (read-only: GETs and `POST /search/jql`; writes nothing to your site):

```bash
python scripts/record_jira_shapes.py --project KEY
```

It writes `tests/fixtures/jira/cloud/*.json`. Open the diff before you commit: every leaf must be a type name
(`string`, `number`, `boolean`, `null`), and `./scripts/check-privacy.sh --all` must pass. The site name, the account ids, the
summaries and the comment text are never written.

## Test

```bash
python -m pytest -q tests/test_jira_contract.py
```

- Without fixtures the contract tests skip and say how to record; the comparison itself is always tested.
- A key the real response has and the mock lacks, or a different value type, fails the test with its path.
- A difference you accept goes in `tests/fixtures/jira/known-gaps.yaml` with a reason (and in ADR 0005). A listed gap that no
  longer occurs fails the test, so the list cannot go stale.

## What it covers and what it does not

Covered: `serverInfo`, `myself`, `field`, `project/search`, `search/jql` (and a bad JQL error), the legacy `search`, one issue and a
missing one, transitions, comments, editmeta, watchers, worklog; status codes included.
Not covered: writes (create, transition with a refused required field, comment), because they change your site; Data Center
(only Cloud is recorded; the `dc` mode is unchecked); behaviour (ordering, JQL semantics), only shapes.
