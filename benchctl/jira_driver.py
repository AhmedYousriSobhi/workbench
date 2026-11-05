"""Ticket traffic through the standard Jira REST API (issue 262).

The mock's `/sim/tickets` and `/sim/activity` do not exist on a real Jira. This module does what they do (a node fault opens or
updates one ticket, recovery resolves it, a team moves tickets along) with calls any Jira accepts: search/jql, issue, comment,
transitions, assignee. It runs on the host, never in the Web UI (SPEC W-3), and reads credentials only from the environment.

Step 1 (this file): verified against jira-mock. Step 2, after the real Cloud shapes are recorded (issue 229): ADF bodies on the
v3 API (`adf` flag, helpers below, not yet checked against a real site), account ids, 429 backoff, cleanup.
Every ticket the driver files carries the label `workbench`, so they can be found and removed.
"""
from __future__ import annotations

import base64
import calendar
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

LABEL = "workbench"
FAULTS = {"power": ("node is down (powered off)", "Highest"), "xid": ("GPU error", "High"),
          "overheat": ("node is overheating", "High"), "fan": ("fan failure", "Medium"),
          "ib": ("InfiniBand port down", "High"), "nic": ("NIC link flapping", "Medium"),
          "slurm": ("Slurm took the node out of service", "High")}  # kind: (summary, priority); same table as jira-mock
OPEN = ("Open", "In Progress")
ORDER = ("Open", "In Progress", "Resolved", "Closed")
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
    def __init__(self, base_url: str, *, auth: str = "", api: int = 2, project: str = "OPS", adf: bool = False,
                 rng: random.Random | None = None, sleep=time.sleep):
        self.base, self.auth, self.api, self.project, self.adf = base_url.rstrip("/"), auth, api, project, adf
        self.rng, self.sleep = rng or random.Random(), sleep

    @classmethod
    def from_env(cls, environ=os.environ, **kw) -> JiraDriver:
        """Jira Cloud from WORKBENCH_JIRA_CLOUD_URL, JIRA_EMAIL and JIRA_API_TOKEN; never from a file in the repository."""
        url, email, token = (environ.get(k, "") for k in ("WORKBENCH_JIRA_CLOUD_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))
        if not (url.startswith("https://") and email and token):
            raise JiraError(0, "set WORKBENCH_JIRA_CLOUD_URL (https://...), JIRA_EMAIL and JIRA_API_TOKEN in the environment")
        auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
        return cls(url, auth=auth, api=3, **kw)

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
        fields = {"project": {"key": self.project}, "summary": summary, "issuetype": {"name": issuetype},
                  "priority": {"name": priority}, "labels": [LABEL, *labels]}
        if description:
            fields["description"] = self.text(description)
        if reporter:
            fields["reporter"] = {"name": reporter}
        return self.call("POST", "/issue", {"fields": fields})["key"]

    def comment(self, key: str, text: str) -> None:
        self.call("POST", f"/issue/{key}/comment", {"body": self.text(text)})

    def move(self, key: str, target: str, resolution: str = "Done") -> None:
        """Walk the workflow to `target` through whatever transitions Jira offers; a required resolution is supplied."""
        for _ in range(6):
            now = self.call("GET", f"/issue/{key}?fields=status")["fields"]["status"]["name"]
            if now == target:
                return
            offered = self.call("GET", f"/issue/{key}/transitions")["transitions"]
            rank = {n: i for i, n in enumerate(ORDER)}
            goal = rank.get(target, len(ORDER))
            direct = [t for t in offered if t["to"]["name"] == target]
            forward = [t for t in offered if now in rank and t["to"]["name"] in rank and rank[now] < rank[t["to"]["name"]] <= goal]
            back = [t for t in offered if t["to"]["name"] == ORDER[0]]
            pick = (direct or sorted(forward, key=lambda t: -rank[t["to"]["name"]]) or back or [None])[0]
            if pick is None:
                raise JiraError(400, f"no transition from {now} towards {target}")
            body = {"transition": {"id": pick["id"]}}
            if "resolution" in (pick.get("fields") or {}):
                body["fields"] = {"resolution": {"name": resolution}}
            self.call("POST", f"/issue/{key}/transitions", body)
        raise JiraError(400, f"{key} did not reach {target}")

    def assign(self, key: str, who: str) -> None:
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
        found = self.search(f'project = {self.project} AND labels = "node-{node}" AND status not in (Resolved, Closed) ORDER BY created ASC')
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
        live = [i for i in quiet if i["fields"]["status"]["name"] in OPEN]
        done = [i for i in quiet if i["fields"]["status"]["name"] == "Resolved"]
        choice = r.choices(["advance", "comment", "assign", "create", "reopen"], [45, 25, 10, 12, 8])[0]
        if choice == "create" and sum(i["fields"]["status"]["name"] != "Closed" for i in mine) >= 30:
            choice = "advance"
        who = r.choice(ENGINEERS)
        if choice == "create":
            return f"created {self.create('Follow-up from the team', labels=['activity'], reporter=r.choice(ENGINEERS))}"
        pool = {"advance": live + done, "comment": live, "assign": live, "reopen": done}[choice]
        if not pool:
            return None
        issue = r.choice(pool)
        key, status = issue["key"], issue["fields"]["status"]["name"]
        if choice == "comment":
            self.comment(key, r.choice(CHATTER))
        elif choice == "assign":
            self.assign(key, who)
        elif choice == "reopen":
            self.move(key, "Open")
        elif status == "Open":
            self.move(key, "In Progress")
        elif status == "In Progress":
            self.move(key, "Resolved")
        else:
            self.move(key, "Closed")
        return f"{choice} {key}"


def _epoch(stamp: str) -> float:
    return calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
