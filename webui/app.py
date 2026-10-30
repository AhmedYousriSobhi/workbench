"""Web UI backend (SPEC W-1..W-3, W-5, W-6, W-8).

Runs as an ordinary user: it reads the governor over /run/workbench/gov.sock and plays scenarios in-process
(HTTP targets only; Slurm steps need Docker access, which the UI must not have). Everything needs the login token.
Built on Starlette (FastAPI's base) rather than FastAPI itself: FastAPI's pydantic alone costs ~15 MB of the 50 MB
budget (ADR 0008).
"""
import asyncio
import hmac
import json
import threading
from pathlib import Path

import yaml
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from starlette.routing import Route

from benchctl import checks, scenario

from .cluster import ActionError, Cluster
from .flow import PAUSE_LEVEL, Flow
from .wiki import Wiki, WikiError, esc, page

STATIC = Path(__file__).parent / "static"
COOKIE = "workbench_ui"
DOCS = Path(__file__).resolve().parent.parent / "docs"


def governor_status(socket: str) -> dict:
    """The governor's status, or an {"ok": False, "error": ...} the page shows as 'not reachable'."""
    try:
        return checks.governor_request({"cmd": "status"}, socket)
    except OSError as e:
        return {"ok": False, "error": f"governor not reachable: {e}"}


class Runs:
    """At most one scenario at a time; state is what the page polls."""

    def __init__(self, root: Path, sim: dict, flow: Flow | None = None):
        self.root, self.sim, self.flow = root, sim, flow
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.state: dict = {"running": False}

    def start(self, name: str, speed: float) -> dict:
        try:
            sc = scenario.load(name, self.root)
        except scenario.ScenarioError as e:
            raise HTTPException(404, str(e)) from e
        problems = scenario.validate(sc, self.sim)
        if problems:
            raise HTTPException(422, "; ".join(problems))
        with self.lock:
            if self.state.get("running"):
                raise HTTPException(409, f"{self.state['name']} is already running")
            steps = scenario.plan(sc)
            self.stop_flag.clear()
            self.state = {"running": True, "name": name, "speed": speed, "stopped": False, "ok": None,
                          "steps": [{"at": s.at, "target": s.target, "do": s.do, "status": "pending", "detail": ""}
                                    for s in steps], "expects": []}
            threading.Thread(target=self._play, args=(sc, steps, speed), daemon=True).start()
            return {"started": name}

    def _play(self, sc: dict, steps: list, speed: float) -> None:
        done = [0]

        def on_step(step, outcome) -> None:
            row = self.state["steps"][done[0]]
            row.update(status=outcome.status, detail=outcome.detail)
            if self.flow and outcome.status == "ok":
                self.flow.note(step.target, outcome.label)
            done[0] += 1

        def on_expect(outcome) -> None:
            self.state["expects"].append({"label": outcome.label, "status": outcome.status, "detail": outcome.detail})

        try:
            rep = scenario.run(sc, speed=speed, dilation=float(self.sim.get("time_dilation", 1.0)), say=lambda m: None,
                               host=False, should_stop=self.stop_flag.is_set, on_step=on_step, on_expect=on_expect)
            self.state.update(ok=rep.ok, stopped=rep.stopped)
        finally:
            self.state["running"] = False

    def stop(self) -> dict:
        self.stop_flag.set()
        return {"stopping": bool(self.state.get("running"))}


def create_app(token: str, socket: str = checks.GOV_SOCKET, scenarios: Path = Path("scenarios"),
               sim_yaml: Path = Path("sim.yaml"), interval: float = 1.0, docs: Path = DOCS) -> Starlette:
    sim = yaml.safe_load(sim_yaml.read_text())
    flow = Flow()
    runs = Runs(scenarios, sim, flow)
    cluster = Cluster(sim)
    wiki = Wiki(docs)

    def authed(request: Request) -> None:
        got = request.cookies.get(COOKIE) or request.headers.get("authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(got.encode(), token.encode()):
            raise HTTPException(401, "login required: open /login?token=<token>")

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

    async def login(request: Request):
        if not hmac.compare_digest(request.query_params.get("token", "").encode(), token.encode()):
            raise HTTPException(401, "wrong token")
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict")
        return resp

    async def index(request: Request):
        return FileResponse(STATIC / "index.html")

    async def status(request: Request):
        return JSONResponse(await asyncio.to_thread(governor_status, socket))

    async def events(request: Request):
        async def stream():
            while True:
                yield f"data: {json.dumps(await asyncio.to_thread(governor_status, socket))}\n\n"
                await asyncio.sleep(interval)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    async def flow_events(request: Request):
        """SSE feed of workload events for the dots; the page opens it only while the Cluster tab is showing."""
        async def stream():
            after = int(request.query_params.get("after") or flow.seq)  # no cursor: only what happens from now on
            while True:
                level = (await asyncio.to_thread(governor_status, socket)).get("level") or 0
                paused = level >= PAUSE_LEVEL
                if not paused:
                    await asyncio.to_thread(flow.poll)
                snap = await asyncio.to_thread(flow.since, after)
                after = snap["cursor"]
                yield f"data: {json.dumps({**snap, 'paused': paused, 'level': level})}\n\n"
                await asyncio.sleep(interval)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    async def cluster_state(request: Request):
        return JSONResponse(await asyncio.to_thread(cluster.snapshot))

    async def plan(request: Request):
        """Estimate only, no side effects (W-3): the page cannot start containers, so it shows the command to run."""
        from benchctl import checks, workbench

        q = request.query_params
        try:
            spec = workbench.from_dict({k: int(q[k]) for k in ("cpu_nodes", "gpu_nodes", "gpus_per_node") if k in q}
                                       | ({"parts": [p for p in q["parts"].split(",") if p]} if "parts" in q else {}),
                                       workbench.defaults(sim))
        except (ValueError, workbench.SpecError) as e:
            raise HTTPException(422, str(e)) from e
        if problems := spec.problems():
            raise HTTPException(422, "; ".join(problems))
        est = workbench.estimate(*workbench.effective(sim, spec))
        avail = checks.mem_available() // (1024 * 1024)
        ok, msg = workbench.verdict(est, avail, spec=spec)
        flags = f"--cpu-nodes {spec.cpu_nodes} --gpu-nodes {spec.gpu_nodes} --gpus-per-node {spec.gpus_per_node}" + "".join(
            f" --with {p}" for p in sorted(spec.parts)) + "".join(f" --without {p}" for p in workbench.PARTS if p not in spec.parts)
        return JSONResponse({"fits": ok, "message": msg, "total_mb": est["total_mb"], "available_mb": avail,
                             "text": workbench.render(spec, est), "command": f"benchctl up {flags}"})

    async def node_detail(request: Request):
        from benchctl import describe

        try:
            info = await asyncio.to_thread(describe.gather, cluster.sim, request.path_params["node"], cluster.fetch)
        except describe.UnknownNode as e:
            raise HTTPException(404, str(e.args[0])) from e
        return JSONResponse({"info": info, "text": describe.render(info)})

    async def node_action(request: Request):
        try:
            results = await asyncio.to_thread(cluster.act, request.path_params["node"], request.path_params["action"])
        except ActionError as e:
            raise HTTPException(422, str(e)) from e
        for r in results:
            if r["status"] == "ok":
                flow.note(r["target"], f"{request.path_params['action']} on {request.path_params['node']}")
        return JSONResponse({"results": results})

    async def list_scenarios(request: Request):
        return JSONResponse([{"name": s["name"], "description": s.get("description", ""), "steps": len(s.get("steps", []))}
                             for s in scenario.list_all(scenarios)])

    async def run_state(request: Request):
        return JSONResponse(runs.state)

    async def stop(request: Request):
        return JSONResponse(runs.stop())

    async def run(request: Request):
        try:
            speed = float(request.query_params.get("speed", "1"))
        except ValueError:
            speed = 0.0
        if not 0.1 <= speed <= 1000:
            raise HTTPException(422, "speed must be between 0.1 and 1000")
        return JSONResponse(runs.start(request.path_params["name"], speed))

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
        routes=[
            Route("/login", login),
            Route("/", protected(index)),
            Route("/help", protected(help_home)),
            Route("/help/", protected(help_home)),
            Route("/help/search", protected(help_search)),
            Route("/help/files/{rel:path}", protected(help_file)),
            Route("/help/{slug:path}", protected(help_page)),
            Route("/api/status", protected(status)),
            Route("/api/events", protected(events)),
            Route("/api/cluster", protected(cluster_state)),
            Route("/api/flow", protected(flow_events)),
            Route("/api/plan", protected(plan)),
            Route("/api/nodes/{node}", protected(node_detail)),
            Route("/api/nodes/{node}/{action}", protected(node_action, write=True), methods=["POST"]),
            Route("/api/scenarios", protected(list_scenarios)),
            Route("/api/scenarios/run", protected(run_state)),
            Route("/api/scenarios/stop", protected(stop, write=True), methods=["POST"]),
            Route("/api/scenarios/{name}/run", protected(run, write=True), methods=["POST"]),
        ],
        exception_handlers={HTTPException: http_error},
    )
