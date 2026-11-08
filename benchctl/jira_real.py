"""A real Jira Data Center as an optional part (ADR 0026, issue 284): the official image, Postgres, and the setup that
Jira's wizard asks for, done by code so nobody clicks through it.

The pieces: `ensure_secrets` makes the passwords once; `provision` finishes the wizard (database, properties, licence,
administrator, no mail), waits until REST really answers, seeds users, a project and tickets, and makes a personal access
token per user. Everything talks to localhost only: a Jira that is not on this machine is refused, so a typo can never
seed a real site. The licence is Atlassian's published 3-hour Data Center test key, read from a file that stays out of git;
after 3 hours the server is read-only and `benchctl remove jira-real-state` starts from nothing (the data volumes are removed)."""
import http.cookiejar
import json
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

IMAGE = "atlassian/jira-software:10.3.9"  # the version the setup below was written against; pin, never `latest`
DB_IMAGE = "postgres:15"
PORT = 8083  # localhost; 8080 is jira-mock
URL = f"http://127.0.0.1:{PORT}"
SECRETS = Path("compose/generated/secrets")
DB_ENV, ENV, LICENSE = SECRETS / "jira-db.env", SECRETS / "jira-real.env", SECRETS / "jira.license"
LICENSE_VAR = "WORKBENCH_JIRA_LICENSE_FILE"
DB_HOST, DB_NAME, DB_USER = "jira-db", "jira", "jira"
PROJECT = "OPS"  # the key the mock and the exported client profile already use
PEOPLE = [("alice", "Alice Example"), ("bob", "Bob Example"), ("carol", "Carol Example"),
          ("dave", "Dave Example"), ("erin", "Erin Example")]  # the names the mock's team uses
TICKETS = 24
# Memory (issue 287): the smallest heap and container limit that pass the setup and a ticket run, measured (see ADR 0026).
HEAP_MIN_MB, HEAP_MAX_MB, CODE_CACHE_MB, LIMIT_MB, DB_LIMIT_MB = 512, 768, 128, 2176, 256
JVM_ARGS = "-XX:+UseSerialGC -XX:TieredStopAtLevel=1 -Xss512k -XX:CICompilerCount=1"  # smaller JVM footprint: a 2176 MB limit works with them (docs/guides/jira-real.md, issue 289)
CONTAINER = "workbench-jira-real-1"
VOLUMES = ("jira-home", "jira-db")  # compose volumes: `reset` removes them


class JiraRealError(Exception):
    pass


def ensure_secrets(db_env: Path = DB_ENV, env: Path = ENV) -> None:
    """The database password, the administrator's and the users' password: random, made once, never printed."""
    for path, lines in ((db_env, [f"POSTGRES_USER={DB_USER}", f"POSTGRES_DB={DB_NAME}", f"POSTGRES_PASSWORD={secrets.token_urlsafe(18)}"]),
                        (env, [f"JIRA_ADMIN_PASSWORD={secrets.token_urlsafe(18)}", f"JIRA_USER_PASSWORD={secrets.token_urlsafe(18)}"])):
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text("\n".join(lines) + "\n")
        path.chmod(0o644 if path is db_env else 0o600)  # the database container reads its file as another user


def read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line) if path.is_file() else {}


def read_license(path: Path | None = None) -> str:
    """The licence text, from $WORKBENCH_JIRA_LICENSE_FILE or compose/generated/secrets/jira.license."""
    path = path or Path(os.environ.get(LICENSE_VAR) or LICENSE)
    text = path.read_text().strip() if path.is_file() else ""
    if not text:
        raise JiraRealError(f"no Jira licence at {path}: save Atlassian's published 3-hour Data Center test licence there "
                            f"(or point ${LICENSE_VAR} at it); see docs/guides/jira-real.md")
    return text


def check_local(base: str) -> str:
    """A base URL on this machine only; anything else is refused (the setup writes users and tickets)."""
    host = urllib.parse.urlsplit(base).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise JiraRealError(f"refusing {base}: the setup seeds users and tickets and only talks to localhost")
    return base.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # the wizard answers a form with a redirect: read it, do not follow it
        return None


class Session:
    """Cookies and the form token of the setup wizard, and JSON calls with basic auth."""

    def __init__(self, base: str):
        self.base = check_local(base)
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect)

    def _send(self, req: urllib.request.Request, timeout: float) -> tuple[int, dict, str]:
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode(errors="replace")

    def page(self, path: str, form: dict | None = None, timeout: float = 280) -> tuple[int, str, str]:
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        req = urllib.request.Request(self.base + path, data=data, method="POST" if form is not None else "GET")
        status, headers, html = self._send(req, timeout)
        return status, headers.get("Location", ""), html

    def api(self, method: str, path: str, body=None, auth: tuple[str, str] | None = None, timeout: float = 60,
            accept: str = "application/json") -> tuple[int, object, str]:
        import base64

        headers = {"Content-Type": "application/json", "Accept": accept}
        if auth:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        status, _, text = self._send(req, timeout)
        try:
            return status, (json.loads(text) if text else None), text
        except ValueError:
            return status, None, text


def token_of(html: str) -> str:
    """The wizard's form token (atl_token), found the way the page prints it."""
    m = re.search(r'name="atl_token"[\s\S]{0,120}?value="([^"]+)"', html) or re.search(r'value="([^"]+_lout)"', html)
    return m.group(1) if m else ""


def state(base: str = URL) -> str:
    """FIRST_RUN, STARTING, RUNNING, ERROR ... from /status; DOWN when nothing answers."""
    try:
        with urllib.request.urlopen(check_local(base) + "/status", timeout=10) as r:
            return json.loads(r.read()).get("state", "DOWN")
    except (OSError, ValueError):
        return "DOWN"


def wait_for(want: tuple[str, ...], seconds: float, base: str = URL, sleep: Callable[[float], None] = time.sleep, every: float = 5) -> str:
    waited = 0.0
    while True:
        s = state(base)
        if s in want:
            return s
        if waited >= seconds:
            raise JiraRealError(f"Jira stayed {s} for {int(seconds)} s (wanted {'/'.join(want)}); see `docker logs workbench-jira-real-1`")
        sleep(every)
        waited += every


def wizard(s: Session, licence: str, admin_pw: str, db_pw: str) -> None:
    """The five setup pages, in order. Jira 10's wizard has no built-in database: Postgres on the cluster network."""
    _, _, html = s.page("/secure/SetupDatabase!default.jspa")
    _, _, html = s.page("/secure/SetupDatabase.jspa", {
        "databaseOption": "external", "databaseType": "postgres72", "jdbcHostname": DB_HOST, "jdbcPort": "5432",
        "jdbcDatabase": DB_NAME, "jdbcUsername": DB_USER, "jdbcPassword": db_pw, "schemaName": "public",
        "testingConnection": "false", "atl_token": token_of(html)})
    _, _, html = s.page("/secure/SetupApplicationProperties!default.jspa")
    _, _, html = s.page("/secure/SetupApplicationProperties.jspa", {
        "title": "workbench Jira", "mode": "private", "baseURL": s.base, "nextStep": "true", "atl_token": token_of(html)})
    _, _, html = s.page("/secure/SetupLicense!default.jspa")
    _, _, html = s.page("/secure/SetupLicense.jspa", {"setupLicenseKey": licence, "atl_token": token_of(html), "next": "Next"})
    _, _, html = s.page("/secure/SetupAdminAccount!default.jspa")
    _, _, html = s.page("/secure/SetupAdminAccount.jspa", {
        "fullname": "Workbench Admin", "email": "admin@example.org", "username": "admin", "password": admin_pw,
        "confirm": admin_pw, "next": "Next", "atl_token": token_of(html)})
    _, _, html = s.page("/secure/SetupMailNotifications!default.jspa")
    s.page("/secure/SetupMailNotifications.jspa", {"noemail": "true", "finish": "Finish", "atl_token": token_of(html)})


def _must(what: str, r: tuple[int, object, str]) -> None:
    """A write that may already have happened (a user, a group, a project): only 'exists' is fine."""
    status, _, text = r
    if status in (200, 201, 204) or (status == 400 and re.search(r"already exists|already in use|already a member|uses this project key", text, re.IGNORECASE)):
        return
    raise JiraRealError(f"{what}: {status} {text[:200]}")


def seed(s: Session, admin: tuple[str, str], user_pw: str, say: Callable[[str], None] = print) -> dict[str, str]:
    """Users, a group, project OPS and tickets, then a personal access token for admin and alice. Safe to repeat."""
    _must("group hpc-ops", s.api("POST", "/rest/api/2/group", {"name": "hpc-ops"}, admin))
    for name, display in PEOPLE:
        _must(f"user {name}", s.api("POST", "/rest/api/2/user", {"name": name, "password": user_pw, "emailAddress": f"{name}@example.org", "displayName": display}, admin))
    for name in ("alice", "bob"):
        _must(f"group member {name}", s.api("POST", "/rest/api/2/group/user?groupname=hpc-ops", {"name": name}, admin))
    _must(f"project {PROJECT}", s.api("POST", "/rest/api/2/project", {
        "key": PROJECT, "name": "HPC operations", "projectTypeKey": "software", "lead": "admin",
        "projectTemplateKey": "com.pyxis.greenhopper.jira:basic-software-development-template"}, admin))
    _, have, _ = s.api("POST", "/rest/api/2/search", {"jql": f"project = {PROJECT}", "maxResults": 0}, admin)
    count = (have or {}).get("total", 0) if isinstance(have, dict) else 0
    topics = ["GPU node drained for a driver update", "Scratch filesystem is slow in the evening", "Job array stuck pending on the alpha partition",
              "Replace a failed fan on a beta node", "Quota question from a user"]
    who = ["alice", "bob", "carol", None]
    for i in range(count, TICKETS):
        fields = {"project": {"key": PROJECT}, "issuetype": {"name": "Task"}, "summary": f"{topics[i % len(topics)]} ({i + 1})"}
        if who[i % len(who)]:
            fields["assignee"] = {"name": who[i % len(who)]}
        _must(f"ticket {i + 1}", s.api("POST", "/rest/api/2/issue", {"fields": fields}, admin))
    say(f"{PROJECT}: {max(count, TICKETS)} tickets")
    tokens: dict[str, str] = {}
    for name, password in (("admin", admin[1]), ("alice", user_pw)):
        auth = (name, password)
        _, old, _ = s.api("GET", "/rest/pat/latest/tokens", None, auth)
        for t in old if isinstance(old, list) else []:
            if t.get("name") == "workbench":
                s.api("DELETE", f"/rest/pat/latest/tokens/{t['id']}", None, auth)
        status, made, text = s.api("POST", "/rest/pat/latest/tokens", {"name": "workbench", "expirationDuration": 1}, auth)
        if isinstance(made, dict) and made.get("rawToken"):
            tokens[name] = made["rawToken"]
        else:
            raise JiraRealError(f"token for {name}: {status} {text[:160]}")
    return tokens


def write_tokens(tokens: dict[str, str], env: Path = ENV, base: str = URL) -> None:
    """Add JIRA_URL and PAT_<USER> to the env file (0600); the file's path is shown, never its content."""
    keep = {k: v for k, v in read_env(env).items() if not k.startswith("PAT_") and k != "JIRA_URL"}
    lines = [f"{k}={v}" for k, v in keep.items()] + [f"JIRA_URL={base}"] + [f"PAT_{n.upper()}={t}" for n, t in tokens.items()]
    env.write_text("\n".join(lines) + "\n")
    env.chmod(0o600)


def _credentials(user: str, env: Path) -> tuple[str, str]:
    values = read_env(env)
    password = values.get("JIRA_ADMIN_PASSWORD" if user == "admin" else "JIRA_USER_PASSWORD", "")
    if not password:
        raise JiraRealError("the real Jira is not set up: benchctl start --with jira-real, then benchctl setup jira-real")
    return user, password


def revoke_token(user: str = "alice", base: str = URL, env: Path = ENV) -> int:
    """Delete the user's `workbench` personal access token in Jira: a client still holding it gets a real 401 (the CAPTCHA fault
    does not touch tokens, issue 303). The old value stays in the env file until `reissue_token`. Returns how many were deleted."""
    s, auth, gone = Session(base), _credentials(user, env), 0
    status, tokens, _ = s.api("GET", "/rest/pat/latest/tokens", None, auth)
    if status != 200 or not isinstance(tokens, list):
        raise JiraRealError(f"could not list {user}'s tokens ({status})")
    for t in tokens:
        if t.get("name") == "workbench" and s.api("DELETE", f"/rest/pat/latest/tokens/{t['id']}", None, auth)[0] in (200, 204):
            gone += 1
    return gone


def reissue_token(user: str = "alice", base: str = URL, env: Path = ENV) -> None:
    """Make a new `workbench` token for the user and write it to the env file as PAT_<USER> (0600, never printed)."""
    revoke_token(user, base, env)
    status, made, text = Session(base).api("POST", "/rest/pat/latest/tokens", {"name": "workbench", "expirationDuration": 1}, _credentials(user, env))
    if not (isinstance(made, dict) and made.get("rawToken")):
        raise JiraRealError(f"token for {user}: {status} {text[:160]}")
    values = read_env(env)
    values[f"PAT_{user.upper()}"] = made["rawToken"]
    env.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    env.chmod(0o600)


def provision(base: str = URL, licence: str | None = None, say: Callable[[str], None] = print,
              sleep: Callable[[float], None] = time.sleep, env: Path = ENV, db_env: Path = DB_ENV) -> dict[str, str]:
    """Finish a started Jira: the wizard when it is a first run, then wait for REST, seed, tokens. Returns the tokens."""
    s = Session(base)
    admin_pw = read_env(env).get("JIRA_ADMIN_PASSWORD", "")
    user_pw = read_env(env).get("JIRA_USER_PASSWORD", "")
    if not admin_pw or not read_env(db_env).get("POSTGRES_PASSWORD"):
        raise JiraRealError("the passwords are not made yet: run `benchctl start --with jira-real` first")
    say("waiting for Jira to answer (the first start takes a few minutes)...")
    st = wait_for(("FIRST_RUN", "RUNNING"), 900, base, sleep)
    if st == "FIRST_RUN":
        wizard(s, licence if licence is not None else read_license(), admin_pw, read_env(db_env)["POSTGRES_PASSWORD"])
        sleep(15)
    wait_for(("RUNNING",), 600, base, sleep)  # /status says RUNNING before REST answers: the sign-in below proves it
    for _ in range(60):
        status = s.api("GET", "/rest/api/2/myself", None, ("admin", admin_pw))[0]
        if status == 200:
            break
        if status == 401:
            raise JiraRealError("the administrator password does not match this Jira: `benchctl remove jira-real-state` starts it from nothing")
        sleep(5)
    else:
        raise JiraRealError(f"Jira answers /status but not REST yet ({status}); try again in a minute")
    say("signed in as admin; seeding")
    tokens = seed(s, ("admin", admin_pw), user_pw, say)
    write_tokens(tokens, env, base)
    say(f"done: tokens are in {env} (not printed). The test licence lasts 3 hours; `benchctl remove jira-real-state` starts over.")
    return tokens


def reset(runner=subprocess.run, env: Path = ENV) -> None:
    """Start from nothing: remove the two containers and the two data volumes (the 3-hour licence has run out, or the data is
    unwanted) and forget the access tokens. The passwords stay: the next setup uses them again."""
    runner(["docker", "rm", "-f", "workbench-jira-real-1", "workbench-jira-db-1"], capture_output=True, text=True, check=False)
    runner(["docker", "volume", "rm", "-f", *(f"workbench_{v}" for v in VOLUMES)], capture_output=True, text=True, check=False)
    if env.is_file():
        keep = [f"{k}={v}" for k, v in read_env(env).items() if not k.startswith("PAT_") and k != "JIRA_URL"]
        env.write_text("\n".join(keep) + "\n")


LICENSE_API = "/rest/plugins/applications/1.0/installed/jira-software/license"


def licence_status(base: str = URL, env: Path = ENV) -> dict | None:
    """The installed licence as Jira reports it: {"valid", "expires" (unix seconds)}; None while Jira does not answer."""
    pw = read_env(env).get("JIRA_ADMIN_PASSWORD", "")
    if not pw:
        return None
    try:
        status, data, _ = Session(base).api("GET", LICENSE_API, None, ("admin", pw), timeout=30, accept="*/*")  # the plugin manager answers 406 to application/json
    except (OSError, JiraRealError):
        return None
    if status != 200 or not isinstance(data, dict) or "expiryDate" not in data:
        return None
    return {"valid": bool(data.get("valid")) and not data.get("expired"), "expires": int(data["expiryDate"]) // 1000}


def _when(ts: int) -> str:
    left = max(0, ts - int(time.time())) // 60
    return time.strftime("%H:%M", time.localtime(ts)) + f" (in {left // 60} h {left % 60} min)"


def refresh_licence(base: str = URL, say: Callable[[str], None] = print, runner=subprocess.run,
                    sleep: Callable[[float], None] = time.sleep, env: Path = ENV) -> dict:
    """Give the real Jira another 3 hours. The test key counts from the start of Jira, so a restart is the renewal: users,
    tickets and tokens stay (they are in the data volumes). Waits until Jira answers and returns the new licence status."""
    check_local(base)
    if runner(["docker", "inspect", "-f", "{{.State.Status}}", CONTAINER], capture_output=True, text=True, check=False).returncode:
        raise JiraRealError("the real Jira is not created yet: `benchctl start --with jira-real`, then `benchctl setup jira-real`")
    before = licence_status(base, env)
    say("licence now: " + (f"valid until {_when(before['expires'])}" if before and before["valid"] else "expired or not readable"))
    say("restarting Jira (its data stays); the licence period starts again with the start...")
    r = runner(["docker", "restart", CONTAINER], capture_output=True, text=True, check=False)
    if r.returncode:
        raise JiraRealError(f"could not restart {CONTAINER}: {(r.stderr or r.stdout).strip()[:200]}")
    wait_for(("RUNNING",), 600, base, sleep)
    for _ in range(60):  # /status says RUNNING before REST answers
        now = licence_status(base, env)
        if now:
            break
        sleep(5)
    else:
        raise JiraRealError("Jira is running but its licence cannot be read yet; try `benchctl refresh jira-real-licence` again in a minute")
    if not now["valid"]:
        raise JiraRealError("the licence is still not valid after the restart: the key in compose/generated/secrets/jira.license has a fixed "
                            "end date. Save a fresh published key there and run `benchctl remove jira-real-state` (docs/guides/jira-real.md)")
    say(f"licence valid until {_when(now['expires'])}")
    return now


AUTO_ENV = "WORKBENCH_JIRA_AUTO_REFRESH"  # minutes left at which the page renews the licence; 0 turns the automatic renewal off
AUTO_MIN = 15


def auto_minutes() -> int:
    raw = os.environ.get(AUTO_ENV, "")
    return int(raw) if raw.strip().isdigit() else AUTO_MIN


def needs_refresh(st: dict | None, min_left: int, now: float | None = None) -> bool:
    """True when Jira answers and its licence is expired or has less than `min_left` minutes left (0 = never)."""
    if not st or min_left <= 0:
        return False
    return not st["valid"] or st["expires"] - (time.time() if now is None else now) < min_left * 60


def licence_text(st: dict | None, now: float | None = None) -> str:
    """One line for a row or a status: when the licence ends, or that it cannot be read."""
    if not st:
        return "licence: not readable (Jira is down or not set up)"
    left = int(st["expires"] - (time.time() if now is None else now)) // 60
    if not st["valid"] or left <= 0:
        return "licence: EXPIRED (benchctl refresh jira-real-licence)"
    return f"licence: valid until {time.strftime('%H:%M', time.localtime(st['expires']))} (in {left // 60} h {left % 60} min)"
