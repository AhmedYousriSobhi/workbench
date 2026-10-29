"""Fake Jira (mode "mock"): a REST v2/v3 subset with a workflow, a JQL subset, webhooks and failure modes.

POST /rest/api/{2,3}/issue                        {"fields": {"project": {"key"}, "summary", ...}}
GET  /rest/api/{2,3}/issue/KEY
POST /rest/api/{2,3}/issue/KEY/comment            {"body": "..."}
GET  /rest/api/{2,3}/issue/KEY/transitions
POST /rest/api/{2,3}/issue/KEY/transitions        {"transition": {"id"}, "fields": {...}}  (400 with a per-field error)
GET  /rest/api/{2,3}/search?jql=project = OPS AND status in (Open, "In Progress") ORDER BY created
POST /rest/webhooks/1.0/webhook                   {"url": "...", "events": ["jira:issue_created", ...]}
POST /sim/failure  {"mode": "429|503|slow", "count": 3, "delay": 2}   (applies to the next /rest requests)
GET  /healthz
Workflow: Open -> In Progress -> Resolved (needs "resolution") -> Closed; Reopen from any later state.
"""
import json
import re
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# status -> [(transition id, name, target, required fields)]
WORKFLOW = {
    "Open": [("11", "Start Progress", "In Progress", []), ("31", "Resolve", "Resolved", ["resolution"])],
    "In Progress": [("21", "Stop Progress", "Open", []), ("31", "Resolve", "Resolved", ["resolution"])],
    "Resolved": [("41", "Close", "Closed", []), ("51", "Reopen", "Open", [])],
    "Closed": [("51", "Reopen", "Open", [])],
}
QUERYABLE = ("project", "status", "assignee", "labels", "issuetype", "key")


class JiraError(Exception):
    def __init__(self, code: int, messages: list[str] | None = None, errors: dict | None = None, headers=None):
        self.code, self.headers = code, headers or {}
        self.body = {"errorMessages": messages or [], "errors": errors or {}}


def post_webhook(url: str, payload: dict) -> None:
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).close()
    except OSError:
        pass  # a dead receiver must not break the API


class Jira:
    def __init__(self, deliver=post_webhook, sleep=time.sleep):
        self.issues: dict[str, dict] = {}
        self.counters: dict[str, int] = {}
        self.webhooks: list[dict] = []
        self.failure = {"mode": None, "count": 0, "delay": 0.0}
        self.deliver, self.sleep = deliver, sleep
        self.lock = threading.Lock()

    def set_failure(self, req: dict) -> None:
        if req.get("mode") not in ("429", "503", "slow", None):
            raise JiraError(400, ["mode must be 429, 503, slow or null"])
        self.failure = {"mode": req.get("mode"), "count": int(req.get("count", 1)), "delay": float(req.get("delay", 2))}

    def gate(self) -> None:
        """Apply the configured failure to one API request."""
        with self.lock:
            f = self.failure
            if not f["mode"] or f["count"] <= 0:
                return
            f["count"] -= 1
            mode, delay = f["mode"], f["delay"]
        if mode == "slow":
            self.sleep(delay)
        elif mode == "429":
            raise JiraError(429, ["Rate limit exceeded"], headers={"Retry-After": "1"})
        else:
            raise JiraError(503, ["Service temporarily unavailable"])

    def fire(self, event: str, issue: dict, extra: dict | None = None) -> None:
        payload = {"webhookEvent": event, "issue": issue, **(extra or {})}
        for hook in self.webhooks:
            if event in hook["events"]:
                threading.Thread(target=self.deliver, args=(hook["url"], payload), daemon=True).start()

    def create(self, body: dict) -> dict:
        f = body.get("fields") or {}
        errors = {}
        project = (f.get("project") or {}).get("key")
        if not project:
            errors["project"] = "project is required"
        if not f.get("summary"):
            errors["summary"] = "You must specify a summary of the issue."
        if errors:
            raise JiraError(400, errors=errors)
        with self.lock:
            self.counters[project] = self.counters.get(project, 0) + 1
            n = self.counters[project]
            key = f"{project}-{n}"
            now = time.strftime("%Y-%m-%dT%H:%M:%S.000+0000", time.gmtime())
            issue = {"id": str(10000 + sum(self.counters.values())), "key": key, "fields": {
                "project": {"key": project}, "summary": f["summary"], "description": f.get("description"),
                "issuetype": {"name": (f.get("issuetype") or {}).get("name", "Task")},
                "status": {"name": "Open"}, "labels": f.get("labels") or [], "assignee": f.get("assignee"),
                "resolution": None, "created": now, "updated": now, "comment": {"comments": []}}}
            self.issues[key] = issue
        self.fire("jira:issue_created", issue)
        return {"id": issue["id"], "key": key, "self": f"/rest/api/2/issue/{key}"}

    def get(self, key: str) -> dict:
        if key not in self.issues:
            raise JiraError(404, ["Issue Does Not Exist"])
        return self.issues[key]

    def comment(self, key: str, body: dict) -> dict:
        issue = self.get(key)
        if not body.get("body"):
            raise JiraError(400, errors={"comment": "Comment body can not be empty!"})
        c = {"id": str(len(issue["fields"]["comment"]["comments"]) + 1), "body": body["body"]}
        issue["fields"]["comment"]["comments"].append(c)
        self.fire("comment_created", issue, {"comment": c})
        return c

    def transitions(self, key: str) -> dict:
        status = self.get(key)["fields"]["status"]["name"]
        return {"transitions": [
            {"id": i, "name": name, "to": {"name": to},
             "fields": {f: {"required": True} for f in req}} for i, name, to, req in WORKFLOW[status]]}

    def transition(self, key: str, body: dict) -> None:
        issue = self.get(key)
        tid = str((body.get("transition") or {}).get("id"))
        match = [t for t in WORKFLOW[issue["fields"]["status"]["name"]] if t[0] == tid]
        if not match:
            raise JiraError(400, [f"Transition {tid} is not valid for this issue's current status"])
        _, _, target, required = match[0]
        given = body.get("fields") or {}
        missing = {f: f"{f.capitalize()} is required." for f in required if not given.get(f)}
        if missing:
            raise JiraError(400, errors=missing)
        issue["fields"]["status"] = {"name": target}
        issue["fields"]["resolution"] = given.get("resolution") if target == "Resolved" else None
        issue["fields"]["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S.000+0000", time.gmtime())
        self.fire("jira:issue_updated", issue, {"changelog": {"items": [{"field": "status", "toString": target}]}})

    def add_webhook(self, body: dict) -> dict:
        if not body.get("url"):
            raise JiraError(400, errors={"url": "url is required"})
        hook = {"url": body["url"], "events": body.get("events") or ["jira:issue_created", "jira:issue_updated",
                                                                      "comment_created"]}
        self.webhooks.append(hook)
        return hook

    def search(self, jql: str) -> dict:
        clauses = parse_jql(jql)
        hits = [i for i in self.issues.values() if all(match(i, *c) for c in clauses)]
        return {"total": len(hits), "startAt": 0, "maxResults": 50, "issues": hits[:50]}


def parse_jql(jql: str) -> list[tuple[str, str, list[str]]]:
    """[(field, op, values)] from `a = x AND b in (y, "z")`; ORDER BY is ignored, anything else is a 400."""
    out = []
    for part in re.split(r"\s+AND\s+", re.split(r"(?:^|\s+)ORDER\s+BY\s+", jql.strip(), flags=re.IGNORECASE)[0], flags=re.IGNORECASE):
        if not part:
            continue
        m = re.fullmatch(r'(\w+)\s*(=|!=|in|not in)\s*(.+)', part.strip(), flags=re.IGNORECASE)
        if not m or m.group(1).lower() not in QUERYABLE:
            raise JiraError(400, [f"Error in the JQL Query: unsupported clause '{part.strip()}'"])
        values = [v.strip().strip('"\'') for v in m.group(3).strip("() ").split(",")]
        out.append((m.group(1).lower(), m.group(2).lower(), values))
    return out


def match(issue: dict, field: str, op: str, values: list[str]) -> bool:
    f = issue["fields"]
    actual = {"key": [issue["key"]], "project": [f["project"]["key"]], "status": [f["status"]["name"]],
              "issuetype": [f["issuetype"]["name"]], "labels": f["labels"],
              "assignee": [(f.get("assignee") or {}).get("name", "")]}[field]
    hit = any(a.lower() == v.lower() for a in actual for v in values)
    return hit if op in ("=", "in") else not hit


API = re.compile(r"^/rest/api/[23]/(.*)$")


PROBE = "x-opti-probe"  # the web UI sets this on its own polls so they are not drawn as client traffic


def counted(path: str, headers) -> bool:
    """A real client request: not a health check, not /sim control, not a UI probe."""
    return not (path.startswith(("/healthz", "/sim/")) or headers.get(PROBE))


def make_handler(jira: Jira):
    stats, lock = {"requests": 0}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj=None, headers=None) -> None:
            if counted(self.path, self.headers):
                with lock:
                    stats["requests"] += 1
            data = b"" if obj is None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            try:
                return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                raise JiraError(400, ["Invalid JSON"]) from None

        def _route(self, method: str):
            url = urlparse(self.path)
            if url.path == "/healthz":
                return 200, {"issues": len(jira.issues)}
            if url.path == "/sim/stats":
                return 200, dict(stats)
            if url.path == "/sim/failure" and method == "POST":
                jira.set_failure(self._body())
                return 200, {"ok": True}
            api = API.match(url.path)
            if not (api or url.path == "/rest/webhooks/1.0/webhook"):
                raise JiraError(404, ["Not found"])
            jira.gate()
            if not api:
                return 201, jira.add_webhook(self._body())
            parts = api.group(1).split("/")
            if parts == ["issue"] and method == "POST":
                return 201, jira.create(self._body())
            if parts == ["search"] and method == "GET":
                return 200, jira.search(parse_qs(url.query).get("jql", [""])[0])
            if parts[0] == "issue" and len(parts) == 2 and method == "GET":
                return 200, jira.get(parts[1])
            if parts[0] == "issue" and len(parts) == 3:
                if (parts[2], method) == ("comment", "POST"):
                    return 201, jira.comment(parts[1], self._body())
                if (parts[2], method) == ("transitions", "GET"):
                    return 200, jira.transitions(parts[1])
                if (parts[2], method) == ("transitions", "POST"):
                    jira.transition(parts[1], self._body())
                    return 204, None
            raise JiraError(404, ["Not found"])

        def _handle(self, method: str) -> None:
            try:
                self._send(*self._route(method))
            except JiraError as e:
                self._send(e.code, e.body, e.headers)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def log_message(self, *args):
            pass

    return Handler


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(Jira())).serve_forever()


if __name__ == "__main__":
    main()
