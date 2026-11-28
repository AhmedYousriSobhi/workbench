"""Web UI backend (SPEC W-1..W-3, W-5, W-6, W-8).

Runs as an ordinary user: it reads the governor over /run/workbench/gov.sock and plays scenarios in-process
(HTTP targets only; Slurm steps need Docker access, which the UI must not have). Everything needs the login token.
Built on Starlette (FastAPI's base) rather than FastAPI itself: FastAPI's pydantic alone costs ~15 MB of the 50 MB
budget (ADR 0008).
"""
import asyncio
import contextlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from pathlib import Path

import yaml
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route

from benchctl import checks, nhc, scenario, slurmtrace

from . import monitoring, telekube, traffic
from .blast import payload as blast_payload
from .blast import preview as blast_preview
from .blast import step_blast
from .cluster import TELEPORT, ActionError, Cluster, http_json, k8s_built, teleport_built
from .flow import PAUSE_LEVEL, Flow, jump_address, ssh_sources
from .graph import attach as attach_graph
from .kubeaudit import KubeAudit
from .wiki import Wiki, WikiError, esc, page

STATIC = Path(__file__).parent / "static"
# The page and its modules run under a strict policy: no inline script or style (#517, ADR 0047). /help carries it too (its
# stylesheet is static/help.css, #525).
# Opt-in (#983): WORKBENCH_DESKTOP_HELPER=1 lets the page ask one loopback port for a title token (static/js/desktop-helper.js).
HELPER_ORIGIN = "http://127.0.0.1:47800"


def helper_on() -> bool:
    return os.environ.get("WORKBENCH_DESKTOP_HELPER") == "1"


def csp(helper: bool = False) -> str:
    return ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
            f"connect-src 'self'{' ' + HELPER_ORIGIN if helper else ''}; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class CSPMiddleware:
    """Pure ASGI (no buffering, so the SSE feeds stream): add the policy to the page, /static and /help responses."""

    def __init__(self, app, policy: str = csp()):
        self.app = app
        self.policy = policy.encode()

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/" or path.startswith(("/static/", "/help"))):
            return await self.app(scope, receive, send)

        async def send_with_csp(msg):
            if msg["type"] == "http.response.start":
                msg.setdefault("headers", []).append((b"content-security-policy", self.policy))
            await send(msg)

        await self.app(scope, receive, send_with_csp)


_STATIC_TYPES = {".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2"}
COOKIE = "workbench_ui"
CLI_EVENT_MAX = 2048  # bytes in one event from `benchctl scenario run` (ADR 0086 phase 4); a step result is a few hundred
CLI_DETAIL_MAX = 300  # characters of a step's detail kept from the CLI
CLI_QUIET_S = 30.0  # a run from the CLI that posts nothing for this long has ended (the CLI posts at least every 10 s)
DOCS = Path(__file__).resolve().parent.parent / "docs"


SLURM_STATE = {"power-off": ("off",), "power-on": ("on",), "clear": ("on", "resume")}  # page action -> governor node-states in order (off/on freeze or thaw the node; clear thaws it, then resumes it, #845)


def governor_status(socket: str) -> dict:
    """The governor's status, or an {"ok": False, "error": ...} the page shows as 'not reachable'."""
    try:
        return checks.governor_request({"cmd": "status"}, socket)
    except OSError as e:
        return {"ok": False, "error": f"governor not reachable: {e}"}


class SharedStatus:
    """One governor status call per `ttl` seconds however many streams are open (issue 451): the first stream whose tick finds the
    value older than `ttl` fetches it (the others wait for that one call), the rest read it. It runs only when a stream asks,
    so with the page hidden (SPEC W-11) nothing polls."""

    def __init__(self, fetch, ttl: float, clock=time.monotonic):
        self.fetch, self.ttl, self.clock = fetch, ttl, clock
        self.value: dict = {}
        self.at = float("-inf")
        self.lock = asyncio.Lock()

    async def get(self) -> dict:
        if self.clock() - self.at < self.ttl:
            return self.value
        async with self.lock:
            if self.clock() - self.at >= self.ttl:  # another stream may have fetched while this one waited for the lock
                self.value = await asyncio.to_thread(self.fetch)
                self.at = self.clock()
            return self.value


class Runs:
    """At most one scenario at a time; state is what the page polls."""

    def __init__(self, root: Path, sim: dict, flow: Flow | None = None, slurm=None, daemon=None, kube=None, trace=None, clock=time.monotonic, wall=time.time,
                 counters=None):
        self.root, self.sim, self.flow, self.slurm, self.daemon, self.kube, self.trace = root, sim, flow, slurm, daemon, kube, trace
        # `counters()` reads the mocks' request counters now ({totals, errors, reachable}); a run keeps them at Play and at its end, so
        # `benchctl export test-report` can say what the client called during the run, with no browser open (issue 739). None: not kept.
        self.counters = counters
        self.clock, self.wall, self.t0 = clock, wall, 0.0  # t0: the clock at Play; step and expectation times are seconds after it
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.state: dict = {"running": False}
        self.cli: dict | None = None  # the run from the CLI shown now: {"id": its run id, "seen": the clock at its last post}

    def since(self) -> float:
        return round(self.clock() - self.t0, 1)

    def _count(self) -> dict | None:
        try:
            return self.counters() if self.counters else None
        except Exception:  # noqa: BLE001 best effort: a mock that does not answer must never leave a run "running" (the report then says "not measured")
            return None

    def _end(self, read=None, **fields) -> None:
        """A run is over: its end counters (`read`, or read now), then the fields. They are read before `running` goes false, so a finished run always has them."""
        self.state.setdefault("counters", {"base": None, "end": None})["end"] = self._count() if read is None else read
        self.state.update(**fields)

    def snapshot(self) -> dict:
        """What the page polls: the state, with `elapsed` (seconds since Play) counted now while the run plays."""
        with self.lock:
            self._expire()
        return {**self.state, "elapsed": self.since()} if self.state.get("running") else dict(self.state)

    def start(self, name: str, speed: float, trace: bool = False) -> dict:
        try:
            sc = scenario.load(name, self.root, self.sim)
        except scenario.ScenarioError as e:
            raise HTTPException(404, str(e)) from e
        problems = scenario.validate(sc, self.sim)
        if problems:
            raise HTTPException(422, "; ".join(problems))
        if trace and not self.trace:
            raise HTTPException(409, "the trace is read by the governor, which is not reachable or has no --workdir")
        with self.lock:
            self._busy()
            steps = scenario.plan(sc)
            self.stop_flag.clear()
            self._fresh(sc, name, speed, steps, "page", trace)
            threading.Thread(target=self._play, args=(sc, steps, speed, trace), daemon=True).start()
            return {"started": name}

    def _busy(self) -> None:
        """One run at a time (ADR 0086 decision 3): refuse while one plays, here or from the CLI. Call with the lock held."""
        self._expire()
        if self.state.get("running"):
            where = "from the CLI" if self.state.get("source") == "cli" else "on the page"
            raise HTTPException(409, f"{self.state['name']} is already running {where}")

    def _fresh(self, sc: dict, name: str, speed: float, steps: list, source: str, trace: bool = False) -> None:
        """A new run state: each row says where it lands on the diagram (ids, its step number n) and, once applied, when (t), ADR 0086."""
        self.t0 = self.clock()
        plan_expects = [{"e": x["e"], "label": x["label"], "ids": x["ids"], "page": x["page"]} for x in scenario.preview(sc, self.sim)["expects"]]
        self.state = {"running": True, "name": name, "ref": sc.get("ref", ""), "speed": speed, "stopped": False, "ok": None, "started": round(self.wall(), 3), "elapsed": 0.0,
                      "source": source, "counters": {"base": None, "end": None}, "steps": [{"at": s.at, "target": s.target, "do": s.do, "n": s.n, "page": not scenario.page_support(s), "why": scenario.page_support(s), "status": "pending",
                                                   "detail": "", "t": None, **scenario.touch_fields(s, self.sim), **step_blast(s)} for s in steps],
                      "expects": [], "plan_expects": plan_expects, "trace": [] if trace else None}

    # ---- a run from the CLI (ADR 0086 phase 4): `benchctl scenario run` posts its steps here, best effort ----
    # The page builds the plan from its own scenario file and keeps only results from the CLI: a status, a short detail and a time per
    # step or expectation, never a part id or a command. Memory only (decision 4); a CLI that stops posting is dropped after CLI_QUIET_S.
    def _expire(self) -> None:
        if self.cli and self.clock() - self.cli["seen"] > CLI_QUIET_S:
            self._end(running=False, stopped=True, lost=True, elapsed=self.since())
            self.cli = None

    def cli_event(self, ev: dict) -> dict:
        kind = ev.get("event")
        counted = self._count() if kind in ("start", "end") else None  # reads the mocks: not while holding the lock the page's polls need
        with self.lock:
            if kind == "start":
                return self._cli_start(ev, counted)
            if not self.cli or not isinstance(ev.get("run"), str) or not hmac.compare_digest(ev["run"].encode(), self.cli["id"].encode()):
                self._expire()
                raise HTTPException(409, "no such run from the CLI on the page (it ended, was dropped, or another run took its place)")
            self.cli["seen"] = self.clock()
            if kind == "step":
                self._cli_step(ev)
            elif kind == "expect":
                self._cli_expect(ev)
            elif kind == "end":
                ok = ev.get("ok")
                self._end(counted, ok=ok if isinstance(ok, bool) else None, stopped=ev.get("stopped") is True, running=False, elapsed=self.since())
                self.cli = None
            elif kind != "beat":
                raise HTTPException(422, "event must be start, step, expect, beat or end")
            return {"ok": True}

    def _cli_start(self, ev: dict, counted: dict | None) -> dict:
        name, speed, key = ev.get("name"), ev.get("speed"), ev.get("key")
        if not (isinstance(name, str) and 0 < len(name) <= 100 and isinstance(key, str) and len(key) <= 64
                and isinstance(speed, (int, float)) and not isinstance(speed, bool) and 0 < speed <= 1000):
            raise HTTPException(422, "start needs name, speed (0 to 1000) and key")
        self._busy()
        try:
            sc = scenario.load(name, self.root, self.sim)
        except scenario.ScenarioError as e:
            raise HTTPException(404, str(e)) from e
        steps = scenario.plan(sc)
        if scenario.plan_key(steps) != key:
            raise HTTPException(409, "the page reads another copy of this scenario or another cluster (restart the page after changing either)")
        self._fresh(sc, sc["name"], float(speed), steps, "cli")
        self.state["counters"]["base"] = counted
        self.cli = {"id": secrets.token_urlsafe(12), "seen": self.clock()}
        return {"run": self.cli["id"]}

    def _result(self, ev: dict) -> tuple[str, str, float]:
        status, detail, t = ev.get("status"), ev.get("detail", ""), ev.get("t")
        if status not in ("ok", "failed", "skipped") or not isinstance(detail, str) or not isinstance(t, (int, float)) or isinstance(t, bool) or not 0 <= t <= 1e6:
            raise HTTPException(422, "a result needs status (ok, failed or skipped), detail (text) and t (seconds)")
        return status, detail[:CLI_DETAIL_MAX], round(float(t), 1)

    def _cli_step(self, ev: dict) -> None:
        i, rows = ev.get("i"), self.state["steps"]
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(rows):
            raise HTTPException(422, f"i must be a step index from 0 to {len(rows) - 1}")
        status, detail, t = self._result(ev)
        row = rows[i]
        row.update(status=status, detail=detail, t=t)
        if self.flow and status == "ok":  # the same traffic mark a page run makes, from the page's own copy of the step
            do = row["do"]
            self.flow.note(row["target"], f"{row['target']} {json.dumps(do, sort_keys=True)}", do.get("nodes") or do.get("drain") or do.get("resume"))

    def _cli_expect(self, ev: dict) -> None:
        plan = {x["label"]: x for x in self.state["plan_expects"]}
        if ev.get("label") not in plan:
            raise HTTPException(422, "label must be one of the scenario's expectations")
        status, detail, t = self._result(ev)
        p = plan[ev["label"]]
        self.state["expects"] = [x for x in self.state["expects"] if x["label"] != p["label"]]
        self.state["expects"].append({"label": p["label"], "status": status, "detail": detail, "t": t, "e": p["e"], "ids": p["ids"]})

    def _play(self, sc: dict, steps: list, speed: float, trace: bool = False) -> None:
        done = [0]
        self.state["counters"]["base"] = self._count()  # before the first step, outside the lock (it reads the mocks)

        def on_step(step, outcome) -> None:
            row = self.state["steps"][done[0]]
            row.update(status=outcome.status, detail=outcome.detail, t=self.since())
            if self.flow and outcome.status == "ok":
                self.flow.note(step.target, outcome.label, step.do.get("nodes") or step.do.get("drain") or step.do.get("resume"))
            done[0] += 1

        ids = {x["label"]: (x["e"], x["ids"]) for x in self.state["plan_expects"]}

        def on_expect(outcome) -> None:
            e, where = ids.get(outcome.label, (0, []))
            self.state["expects"].append({"label": outcome.label, "status": outcome.status, "detail": outcome.detail, "t": self.since(), "e": e, "ids": where})

        tracer = slurmtrace.Deferred(slurmtrace.nodes_of(sc), self.trace, self.state["trace"].append) if trace else None
        try:
            rep = scenario.run(sc, speed=speed, dilation=float(self.sim.get("time_dilation", 1.0)), say=lambda m: None,
                               host=False, should_stop=self.stop_flag.is_set, on_step=on_step, on_expect=on_expect,
                               slurm=self.slurm, daemon=self.daemon, kube=self.kube, on_before=tracer.before_step if tracer else lambda step: None)
            if tracer:
                tracer.finish()
            self.state.update(ok=rep.ok, stopped=rep.stopped)
        finally:
            self._end(elapsed=self.since(), running=False)

    def stop(self) -> dict:
        if self.state.get("source") == "cli" and self.state.get("running"):
            return {"stopping": False, "why": "a run from the CLI stops with Ctrl+C in its terminal"}
        self.stop_flag.set()
        return {"stopping": bool(self.state.get("running"))}


def login_url(base: str, token: str) -> str:
    """A one-time login link for a running page, minted with the token (tests, shot scripts); the token is never in the URL."""
    import urllib.request
    req = urllib.request.Request(f"{base}/api/login-code", method="POST",
                                 headers={"Authorization": f"Bearer {token}", "X-Requested-With": "workbench"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return f"{base}/login?code={json.load(r)['code']}"


def create_app(token: str, socket: str = checks.GOV_SOCKET, scenarios: Path = Path("scenarios"),
               sim_yaml: Path = Path("sim.yaml"), interval: float = 1.0, docs: Path = DOCS,
               fetch=None, poster=None, nhc_src: Path = nhc.SRC, nhc_out: Path = nhc.OUT, counters=None,
               desktop_helper: bool | None = None) -> Starlette:
    helper = helper_on() if desktop_helper is None else desktop_helper  # read once, at start: a running page keeps its CSP
    sim = yaml.safe_load(sim_yaml.read_text())
    flow = Flow(ssh=None if fetch else ssh_sources, jump_ip=jump_address(),  # a test that passes `fetch` reaches no host counters
                kube=KubeAudit(lambda req: checks.governor_request(req, socket, timeout=2)))

    def live_counters() -> dict:
        flow.poll(force=True)
        return flow.counters()

    if counters is None and not fetch:  # a test that passes `fetch` must not reach the host's mocks: it keeps no counters unless it passes its own
        counters = live_counters
    runs = Runs(scenarios, sim, flow, counters=counters, slurm=lambda n, s, r: _governed(slurm_state(n, s, r)),
                daemon=lambda do: daemon_step(do), kube=lambda do: kube_step(do), trace=lambda nodes, since: slurm_trace(nodes, since))
    cluster = Cluster(sim, kube=lambda do: kube_step(do), **{k: v for k, v in (("fetch", fetch), ("poster", poster)) if v})  # test seams: a host stack must not answer or be hit
    wiki = Wiki(docs)

    def authed(request: Request) -> None:
        got = request.cookies.get(COOKIE) or request.headers.get("authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(got.encode(), token.encode()):
            raise HTTPException(401, "login required: open /login (run ./scripts/benchctl open for a link)")

    def csrf(request: Request) -> None:  # a cross-site form cannot set this header
        if request.headers.get("x-requested-with") != "workbench":
            raise HTTPException(403, "missing X-Requested-With")

    def protected(fn, write: bool = False):
        async def handler(request: Request):
            authed(request)
            if write:
                csrf(request)
            return await fn(request)
        return handler

    codes: dict[str, float] = {}  # one-time login codes -> expiry; the long-lived token never travels in a URL (SEC2, #466)

    def signed_in() -> Response:
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict")
        return resp

    async def login(request: Request):
        """GET /login?code=C signs in with a one-time code (60 s, used up on first use); GET /login alone shows the form
        and POST /login takes the token from the form body, so a browser history entry never holds a working secret."""
        if request.method == "POST":
            form = urllib.parse.parse_qs((await request.body()).decode(errors="replace"))
            if not hmac.compare_digest(form.get("token", [""])[0].encode(), token.encode()):
                raise HTTPException(401, "wrong token")
            return signed_in()
        code = request.query_params.get("code")
        if code is None:
            return FileResponse(STATIC / "login.html")
        now = time.monotonic()
        for k in [k for k, exp in codes.items() if exp < now]:
            del codes[k]
        if codes.pop(code, 0) < now:
            raise HTTPException(401, "login code unknown, used or expired: run ./scripts/benchctl open for a new link")
        return signed_in()

    async def login_code(request: Request):
        """A fresh one-time code, for `benchctl open` and the terminal link; the caller already holds the token."""
        code = secrets.token_urlsafe(16)
        codes[code] = time.monotonic() + 60
        return JSONResponse({"code": code})

    async def index(request: Request):
        return FileResponse(STATIC / "index.html")

    async def static_file(request: Request):
        """The page's stylesheet and ES modules (ADR 0007: native modules, no build step). Same token as the page; only
        .js, .css and .woff2 files under static/, and revalidated on every load so an upgrade is never served stale."""
        path = (STATIC / request.path_params["rel"]).resolve()
        kind = _STATIC_TYPES.get(path.suffix)
        if kind is None or not path.is_file() or STATIC.resolve() not in path.parents:
            raise HTTPException(404, "no such file")
        return FileResponse(path, media_type=kind, headers={"Cache-Control": "no-cache"})

    async def status(request: Request):
        return JSONResponse(await asyncio.to_thread(governor_status, socket))

    shared = SharedStatus(lambda: governor_status(socket), interval * 0.9)  # a lone stream still gets a fresh value every tick

    async def events(request: Request):
        async def stream():
            while True:
                yield f"data: {json.dumps(await shared.get())}\n\n"
                await asyncio.sleep(interval)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    async def flow_events(request: Request):
        """SSE feed of workload events for the dots; the page opens it only while the Cluster tab is showing."""
        raw = request.query_params.get("after")
        try:
            after = int(raw) if raw else flow.seq  # no cursor: only what happens from now on
        except ValueError as e:  # before the stream starts, so the client gets a status code, not a broken stream
            raise HTTPException(422, "after must be a whole number") from e

        async def stream():
            nonlocal after
            while True:
                level = (await shared.get()).get("level") or 0
                paused = level >= PAUSE_LEVEL
                if not paused:
                    await asyncio.to_thread(flow.poll)
                snap = await asyncio.to_thread(flow.since, after)
                after = snap["cursor"]
                yield f"data: {json.dumps({**snap, 'paused': paused, 'level': level})}\n\n"
                await asyncio.sleep(interval)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    async def cluster_state(request: Request):
        snap = await asyncio.to_thread(cluster.snapshot)
        return JSONResponse({**attach_graph(snap), "blast": blast_payload()})

    def node_shapes(saved) -> dict:
        """What one node of each kind is (the sheet shows it next to the counts), and the Kubernetes counts now in effect."""
        from benchctl import k8s

        groups = k8s.node_groups(sim)
        kc = next((g for g in groups if not g.get("gpus")), {})
        kg = next((g for g in groups if g.get("gpus")), {})
        pick = lambda v, g: int(g.get("count", 0)) if v is None else v
        return {"slurm_cpu": {"cpus": sim.get("compute", {}).get("cpus"), "memory_gb": round(sim.get("compute", {}).get("memory_mb", 0) / 1024, 1),
                              "prefix": sim.get("compute", {}).get("prefix", "cn")},
                "slurm_gpu": {"gpu_type": sim.get("gpu", {}).get("type", ""), "prefix": sim.get("gpu", {}).get("prefix", "gpu")},
                "k8s_cpu": {"cpus": kc.get("cpus"), "memory_gb": kc.get("memory_gb"), "prefix": kc.get("prefix", "kcn"), "count": pick(saved.k8s_cpu_nodes if saved else None, kc)},
                "k8s_gpu": {"cpus": kg.get("cpus"), "memory_gb": kg.get("memory_gb"), "gpus": kg.get("gpus"), "gpu_type": kg.get("gpu_type", ""),
                            "prefix": kg.get("prefix", "kgpu"), "count": pick(saved.k8s_nodes if saved else None, kg)},
                "bmc": {"groups": saved.bmc_groups if saved else None, "nodes": saved.bmc_nodes if saved else None},
                "controller": {"enabled": saved.controller if saved and saved.controller is not None else bool((sim.get("controller") or {}).get("enabled"))},
                "bcm": {"manage_kubernetes": saved.bcm_k8s if saved and saved.bcm_k8s is not None else bool((sim.get("bcm") or {}).get("manage_kubernetes", True))}}

    def build_plan(q) -> tuple:
        """Blocking (file reads, a governor call): run it with asyncio.to_thread."""
        from benchctl import workbench

        try:
            spec = workbench.from_dict({k: int(q[k]) for k in ("cpu_nodes", "gpu_nodes", "gpus_per_node", "k8s_nodes", "k8s_cpu_nodes", "dcgm_nodes") if k in q}
                                       | ({"parts": [p for p in q["parts"].split(",") if p]} if "parts" in q else {})
                                       | ({"bmc_nodes": [n for n in q["bmc_nodes"].split(",") if n]} if "bmc_nodes" in q else {})
                                       | ({"bmc_groups": [g for g in q["bmc_groups"].split(",") if g]} if "bmc_groups" in q else {})
                                       | ({"bcm_k8s": q["bcm_k8s"] == "1"} if "bcm_k8s" in q else {})
                                       | ({"controller": q["controller"] == "1"} if "controller" in q else {})
                                       | ({"login_nodes": [m for m in q["logins"].split(",") if m]} if "logins" in q else {}),
                                       workbench.defaults(sim))
        except (ValueError, workbench.SpecError) as e:
            raise HTTPException(422, str(e)) from e
        if problems := spec.problems():
            raise HTTPException(422, "; ".join(problems))
        est = workbench.estimate(*workbench.effective(sim, spec), app_mb=0, running=set(governor_status(socket).get("services") or []))  # the page is open next to the app: its memory is already out of the free figure
        avail = checks.mem_available() // (1024 * 1024)
        ok, msg = workbench.verdict(est, avail, spec=spec)
        return spec, est, avail, ok, msg

    async def settings(request: Request):
        """The options the Cluster architecture sheet offers, with what each costs; `on` follows the workbench last built."""
        from benchctl import compose, export, workbench

        try:
            _, services, _ = export.configured(yaml.safe_load(sim_yaml.read_text()))
            built = workbench.parts_in(services)
        except (export.ExportError, OSError, ValueError):
            services, built = set(), None  # nothing built to read: offer the defaults
        nodes = [{"name": n, "kind": k, "bmc": f"bmc-{n}" in services if "bmc-farm" in services else True}
                 for n, k in cluster.nodes]
        from benchctl import logins

        try:
            saved = workbench.current(sim)  # the workbench last built, else sim.yaml's `login:`
        except workbench.SpecError:
            saved = None
        return JSONResponse({"parts": workbench.catalog(built),
                             "logins": {"modes": list(logins.MODES), "kinds": list(logins.KINDS), "max": logins.MAX_LOGINS,
                                        "now": saved.login_nodes if saved else logins.modes(sim)},
                             "shapes": node_shapes(saved),
                             "nodes": nodes, "bmc_node_mb": compose.BMC_FORWARDER_MB,
                             "limits": {k: list(v) for k, v in workbench.LIMITS.items()}})

    async def plan(request: Request):
        """Estimate only, no side effects: the page cannot start containers. Applying goes through the governor."""
        spec, est, avail, ok, msg = await asyncio.to_thread(build_plan, request.query_params)
        from benchctl import workbench

        flags = f"--cpu-nodes {spec.cpu_nodes} --gpu-nodes {spec.gpu_nodes} --gpus-per-node {spec.gpus_per_node}" + "".join(
            f" --with {p}" for p in sorted(spec.parts)) + "".join(f" --without {p}" for p in workbench.PARTS if p not in spec.parts)
        if spec.bmc_nodes is not None:
            flags += f" --bmc-nodes {','.join(spec.bmc_nodes)}"
        flags += "".join(f" {f} {getattr(spec, k)}" for k, f in (("k8s_nodes", "--k8s-nodes"), ("k8s_cpu_nodes", "--k8s-cpu-nodes"), ("dcgm_nodes", "--dcgm-nodes")) if getattr(spec, k) is not None)
        if spec.bmc_groups is not None:
            flags += f" --bmc-groups {','.join(spec.bmc_groups)}"
        if spec.controller is not None:
            flags += " --controller" if spec.controller else " --no-controller"
        if spec.bcm_k8s is not None:
            flags += " --bcm-k8s" if spec.bcm_k8s else " --no-bcm-k8s"
        flags += f" --login {','.join(spec.login_nodes)} --app-mb 0"
        return JSONResponse({"fits": ok, "message": msg, "total_mb": est["total_mb"], "need_mb": est["need_mb"], "expected_mb": est["expected_mb"],
                             "running_mb": est["running_mb"], "available_mb": avail, "floor_mb": workbench.FLOOR_MB,
                             "budget_mb": avail - workbench.FLOOR_MB, "headroom_mb": avail - workbench.FLOOR_MB - est["need_mb"],
                             "breakdown": workbench.breakdown(est),
                             "text": workbench.render(spec, est), "command": f"benchctl up {flags}"})

    async def jira_refresh(request: Request):
        """The Refresh licence button: ask the governor to restart the real Jira (its test licence counts from its start)."""
        try:
            res = await asyncio.to_thread(checks.governor_request, {"cmd": "refresh-jira-licence"}, socket)
        except (OSError, ValueError) as e:
            raise HTTPException(503, "the governor is not reachable: start it with ./scripts/benchctl start") from e
        if not res.get("ok"):
            raise HTTPException(409, res.get("error", "the governor refused"))
        if res.get("dry_run"):
            return JSONResponse({"started": False, "message": "The governor is in dry-run mode or has no --workdir. Run: benchctl refresh jira-real-licence"})
        return JSONResponse({"started": True, "message": "Restarting the real Jira: ready in about a minute with 3 more hours. Progress is in Governor activity."})

    async def plan_apply(request: Request):
        """Ask the governor to rebuild the workbench to this plan (it runs `benchctl up` as the user, calm levels only)."""
        spec, _, _, ok, msg = await asyncio.to_thread(build_plan, request.query_params)
        if not ok:
            raise HTTPException(422, "does not fit: " + msg)
        req = {"cmd": "apply-plan", "cpu_nodes": spec.cpu_nodes, "gpu_nodes": spec.gpu_nodes,
               "gpus_per_node": spec.gpus_per_node, "parts": sorted(spec.parts)}
        if spec.bmc_nodes is not None:
            req["bmc_nodes"] = spec.bmc_nodes
        req["login_nodes"] = spec.login_nodes
        if spec.bcm_k8s is not None:
            req["bcm_k8s"] = spec.bcm_k8s
        if spec.controller is not None:
            req["controller"] = spec.controller
        if spec.bmc_groups is not None:
            req["bmc_groups"] = spec.bmc_groups
        req.update({k: getattr(spec, k) for k in ("k8s_nodes", "k8s_cpu_nodes", "dcgm_nodes") if getattr(spec, k) is not None})
        try:
            res = await asyncio.to_thread(checks.governor_request, req, socket)
        except (OSError, ValueError) as e:
            raise HTTPException(503, "the governor is not reachable: start it with ./scripts/benchctl start") from e
        if not res.get("ok"):
            raise HTTPException(409, res.get("error", "the governor refused"))
        if res.get("dry_run"):
            return JSONResponse({"started": False, "message": "The governor is in dry-run mode or has no --workdir, so nothing was applied. Run the command shown instead."})
        return JSONResponse({"started": True, "job": res.get("job"), "message": "Applying. Nodes appear and disappear as they change; the stages are listed below."})

    async def nhc_state(request: Request):
        """The health checks in run order, the templates the page may add from, and any problem with the files."""
        return JSONResponse(await asyncio.to_thread(nhc.state, nhc_src))

    async def nhc_change(request: Request):
        """One change to the checks, validated like the CLI's (same module): reorder, toggle, params, remove, add from a
        template. Custom scripts are not accepted from the page (a CLI or file edit); a refusal changes nothing."""
        try:
            body = await request.json()
        except ValueError as e:
            raise HTTPException(400, "send JSON") from e
        op, name = body.get("op"), str(body.get("name", ""))
        params = {str(k): str(v) for k, v in (body.get("params") or {}).items()}
        position = body.get("position")

        def apply() -> None:
            if op == "reorder":
                nhc.reorder([str(n) for n in body.get("order", [])], nhc_src)
            elif op == "toggle":
                nhc.set_check(name, enabled=bool(body.get("enabled")), src=nhc_src)
            elif op == "params":
                nhc.set_check(name, params=params, src=nhc_src)
            elif op == "remove":
                nhc.remove(name, nhc_src)
            elif op == "add":
                nhc.add(name, template=str(body.get("template", "")), params=params,
                        position=position if isinstance(position, int) else None, src=nhc_src)
            else:
                raise nhc.NhcError("op must be reorder, toggle, params, remove or add")
            if errs := nhc.sync(nhc_src, nhc_out):
                raise nhc.NhcError("\n".join(errs))

        try:
            await asyncio.to_thread(apply)
        except nhc.NhcError as e:
            raise HTTPException(422, str(e)) from e
        return JSONResponse(await asyncio.to_thread(nhc.state, nhc_src))

    async def node_detail(request: Request):
        from benchctl import describe

        try:
            info = await asyncio.to_thread(describe.gather, cluster.live_sim(), request.path_params["node"], cluster.fetch)
        except describe.UnknownNode as e:
            raise HTTPException(404, str(e.args[0])) from e
        from benchctl import addressing, export

        node, plan = request.path_params["node"], addressing.plan(cluster.live_sim())
        try:
            _, services, _ = export.configured(yaml.safe_load(sim_yaml.read_text()))
        except (export.ExportError, OSError, ValueError):
            services = set()
        return JSONResponse({"info": info, "text": describe.render(info), "ip": plan["nodes"].get(node),
                             "bmc_ip": plan["bmc"].get(node) if f"bmc-{node}" in services else None})

    async def node_tickets(request: Request):
        try:
            found = await asyncio.to_thread(cluster.tickets, request.path_params["node"])
        except KeyError as e:
            raise HTTPException(404, f"unknown node {e.args[0]}") from e
        return JSONResponse({"source": "jira-mock", "tickets": found})

    licence = {"text": "", "asked": 0.0}  # the real Jira's licence as the watcher last saw it, and when it last asked for a renewal

    @contextlib.asynccontextmanager
    async def lifespan(app):
        from webui import licence as licence_mod

        task = asyncio.create_task(licence_mod.watch(licence, socket))
        try:
            yield
        finally:
            task.cancel()

    async def lab_list(request: Request):
        from webui import lab as labview

        return JSONResponse({"labs": [labview.summary(n) for n in await asyncio.to_thread(labview.names)]})

    async def lab_detail(request: Request):
        from webui import lab as labview

        gov = (await asyncio.to_thread(governor_status, socket)).get("lab")
        try:
            return JSONResponse(await asyncio.to_thread(labview.view, request.path_params["name"], yaml.safe_load(sim_yaml.read_text()), gov))
        except (labview.LabRequestError, labview.lab.LabError) as e:
            raise HTTPException(404, str(e)) from e

    async def lab_report(request: Request):
        """One run's report as a download (`format=md|json`); `anonymize=1` maps node and user names (issue 614)."""
        from webui import lab as labview

        q = request.query_params
        try:
            data, md = await asyncio.to_thread(labview.report, request.path_params["name"], q.get("run", "last"), q.get("anonymize") == "1")
        except (labview.LabRequestError, labview.lab.LabError) as e:
            raise HTTPException(404, str(e)) from e
        if q.get("format") == "json":
            return JSONResponse(data)
        return Response(md, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="lab-report.md"'})

    async def lab_act(request: Request):
        """A lab button: the governor runs it (L0-L1, admitted); this page never touches Docker (W-3). One message back."""
        from webui import lab as labview

        try:
            body = await request.json()
        except ValueError:
            body = {}
        try:
            req = labview.request(request.path_params["name"], request.path_params["action"], body if isinstance(body, dict) else {})
        except (labview.LabRequestError, labview.lab.LabError) as e:
            raise HTTPException(422, str(e)) from e
        try:
            res = await asyncio.to_thread(checks.governor_request, req, socket)
        except (OSError, ValueError) as e:
            raise HTTPException(503, "the governor is not reachable: start it with ./scripts/benchctl start") from e
        if not res.get("ok"):
            raise HTTPException(409, res.get("error", "the governor refused"))
        if res.get("dry_run"):
            return JSONResponse({"started": False, "message": "The governor is in dry-run mode, so nothing was started."})
        return JSONResponse({"started": True, "message": f"Sent: {req['cmd']} {req['name']}. Watch the status chip."})

    async def connections(request: Request):
        from webui import connections as conn

        rows = conn.entities(cluster.sim)
        for e in rows:
            if e["id"] == "jira-real" and licence["text"]:
                e["status"] = licence["text"]
            if e["id"] == "ui" and helper:
                e["status"] = f"Desktop helper switch on: the page may ask {HELPER_ORIGIN} for a title token (issue 983)."
        return JSONResponse({"entities": rows})

    async def desktop_helper_state(request: Request):
        """Whether this page started with WORKBENCH_DESKTOP_HELPER=1, so the title script asks the helper only when the CSP allows it."""
        return JSONResponse({"on": helper})

    async def export_profile(request: Request):
        """The client profile file for the cluster on disk now. Read fresh per request (the page has no Docker, W-3),
        so it follows a rebuild; the format ID comes from the local file, never from the repository."""
        from benchctl import export

        q = request.query_params
        try:
            base = yaml.safe_load(sim_yaml.read_text())
            sim, services, label = export.configured(base)
            body = export.build(sim, services, "workbench", q.get("bmc") == "1", q.get("user") or None,
                                bmc_via=q.get("bmc_via", "ip"), loopback=q.get("addr") == "loopback")
            text = export.to_json(body, export.format_id())
        except export.ExportError as e:
            raise HTTPException(422, str(e)) from e
        return Response(text, media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="bench-profile.json"', "X-Profile": label,
                                 "X-Warning": " ".join(export.warnings(services, sim))})

    def _governed(row: dict) -> tuple[bool, str]:
        """(done, why not) from a slurm_state row, for the scenario engine."""
        return row["status"] == "ok", row["detail"]

    def slurm_state(node: str, state: str, reason: str | None = None) -> dict:
        """Slurm is told through the governor (the page has no Docker, W-3); a refusal is reported, never fatal."""
        try:
            res = checks.governor_request({"cmd": "node-state", "node": node, "state": state,
                                           **({"reason": re.sub(r"[^A-Za-z0-9 .,:_/()+-]", "", reason)[:60]} if reason else {})}, socket)
        except (OSError, ValueError):
            return {"target": "slurm", "status": "skipped", "detail": "the governor is not reachable"}
        if not res.get("ok"):
            return {"target": "slurm", "status": "skipped", "detail": res.get("error", "refused")}
        if res.get("dry_run"):
            return {"target": "slurm", "status": "skipped", "detail": "the governor is in dry-run mode or has no --workdir"}
        return {"target": "slurm", "status": "ok", "detail": f"asked the governor to set {node} {state}"}

    def kube_step(do: dict) -> tuple[bool, str]:
        """A scenario's Kubernetes step, asked of the governor (the page has no Docker, W-3); (done, why not). One message when the part is off."""
        if not k8s_built():
            return False, "the Kubernetes part is not built in this cluster (benchctl up --with k8s)"
        try:
            res = checks.governor_request({"cmd": "k8s-step", "do": do}, socket)
        except (OSError, ValueError):
            return False, "the governor is not reachable"
        if not res.get("ok"):
            return False, res.get("error", "refused")
        if res.get("dry_run"):
            return False, "the governor is in dry-run mode or has no --workdir"
        return True, "asked the governor"

    def daemon_step(do: dict) -> tuple[bool, str]:
        """The job and daemon steps of a scenario, asked of the governor (the page has no Docker, W-3); (done, why not)."""
        reqs = [{"cmd": "slurm-step", "name": f"slurmd-{a}", "node": n} for a in ("stop", "restart", "start") for n in do.get(f"slurmd_{a}", [])]
        reqs += [{"cmd": "slurm-step", "name": "slurmctld-restart"}] if do.get("slurmctld_restart") else []
        if isinstance(do.get("submit"), dict):
            sub = do["submit"]
            reqs.append({"cmd": "submit-job", "node": sub["node"], "gpus": int(sub.get("gpus", 0)), "duration": int(sub.get("duration", 600)),
                         **({"user": sub["user"]} if sub.get("user") else {})})
        reqs += [{"cmd": "slurm-step", "name": "cancel-jobs"}] if do.get("cancel") else []
        for r in reqs:
            try:
                res = checks.governor_request(r, socket)
            except (OSError, ValueError):
                return False, "the governor is not reachable"
            if not res.get("ok"):
                return False, res.get("error", "refused")
            if res.get("dry_run"):
                return False, "the governor is in dry-run mode or has no --workdir"
        return True, "asked the governor"

    def slurm_trace(nodes: list[str], since: float) -> dict | None:
        try:
            res = checks.governor_request({"cmd": "slurm-trace", "nodes": nodes, "since": since}, socket)
        except (OSError, ValueError):
            return None
        return res.get("trace") if res.get("ok") else None

    async def teleport_kube(request: Request):
        """Teleport's Kubernetes request counters and the last kube.request audit events (issue 755); hidden unless both parts are built."""
        data = await asyncio.to_thread(lambda: telekube.collect(teleport_built() and k8s_built(), lambda u: http_json(u, timeout=1.5), TELEPORT))
        return JSONResponse(data)

    slurm_stats_cache: list = [float("-inf"), None]

    async def slurm_stats(request: Request):
        """slurmctld's own request counts (sdiag) and the Slurm node states (sinfo), through the governor; cached 4 s so many tabs cost one exec."""
        if time.monotonic() - slurm_stats_cache[0] > 4:
            try:
                res = await asyncio.to_thread(checks.governor_request, {"cmd": "slurm-stats"}, socket)
            except (OSError, ValueError):
                res = {}
            slurm_stats_cache[:] = [time.monotonic(), res.get("stats") if res.get("ok") else None]
        return JSONResponse({"stats": slurm_stats_cache[1]})

    async def traffic_record(request: Request):
        """Who called which mock, how it ended and how long it took (the mocks' own record, GET /sim/traffic); `?since=T` limits `recent`."""
        try:
            since = max(0.0, float(request.query_params.get("since") or 0))
        except ValueError:
            return JSONResponse({"error": "since must be a number"}, status_code=400)
        return JSONResponse(await asyncio.to_thread(traffic.collect, since))

    async def ticket_bot(request: Request):
        """GET: the settings the running ticket bot was last given. POST: change them (the governor runs `benchctl set ticket-bot`, issue 728)."""
        body = {}
        if request.method == "POST":
            try:
                body = await request.json()
            except ValueError as e:
                raise HTTPException(422, "send a JSON object") from e
            if not isinstance(body, dict):
                raise HTTPException(422, "send a JSON object")
        req = {"cmd": "ticket-bot-set", **{k: body[k] for k in ("every", "resolve", "ignore", "reopen") if k in body}} if request.method == "POST" else {"cmd": "ticket-bot-get"}
        try:
            res = await asyncio.to_thread(checks.governor_request, req, socket)
        except (OSError, ValueError) as e:
            raise HTTPException(503, "the governor is not reachable: start it with ./scripts/benchctl start") from e
        if not res.get("ok"):
            raise HTTPException(409, res.get("error", "the governor refused"))
        if res.get("dry_run"):
            return JSONResponse({"started": False, "message": "The governor is in dry-run mode, so nothing was changed. Run: ./scripts/benchctl set ticket-bot ..."})
        return JSONResponse(res if request.method == "GET" else {"started": True, "message": "Sent: the bot applies it within one cycle."})

    async def capture_call(req: dict) -> JSONResponse:
        """One wire's messages, on demand and only through the governor (issue 505): it starts, reads and stops the capture; this page never touches Docker."""
        try:
            res = await asyncio.to_thread(checks.governor_request, req, socket)
        except (OSError, ValueError) as e:
            raise HTTPException(503, "the governor is not reachable: start it with ./scripts/benchctl start") from e
        if not res.get("ok"):
            raise HTTPException(409, res.get("error", "the governor refused"))
        return JSONResponse(res)

    async def capture_start(request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        node = body.get("node") if isinstance(body, dict) else None
        if not (isinstance(node, str) and re.fullmatch(r"[a-z]{2,8}[0-9]{3}", node)):
            raise HTTPException(400, "node must be a node name such as cn001")
        return await capture_call({"cmd": "capture-start", "node": node})

    async def capture_read(request: Request):
        try:
            since = max(0, int(request.query_params.get("since") or 0))
        except ValueError:
            raise HTTPException(400, "since must be a whole number") from None
        return await capture_call({"cmd": "capture-read", "since": since})

    async def capture_stop(request: Request):
        return await capture_call({"cmd": "capture-stop"})

    monitoring_cache: list = [float("-inf"), []]

    async def monitoring_links(request: Request):
        """Prometheus's own scrape results as Traffic links; cached 10 s, empty while Prometheus does not answer."""
        if time.monotonic() - monitoring_cache[0] > 10:
            monitoring_cache[:] = [time.monotonic(), await asyncio.to_thread(monitoring.links)]
        return JSONResponse({"links": monitoring_cache[1]})

    async def node_workload(request: Request):
        """Submit a job on this node: the governor runs `benchctl submit` (the page has no Docker, W-3)."""
        q, node = request.query_params, request.path_params["node"]
        try:
            gpus, duration = int(q.get("gpus", "0")), int(q.get("duration", "300"))
        except ValueError as e:
            raise HTTPException(422, "gpus and duration must be whole numbers") from e
        if node not in {n["name"] for n in (await asyncio.to_thread(cluster.snapshot))["nodes"]}:
            raise HTTPException(404, f"unknown node {node}")
        try:
            res = await asyncio.to_thread(checks.governor_request, {"cmd": "submit-job", "node": node, "gpus": gpus, "duration": duration}, socket)
        except (OSError, ValueError) as e:
            raise HTTPException(503, "the governor is not reachable: start it with ./scripts/benchctl start") from e
        if not res.get("ok"):
            raise HTTPException(409, res.get("error", "the governor refused"))
        if res.get("dry_run"):
            return JSONResponse({"started": False, "message": f"The governor is in dry-run mode or has no --workdir, so nothing was submitted. Run: ./scripts/benchctl submit {node} --gpus {gpus} --duration {duration}"})
        flow.note("slurm", f"job submitted on {node}", [node])
        return JSONResponse({"started": True, "message": f"Job submitted on {node} for {duration // 60} min: squeue shows it in a few seconds."})

    async def node_action(request: Request):
        try:
            results = await asyncio.to_thread(cluster.act, request.path_params["node"], request.path_params["action"])
        except ActionError as e:
            raise HTTPException(422, str(e)) from e
        states = () if cluster.hardware().get(request.path_params["node"], "").startswith("k8s") else SLURM_STATE.get(request.path_params["action"], ())  # a Kubernetes node is no Slurm node
        if states and any(r["status"] == "ok" for r in results):  # the BMC changed; Slurm only knows through the governor
            for state in states:
                results.append(await asyncio.to_thread(slurm_state, request.path_params["node"], state))
        for r in results:
            if r["status"] == "ok":
                flow.note(r["target"], f"{request.path_params['action']} on {request.path_params['node']}", [request.path_params["node"]])
        return JSONResponse({"results": results})

    async def list_scenarios(request: Request):
        return JSONResponse([{"name": s["name"], "description": s.get("description", ""), "steps": len(s.get("steps", [])),
                              "targets": sorted({x["target"] for x in s.get("steps", [])})}
                             for s in scenario.list_all(scenarios)])

    async def scenario_preview(request: Request):
        try:
            sc = scenario.load(request.path_params["name"], scenarios, sim)
        except scenario.ScenarioError as e:
            raise HTTPException(404, str(e)) from e
        return JSONResponse(blast_preview(sc, sim))  # with each step's declared blast (ADR 0086 phase 2)

    async def run_state(request: Request):
        return JSONResponse(runs.snapshot())

    async def stop(request: Request):
        return JSONResponse(runs.stop())

    async def cli_event(request: Request):
        """One event of a run from the CLI (ADR 0086 phase 4): JSON, at most CLI_EVENT_MAX bytes, read in pieces so a larger body is never held."""
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > CLI_EVENT_MAX:
                raise HTTPException(413, f"an event is at most {CLI_EVENT_MAX} bytes")
        try:
            ev = json.loads(body)
        except ValueError as e:
            raise HTTPException(422, "the body must be JSON") from e
        if not isinstance(ev, dict):
            raise HTTPException(422, "the body must be a JSON object")
        return JSONResponse(await asyncio.to_thread(runs.cli_event, ev))  # a start and an end read the mocks' counters

    async def run(request: Request):
        try:
            speed = float(request.query_params.get("speed", "1"))
        except ValueError:
            speed = 0.0
        if not 0.1 <= speed <= 1000:
            raise HTTPException(422, "speed must be between 0.1 and 1000")
        return JSONResponse(runs.start(request.path_params["name"], speed, request.query_params.get("trace") == "1"))

    async def help_home(request: Request):
        return RedirectResponse("/help/manual/README", status_code=303)

    async def help_page(request: Request):
        slug = request.path_params["slug"]
        try:
            title, body = await asyncio.to_thread(wiki.render, slug)
        except WikiError as e:
            raise HTTPException(404, f"no help page {slug!r}") from e
        return HTMLResponse(page(wiki, title, body, current=slug))

    async def help_search(request: Request):
        q = request.query_params.get("q", "")[:100]
        hits = await asyncio.to_thread(wiki.search, q)
        rows = "".join(f'<div class="hit"><a href="/help/{esc(h["slug"])}">{esc(h["title"])}</a><p>…{esc(h["snippet"])}…</p></div>'
                       for h in hits)
        body = f"<h1>Search: {esc(q)}</h1>" + (rows or "<p>No page matches. Try fewer or different words.</p>")
        return HTMLResponse(page(wiki, "Search", body, query=q))

    async def help_file(request: Request):
        try:
            return FileResponse(wiki.file_path(request.path_params["rel"]))
        except WikiError as e:
            raise HTTPException(404, "no such file") from e

    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return Starlette(
        lifespan=lifespan,
        middleware=[Middleware(CSPMiddleware, policy=csp(helper))],
        routes=[
            Route("/login", login, methods=["GET", "POST"]),
            Route("/api/login-code", protected(login_code, write=True), methods=["POST"]),
            Route("/", protected(index)),
            Route("/static/{rel:path}", protected(static_file)),
            Route("/help", protected(help_home)),
            Route("/help/", protected(help_home)),
            Route("/help/search", protected(help_search)),
            Route("/help/files/{rel:path}", protected(help_file)),
            Route("/help/{slug:path}", protected(help_page)),
            Route("/api/traffic", protected(traffic_record)),
            Route("/api/ticket-bot", protected(ticket_bot)),
            Route("/api/ticket-bot", protected(ticket_bot, write=True), methods=["POST"]),
            Route("/api/capture/start", protected(capture_start, write=True), methods=["POST"]),
            Route("/api/capture/read", protected(capture_read)),
            Route("/api/capture/stop", protected(capture_stop, write=True), methods=["POST"]),
            Route("/api/status", protected(status)),
            Route("/api/events", protected(events)),
            Route("/api/cluster", protected(cluster_state)),
            Route("/api/flow", protected(flow_events)),
            Route("/api/slurm-stats", protected(slurm_stats)),
            Route("/api/teleport-kube", protected(teleport_kube)),
            Route("/api/monitoring", protected(monitoring_links)),
            Route("/api/settings", protected(settings)),
            Route("/api/plan", protected(plan)),
            Route("/api/plan/apply", protected(plan_apply, write=True), methods=["POST"]),
            Route("/api/jira-real/refresh", protected(jira_refresh, write=True), methods=["POST"]),
            Route("/api/nhc", protected(nhc_state)),
            Route("/api/nhc", protected(nhc_change, write=True), methods=["POST"]),
            Route("/api/lab", protected(lab_list)),
            Route("/api/lab/{name}", protected(lab_detail)),
            Route("/api/lab/{name}/report", protected(lab_report)),
            Route("/api/lab/{name}/{action}", protected(lab_act, write=True), methods=["POST"]),
            Route("/api/connections", protected(connections)),
            Route("/api/desktop-helper", protected(desktop_helper_state)),
            Route("/api/export/profile", protected(export_profile)),
            Route("/api/nodes/{node}", protected(node_detail)),
            Route("/api/nodes/{node}/tickets", protected(node_tickets)),
            Route("/api/nodes/{node}/workload", protected(node_workload, write=True), methods=["POST"]),
            Route("/api/nodes/{node}/{action}", protected(node_action, write=True), methods=["POST"]),
            Route("/api/scenarios", protected(list_scenarios)),
            Route("/api/scenarios/run", protected(run_state)),
            Route("/api/scenarios/{name}", protected(scenario_preview)),
            Route("/api/scenarios/stop", protected(stop, write=True), methods=["POST"]),
            Route("/api/scenarios/cli", protected(cli_event, write=True), methods=["POST"]),
            Route("/api/scenarios/{name}/run", protected(run, write=True), methods=["POST"]),
        ],
        exception_handlers={HTTPException: http_error},
    )
