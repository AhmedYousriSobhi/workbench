"""Fake Jira (mode "mock"): a REST v2/v3 subset with a workflow, a JQL subset, webhooks and failure modes.

POST /rest/api/{2,3}/issue                        {"fields": {"project": {"key"}, "summary", ...}}
GET  /rest/api/{2,3}/issue/KEY
POST /rest/api/{2,3}/issue/KEY/comment            {"body": "..."}
GET  /rest/api/{2,3}/issue/KEY/transitions
POST /rest/api/{2,3}/issue/KEY/transitions        {"transition": {"id"}, "fields": {...}}  (400 with a per-field error)
GET  /rest/api/{2,3}/search?jql=project = OPS AND status in (Open, "In Progress") ORDER BY created
POST /rest/api/{2,3}/search      {"jql", "maxResults", "startAt", "fields"}        (v2 paging: startAt, total)
GET|POST /rest/api/{2,3}/search/jql  {"jql", "maxResults", "nextPageToken", "fields"}  (Cloud paging: token, isLast)
GET  /rest/api/{2,3}/{myself,serverInfo,field,project,project/search}
JQL: AND OR NOT ( ), = != ~ !~ in, not in, is [not] EMPTY, < > <= >= (dates, -7d), currentUser(), ORDER BY created|updated|key
POST /rest/webhooks/1.0/webhook                   {"url": "...", "events": ["jira:issue_created", ...]}
POST /sim/failure  {"mode": "401|429|503|slow", "count": 3, "delay": 2}   (applies to the next /rest requests)
GET  /healthz
Workflow: Open -> In Progress -> Resolved (needs "resolution") -> Closed; Reopen from any later state.
"""
import base64
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
        if req.get("mode") not in ("401", "429", "503", "slow", None):
            raise JiraError(400, ["mode must be 401, 429, 503, slow or null"])
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
        elif mode == "401":
            raise JiraError(401, ["You are not authenticated. Authentication required to perform this operation."])
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
                "status": status_of("Open"), "labels": f.get("labels") or [], "assignee": person(f.get("assignee")),
                "reporter": person(f.get("reporter") or ME), "priority": {"name": (f.get("priority") or {}).get("name", "Medium")},
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
        issue["fields"]["status"] = status_of(target)
        issue["fields"]["resolution"] = given.get("resolution") if target == "Resolved" else None
        issue["fields"]["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S.000+0000", time.gmtime())
        self.fire("jira:issue_updated", issue, {"changelog": {"items": [{"field": "status", "toString": target}]}})

    def projects(self) -> list[dict]:
        return [{"key": k, "name": k, "id": str(10000 + n)} for n, k in enumerate(sorted(self.counters or {"OPS": 0}))]

    def add_webhook(self, body: dict) -> dict:
        if not body.get("url"):
            raise JiraError(400, errors={"url": "url is required"})
        hook = {"url": body["url"], "events": body.get("events") or ["jira:issue_created", "jira:issue_updated",
                                                                      "comment_created"]}
        self.webhooks.append(hook)
        return hook

    def search(self, jql: str, start: int = 0, limit: int = 50, fields: list[str] | None = None) -> dict:
        """One page of matches. Everything is returned when `fields` is None, else only those fields."""
        q = Query(jql)
        hits = [i for i in self.issues.values() if q.pred(i)]
        if q.order and q.order[0].lower() in ("created", "updated", "key"):
            by = q.order[0].lower()  # ties (one-second timestamps) fall back to creation order
            hits.sort(key=lambda i: (i["key"] if by == "key" else i["fields"][by], int(i["id"])),
                      reverse=len(q.order) > 1 and q.order[1].lower() == "desc")
        page = hits[start:start + limit]
        if fields is not None:
            page = [{"id": i["id"], "key": i["key"], "fields": {k: v for k, v in i["fields"].items() if k in fields}}
                    for i in page]
        return {"total": len(hits), "startAt": start, "maxResults": limit, "issues": page}


FIELDS = ("project", "status", "statuscategory", "assignee", "reporter", "labels", "issuetype", "key", "summary",
          "description", "text", "priority", "resolution", "created", "updated")
CATEGORY = {"Open": ("new", "To Do"), "In Progress": ("indeterminate", "In Progress"),
            "Resolved": ("done", "Done"), "Closed": ("done", "Done")}
ME = "alice"  # what currentUser() and a Bearer token mean
TOKEN = re.compile(r"""\s*(?:("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(!=|!~|<=|>=|[=~<>(),])|([^\s=!~<>(),"']+))""")
RELATIVE = re.compile(r"-(\d+)([mhdw])")


def bad_jql(why: str) -> JiraError:
    return JiraError(400, [f"Error in the JQL Query: {why}"])


def tokens(jql: str) -> list[tuple[str, str]]:
    """[(kind, text)]: kind is "str" (quoted, unescaped), "op" or "word"."""
    out, pos = [], 0
    while pos < len(jql.rstrip()):
        m = TOKEN.match(jql, pos)
        if not m:
            raise bad_jql(f"unexpected character near '{jql[pos:pos + 10].strip()}'")
        pos = m.end()
        if m.group(1):
            out.append(("str", re.sub(r"\\(.)", r"\1", m.group(1)[1:-1])))
        elif m.group(2):
            out.append(("op", m.group(2)))
        else:
            out.append(("word", m.group(3)))
    return out


def person(name: str | dict | None) -> dict | None:
    if not name:
        return None
    if isinstance(name, dict):
        name = name.get("name") or name.get("accountId", "").removesuffix("-id") or name.get("displayName", "")
    return {"name": name, "accountId": f"{name}-id", "displayName": f"{name.title()} Example",
            "emailAddress": f"{name}@example.org", "active": True}


def status_of(name: str) -> dict:
    key, label = CATEGORY[name]
    return {"name": name, "statusCategory": {"key": key, "name": label}}


def instant(value: str) -> str:
    """An absolute date, or a relative one such as -7d, as a sortable ISO string."""
    m = RELATIVE.fullmatch(value)
    if not m:
        return value
    secs = int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() - secs))


def field_values(issue: dict, field: str) -> list[str]:
    f = issue["fields"]
    if field == "key":
        return [issue["key"]]
    if field == "project":
        return [f["project"]["key"]]
    if field == "statuscategory":
        return [f["status"]["statusCategory"]["name"], f["status"]["statusCategory"]["key"]]
    if field in ("status", "issuetype", "priority"):
        return [(f.get(field) or {}).get("name", "")]
    if field in ("assignee", "reporter"):
        p = f.get(field)
        return [p["name"], p["accountId"], p["displayName"], p["emailAddress"]] if p else []
    if field == "labels":
        return list(f["labels"])
    if field == "resolution":
        return [f["resolution"]] if f.get("resolution") else []
    if field in ("created", "updated"):
        return [f[field].replace("T", " ")[:16]]
    if field == "text":
        return [f["summary"], f.get("description") or "", *(c["body"] for c in f["comment"]["comments"])]
    return [f.get(field) or ""] if f.get(field) else []  # summary, description


class Query:
    """A JQL expression: AND, OR, NOT, parentheses, =, !=, ~, !~, in, not in, is [not] EMPTY, < > <= >=, ORDER BY."""

    def __init__(self, jql: str):
        head, *tail = re.split(r"(?i)(?:^|\s+)order\s+by\s+", jql.strip(), maxsplit=1)
        self.toks, self.i = tokens(head), 0
        self.order = tail[0].split()[:2] if tail and tail[0].strip() else []
        self.pred = (lambda _i: True) if not self.toks else self.expr()
        if self.i != len(self.toks):
            raise bad_jql(f"unexpected '{self.toks[self.i][1]}'")

    def peek(self, *words: str) -> bool:
        return self.i < len(self.toks) and self.toks[self.i][1].lower() in words and self.toks[self.i][0] != "str"

    def take(self) -> tuple[str, str]:
        if self.i >= len(self.toks):
            raise bad_jql("unexpected end of the query")
        self.i += 1
        return self.toks[self.i - 1]

    def expr(self):
        parts = [self.conj()]
        while self.peek("or"):
            self.i += 1
            parts.append(self.conj())
        return lambda x: any(p(x) for p in parts)

    def conj(self):
        parts = [self.neg()]
        while self.peek("and"):
            self.i += 1
            parts.append(self.neg())
        return lambda x: all(p(x) for p in parts)

    def neg(self):
        if self.peek("not"):
            self.i += 1
            inner = self.neg()
            return lambda x: not inner(x)
        if self.peek("("):
            self.i += 1
            inner = self.expr()
            if not self.peek(")"):
                raise bad_jql("missing ')'")
            self.i += 1
            return inner
        return self.clause()

    def value(self) -> str:
        kind, text = self.take()
        if kind == "word" and text.lower() == "currentuser" and self.peek("("):
            self.i += 1
            self.take()
            return ME
        return text

    def clause(self):
        kind, name = self.take()
        field = name.lower()
        if kind != "word" or field not in FIELDS:
            raise bad_jql(f"unsupported clause '{name}'")
        _, op = self.take()
        op = op.lower()
        if op == "not":
            self.take()
            op = "not in"
        if op == "is" and self.peek("not"):
            self.i += 1
            op = "is not"
        if op not in ("=", "!=", "~", "!~", "<", ">", "<=", ">=", "in", "not in", "is", "is not"):
            raise bad_jql(f"unsupported operator '{op}' for '{name}'")
        if op in ("in", "not in"):
            if not self.peek("("):
                raise bad_jql("a list is expected after 'in'")
            self.i += 1
            vals = [self.value()]
            while self.peek(","):
                self.i += 1
                vals.append(self.value())
            if not self.peek(")"):
                raise bad_jql("missing ')'")
            self.i += 1
        else:
            vals = [self.value()]
        empty = vals[0].lower() in ("empty", "null")
        return lambda issue: self.test(issue, field, op, vals, empty)

    @staticmethod
    def test(issue: dict, field: str, op: str, vals: list[str], empty: bool) -> bool:
        have = field_values(issue, field)
        low = [h.lower() for h in have]
        want = [v.lower() for v in vals]
        if op in ("is", "=") and empty:
            return not [h for h in have if h]
        if op in ("is not", "!=") and empty:
            return bool([h for h in have if h])
        if op in ("~", "!~"):
            needle = re.sub(r"\\(.)", r"\1", vals[0]).lower()
            hit = any(needle in h for h in low)
            return hit if op == "~" else not hit
        if op in ("<", ">", "<=", ">=") and have:
            a, b = have[0], instant(vals[0])
            return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        hit = any(h in want for h in low)
        return hit if op in ("=", "in", "is") else not hit


FIELD_LIST = [{"id": i, "name": n, "custom": False, "navigable": True, "orderable": True}
              for i, n in (("summary", "Summary"), ("description", "Description"), ("status", "Status"),
                           ("priority", "Priority"), ("assignee", "Assignee"), ("reporter", "Reporter"),
                           ("labels", "Labels"), ("resolution", "Resolution"), ("issuetype", "Issue Type"),
                           ("created", "Created"), ("updated", "Updated"), ("comment", "Comment"),
                           ("project", "Project"))]

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
            if parts in (["search"], ["search", "jql"]) and method in ("GET", "POST"):
                return 200, self._search(parts, method, url.query)
            if parts == ["myself"] and method == "GET":
                return 200, self._myself()
            if parts == ["serverInfo"] and method == "GET":
                return 200, {"baseUrl": f"http://{self.headers.get('Host', 'localhost')}", "version": "9.12.0",
                             "versionNumbers": [9, 12, 0], "deploymentType": "Server", "serverTitle": "workbench jira-mock"}
            if parts == ["field"] and method == "GET":
                return 200, FIELD_LIST
            if parts == ["project"] and method == "GET":
                return 200, jira.projects()
            if parts == ["project", "search"] and method == "GET":
                found = jira.projects()
                return 200, {"values": found, "total": len(found), "isLast": True, "startAt": 0, "maxResults": 50}
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

        def _search(self, parts: list[str], method: str, query: str) -> dict:
            """GET (query string) or POST (JSON body); /search/jql pages by token and has no total, /search by offset."""
            asked = parse_qs(query)
            args = self._body() if method == "POST" else {k: v[0] for k, v in asked.items()}
            fields = args.get("fields")
            if isinstance(fields, str):
                fields = [f.strip() for f in fields.split(",") if f.strip()]
            if not fields or fields[0].startswith("*"):
                fields = None
            try:
                limit, token = int(args.get("maxResults") or 50), args.get("nextPageToken")
                start = int(token) if token else int(args.get("startAt") or 0)
            except ValueError:
                raise JiraError(400, ["maxResults, startAt and nextPageToken must be numbers"]) from None
            found = jira.search(str(args.get("jql") or ""), start, limit, fields)
            if parts == ["search"]:
                return found
            more = start + limit < found["total"]
            return {"issues": found["issues"], "isLast": not more, **({"nextPageToken": str(start + limit)} if more else {})}

        def _myself(self) -> dict:
            """Basic auth names the account by its email (Cloud style); anything else is the default user."""
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Basic "):
                try:
                    email = base64.b64decode(auth[6:]).decode().partition(":")[0]
                except ValueError:
                    email = ""
                if "@" in email:
                    me = person(email.split("@")[0])
                    return {**me, "emailAddress": email}
            return person(ME)

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
