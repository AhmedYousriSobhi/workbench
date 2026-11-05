"""Ticket traffic through the standard Jira REST API (issue 262).

The mock's `/sim/tickets` and `/sim/activity` do not exist on a real Jira. This module does what they do (a node fault opens or
updates one ticket, recovery resolves it, a team moves tickets along) with calls any Jira accepts: search/jql, issue, comment,
transitions, assignee. It runs on the host, never in the Web UI (SPEC W-3), and reads credentials only from the environment.

Targets: jira-mock and the real Jira Data Center part (issue 284, `for_target`), one code path for both. The workflow differs
(mock: Open / In Progress / Resolved / Closed; real: To Do / In Progress / Done), so statuses are compared by status category
(new, indeterminate, done), not by name, and `move` follows the transitions Jira offers. Data Center has no `search/jql`
(`dc` flag: `/search` with offsets), identifies users by name, and has no "ops-bot" or "Incident" type; the driver adapts.
Jira Cloud (issue 229): ADF bodies on the v3 API (`adf` flag, helpers below, not yet checked against a real site), account ids.
Every ticket the driver files carries the label `workbench`, so they can be found and removed.
"""
from __future__ import annotations

import base64
import calendar
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LABEL = "workbench"
FAULTS = {"power": ("node is down (powered off)", "Highest"), "xid": ("GPU error", "High"),
          "overheat": ("node is overheating", "High"), "fan": ("fan failure", "Medium"),
          "ib": ("InfiniBand port down", "High"), "nic": ("NIC link flapping", "Medium"),
          "slurm": ("Slurm took the node out of service", "High")}  # kind: (summary, priority); same table as jira-mock
CATEGORY = {"Open": "new", "To Do": "new", "In Progress": "indeterminate", "Resolved": "done", "Closed": "done", "Done": "done"}
RANK = {"new": 0, "indeterminate": 1, "done": 2}
ENGINEERS = ("bob", "carol", "dave", "erin")
CHATTER = ("Looking into this now.", "Can someone confirm this is still happening?", "Checked the node, nothing obvious yet.",
           "Restarted the service, watching it.", "Vendor case opened, waiting on a reply.", "Seen this before, same cause as last week.")
COOLDOWN = 20  # seconds a ticket is left alone after any change, so a client's own edit is not undone under it


class JiraError(Exception):
    def __init__(self, code: int, body):
        super().__init__(f"Jira answered {code}: {str(body)[:200]}")
        self.code, self.body = code, body


def adf(text: str) -> dict:
    """Atlassian Document Format: what Jira Cloud's v3 API wants for a comment or description body."""
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def text_of(body) -> str:
    """The plain text of a v2 string or a v3 ADF document."""
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        return (body.get("text") or "") + " ".join(t for t in (text_of(c) for c in body.get("content") or []) if t)
    return ""


class JiraDriver:
    def __init__(self, base_url: str, *, auth: str = "", api: int = 2, project: str = "OPS", adf: bool = False, dc: bool = False,
                 rng: random.Random | None = None, sleep=time.sleep):
        self.base, self.auth, self.api, self.project, self.adf, self.dc = base_url.rstrip("/"), auth, api, project, adf, dc
        self._types: set[str] | None = None
        self.rng, self.sleep = rng or random.Random(), sleep

    @classmethod
    def from_env(cls, environ=os.environ, **kw) -> JiraDriver:
        """Jira Cloud from WORKBENCH_JIRA_CLOUD_URL, JIRA_EMAIL and JIRA_API_TOKEN; never from a file in the repository."""
        url, email, token = (environ.get(k, "") for k in ("WORKBENCH_JIRA_CLOUD_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))
        if not (url.startswith("https://") and email and token):
            raise JiraError(0, "set WORKBENCH_JIRA_CLOUD_URL (https://...), JIRA_EMAIL and JIRA_API_TOKEN in the environment")
        auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
        return cls(url, auth=auth, api=3, **kw)

    @classmethod
    def for_target(cls, target: str, environ=os.environ, root: Path | None = None, **kw) -> JiraDriver:
        """`mock`: the Jira mock on 127.0.0.1:8080 (WORKBENCH_JIRA_MOCK_URL). `real`: the Data Center part, with the admin token that
        `benchctl setup jira-real` wrote to compose/generated/secrets/jira-real.env."""
        if target == "mock":
            return cls(environ.get("WORKBENCH_JIRA_MOCK_URL", "http://127.0.0.1:8080"), **kw)
        if target != "real":
            raise JiraError(0, "target must be mock or real")
        from benchctl import jira_real
        env = jira_real.read_env((root or Path.cwd()) / jira_real.ENV)
        if not (env.get("JIRA_URL") and env.get("PAT_ADMIN")):
            raise JiraError(0, "the real Jira is not set up: benchctl start --with jira-real, then benchctl setup jira-real")
        return cls(env["JIRA_URL"], auth="Bearer " + env["PAT_ADMIN"], dc=True, project=jira_real.PROJECT, **kw)

    # ---- transport ----
    def call(self, method: str, path: str, body=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json", "X-Workbench-Probe": "driver"}
        if self.auth:
            headers["Authorization"] = self.auth
        req = urllib.request.Request(f"{self.base}/rest/api/{self.api}{path}", method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            try:
                raise JiraError(e.code, json.loads(e.read() or b"null")) from None
            except ValueError:
                raise JiraError(e.code, "") from None
        return json.loads(raw) if raw else None

    def text(self, value: str):
        return adf(value) if self.adf else value

    # ---- reading ----
    def search(self, jql: str, fields=("summary", "status", "labels", "updated")) -> list[dict]:
        out, token = [], None
        if self.dc:
            while True:
                page = self.call("POST", "/search", {"jql": jql, "fields": list(fields), "maxResults": 100, "startAt": len(out)})
                out += page["issues"]
                if not page["issues"] or len(out) >= page["total"]:
                    return out
        while True:
            page = self.call("POST", "/search/jql", {"jql": jql, "fields": list(fields), "maxResults": 100,
                                                     **({"nextPageToken": token} if token else {})})
            out += page["issues"]
            token = page.get("nextPageToken")
            if page.get("isLast", True) or not token:
                return out

    def comments(self, key: str) -> list[str]:
        return [text_of(c.get("body")) for c in self.call("GET", f"/issue/{key}/comment")["comments"]]

    # ---- writing ----
    def create(self, summary: str, *, description: str = "", labels=(), issuetype: str = "Task", priority: str = "Medium",
               reporter: str | None = None) -> str:
        fields = {"project": {"key": self.project}, "summary": summary, "issuetype": {"name": self.issue_type(issuetype)},
                  "priority": {"name": priority}, "labels": [LABEL, *labels]}
        if description:
            fields["description"] = self.text(description)
        if reporter and (not self.dc or reporter in ENGINEERS):  # the real Jira knows alice to erin, not "ops-bot"
            fields["reporter"] = {"name": reporter}
        return self.call("POST", "/issue", {"fields": fields})["key"]

    def issue_type(self, wanted: str) -> str:
        """The project's own issue type: the real Jira's basic project has no Incident, so Bug stands in, then Task."""
        if not self.dc:
            return wanted
        if self._types is None:
            try:
                self._types = {t["name"] for t in self.call("GET", f"/project/{self.project}")["issueTypes"]}
            except (JiraError, KeyError):
                self._types = set()
        return wanted if wanted in self._types or not self._types else next((t for t in ("Bug", "Task") if t in self._types), wanted)

    def comment(self, key: str, text: str) -> None:
        self.call("POST", f"/issue/{key}/comment", {"body": self.text(text)})

    def move(self, key: str, target: str, resolution: str = "Done") -> None:
        """Walk the workflow towards `target` (a name from either workflow) through whatever transitions Jira offers: an exact
        name first, then any status of the same category (Done for Resolved), else forwards, else back to the start. A target
        the workflow has no status for (Closed after Done) is reached when the ticket is in the same category. A required
        resolution is supplied."""
        goal = CATEGORY.get(target, "done")
        for _ in range(6):
            st = self.call("GET", f"/issue/{key}?fields=status")["fields"]["status"]
            now, cat = st["name"], st.get("statusCategory", {}).get("key") or CATEGORY.get(st["name"], "new")
            if now == target:
                return
            offered = self.call("GET", f"/issue/{key}/transitions")["transitions"]

            def to_cat(t):
                return (t["to"].get("statusCategory") or {}).get("key") or CATEGORY.get(t["to"]["name"], "new")
            direct = [t for t in offered if t["to"]["name"] == target]
            if not direct and cat == goal:
                return
            same = [t for t in offered if to_cat(t) == goal]
            forward = [t for t in offered if RANK[cat] < RANK[to_cat(t)] <= RANK[goal]]
            back = [t for t in offered if to_cat(t) == "new"]
            pick = (direct or same or sorted(forward, key=lambda t: -RANK[to_cat(t)]) or back or [None])[0]
            if pick is None:
                raise JiraError(400, f"no transition from {now} towards {target}")
            body = {"transition": {"id": pick["id"]}}
            if "resolution" in (pick.get("fields") or {}):
                body["fields"] = {"resolution": {"name": resolution}}
            self.call("POST", f"/issue/{key}/transitions", body)
        raise JiraError(400, f"{key} did not reach {target}")

    def assign(self, key: str, who: str) -> None:
        if self.dc:
            self.call("PUT", f"/issue/{key}/assignee", {"name": who})
            return
        found = self.call("GET", f"/user/search?query={urllib.parse.quote(who)}")
        pick = next((u for u in found if who in (u.get("name"), u.get("displayName", "").lower())), found[0] if found else None)
        self.call("PUT", f"/issue/{key}/assignee", {"accountId": pick["accountId"]} if pick and pick.get("accountId") else {"name": who})

    # ---- what the simulator does ----
    def fault(self, node: str, kind: str = "", detail: str = "", *, open: bool = True, merge: bool = False) -> dict:
        """A node fault: one open ticket per node and kind (a repeat comments, an identical comment is not added twice); recovery
        comments and resolves the node's open tickets of that kind (every kind when `kind` is empty)."""
        if not node or (open and kind not in FAULTS):
            raise JiraError(400, "fault needs node and, to open one, kind: " + ", ".join(FAULTS))
        merge = merge and open
        found = self.search(f'project = {self.project} AND labels = "node-{node}" AND statusCategory != Done ORDER BY created ASC')
        mine = [i for i in found if merge or not kind or f"fault-{kind}" in i["fields"]["labels"]]
        if not open:
            for i in mine:
                self.comment(i["key"], f"Recovered. {detail}".strip())
                self.move(i["key"], "Resolved", "Done")
            return {"resolved": [i["key"] for i in mine]}
        if mine:
            text = f"{'Slurm reports: ' if kind == 'slurm' else 'Still failing. '}{detail}".strip()
            seen = self.comments(mine[0]["key"])
            if not seen or seen[-1] != text:
                self.comment(mine[0]["key"], text)
            return {"existing": mine[0]["key"]}
        title, priority = FAULTS[kind]
        return {"created": self.create(f"{node}: {title}", description=detail or title, issuetype="Incident", priority=priority,
                                       labels=[f"node-{node}", f"fault-{kind}", "auto"], reporter="ops-bot")}

    def tick(self) -> str | None:
        """One change a team would make: move a ticket along, comment, assign, reopen or file a new one. Tickets changed in the
        last COOLDOWN seconds are left alone. Returns what was done, or None."""
        r = self.rng
        now = time.time()
        mine = self.search(f'project = {self.project} AND labels = "{LABEL}" ORDER BY updated ASC')
        quiet = [i for i in mine if now - _epoch(i["fields"]["updated"]) >= COOLDOWN]
        live = [i for i in quiet if _cat(i) != "done"]
        done = [i for i in quiet if _cat(i) == "done" and i["fields"]["status"]["name"] not in ("Closed", "Done")]  # Done is the end
        reopenable = [i for i in quiet if _cat(i) == "done" and i["fields"]["status"]["name"] != "Closed"]
        choice = r.choices(["advance", "comment", "assign", "create", "reopen"], [45, 25, 10, 12, 8])[0]
        if choice == "create" and sum(_cat(i) != "done" for i in mine) >= 30:
            choice = "advance"
        who = r.choice(ENGINEERS)
        if choice == "create":
            return f"created {self.create('Follow-up from the team', labels=['activity'], reporter=r.choice(ENGINEERS))}"
        pool = {"advance": live + done, "comment": live, "assign": live, "reopen": reopenable}[choice]
        if not pool:
            return None
        issue = r.choice(pool)
        key, status = issue["key"], _cat(issue)
        if choice == "comment":
            self.comment(key, r.choice(CHATTER))
        elif choice == "assign":
            self.assign(key, who)
        elif choice == "reopen":
            self.move(key, "Open")
        elif status == "new":
            self.move(key, "In Progress")
        elif status == "indeterminate":
            self.move(key, "Resolved")
        else:
            self.move(key, "Closed")
        return f"{choice} {key}"


def _cat(issue: dict) -> str:
    st = issue["fields"]["status"]
    return (st.get("statusCategory") or {}).get("key") or CATEGORY.get(st["name"], "new")


def _epoch(stamp: str) -> float:
    """Seconds since the epoch of a Jira timestamp such as 2026-11-05T08:40:04.123+0200 (the offset counts)."""
    t = calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
    m = re.search(r"([+-])(\d\d):?(\d\d)$", stamp[19:])
    return t - (1 if m.group(1) == "+" else -1) * (int(m.group(2)) * 3600 + int(m.group(3)) * 60) if m else t
