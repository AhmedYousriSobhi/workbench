#!/usr/bin/env python3
"""Record the shapes of real Jira Cloud responses as fixtures for the jira-mock contract test (issue 229, NF-2).

Read-only: GET requests and POST /search/jql only. Nothing is written but shapes (keys and value types), so no site name,
account id, email, summary or comment text reaches the repository. Credentials come only from the environment and are never printed:

    export WORKBENCH_JIRA_CLOUD_URL=https://YOURSITE.atlassian.net   # your own free Jira Cloud site
    export JIRA_EMAIL=you@example.com
    export JIRA_API_TOKEN=...                                         # https://id.atlassian.com/manage-profile/security/api-tokens
    python scripts/record_jira_shapes.py --project KEY

The project needs at least one issue. Review `git diff tests/fixtures/jira` before committing; `scripts/check-privacy.sh` also runs.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jira_shapes import shape

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "jira" / "cloud"
FIELDS = ["summary", "status", "assignee", "priority", "labels", "created", "updated"]
# name, method, path, body. {project} and {issue} are filled in here and again, with the mock's own, by the contract test.
REQUESTS = [
    ("serverInfo", "GET", "/rest/api/3/serverInfo", None),
    ("myself", "GET", "/rest/api/3/myself", None),
    ("field", "GET", "/rest/api/3/field", None),
    ("project_search", "GET", "/rest/api/3/project/search", None),
    ("search_jql", "POST", "/rest/api/3/search/jql", {"jql": "project = {project} ORDER BY created DESC", "fields": FIELDS, "maxResults": 2}),
    ("search_jql_bad", "POST", "/rest/api/3/search/jql", {"jql": "this is ((not jql", "fields": ["summary"], "maxResults": 1}),
    ("search_legacy", "GET", "/rest/api/3/search?jql=project%20%3D%20{project}&maxResults=2&fields=summary,status", None),
    ("issue", "GET", "/rest/api/3/issue/{issue}?fields=" + ",".join(FIELDS), None),
    ("issue_missing", "GET", "/rest/api/3/issue/{project}-999999", None),
    ("transitions", "GET", "/rest/api/3/issue/{issue}/transitions", None),
    ("comments", "GET", "/rest/api/3/issue/{issue}/comment", None),
    ("editmeta", "GET", "/rest/api/3/issue/{issue}/editmeta", None),
    ("watchers", "GET", "/rest/api/3/issue/{issue}/watchers", None),
    ("worklog", "GET", "/rest/api/3/issue/{issue}/worklog", None),
]


def fill(text, **names):
    if isinstance(text, str):
        return text.format(**names)
    return {k: fill(v, **names) for k, v in text.items()} if isinstance(text, dict) else [fill(v, **names) for v in text] if isinstance(text, list) else text


def call(site: str, auth: str, method: str, path: str, body):
    req = urllib.request.Request(site + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": auth, "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    try:
        return code, json.loads(raw) if raw else None
    except ValueError:
        return code, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", required=True, help="a project key on your site that has at least one issue")
    args = ap.parse_args()
    site = os.environ.get("WORKBENCH_JIRA_CLOUD_URL", "").rstrip("/")
    email, token = os.environ.get("JIRA_EMAIL", ""), os.environ.get("JIRA_API_TOKEN", "")
    if not (site.startswith("https://") and email and token):
        print("set WORKBENCH_JIRA_CLOUD_URL (https://...), JIRA_EMAIL and JIRA_API_TOKEN in the environment; see the top of this file", file=sys.stderr)
        return 2
    auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
    code, found = call(site, auth, "POST", "/rest/api/3/search/jql",
                       {"jql": f"project = {args.project} ORDER BY created ASC", "fields": ["summary"], "maxResults": 1})
    if code != 200 or not (found or {}).get("issues"):
        print(f"no issue found in project {args.project} (HTTP {code}); check the key, the account and the token", file=sys.stderr)
        return 1
    names = {"project": args.project, "issue": found["issues"][0]["key"]}
    OUT.mkdir(parents=True, exist_ok=True)
    for name, method, path, body in REQUESTS:
        code, data = call(site, auth, method, fill(path, **names), None if body is None else fill(body, **names))
        fixture = {"request": {"method": method, "path": path, **({"body": body} if body is not None else {})}, "status": code, "shape": shape(data)}
        (OUT / f"{name}.json").write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
        print(f"{name}: HTTP {code}")
    print(f"wrote {len(REQUESTS)} fixtures to tests/fixtures/jira/cloud; review the diff, then commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
