"""Web UI backend (SPEC W-1..W-3, W-5, W-6, W-8).

Runs as an ordinary user: it reads the governor over /run/hpcsim/gov.sock and plays scenarios in-process
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
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.routing import Route

from simctl import checks, scenario

from .cluster import ActionError, Cluster
from .flow import PAUSE_LEVEL, Flow

STATIC = Path(__file__).parent / "static"
COOKIE = "hpcsim_ui"


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
               sim_yaml: Path = Path("sim.yaml"), interval: float = 1.0) -> Starlette:
    sim = yaml.safe_load(sim_yaml.read_text())
    flow = Flow()
    runs = Runs(scenarios, sim, flow)
    cluster = Cluster(sim)

    def authed(request: Request) -> None:
        got = request.cookies.get(COOKIE) or request.headers.get("authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(got.encode(), token.encode()):
            raise HTTPException(401, "login required: open /login?token=<token>")

    def csrf(request: Request) -> None:  # a cross-site form cannot set this header
        if request.headers.get("x-requested-with") != "opti-sim":
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

    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return Starlette(
        routes=[
            Route("/login", login),
            Route("/", protected(index)),
            Route("/api/status", protected(status)),
            Route("/api/events", protected(events)),
            Route("/api/cluster", protected(cluster_state)),
            Route("/api/flow", protected(flow_events)),
            Route("/api/nodes/{node}/{action}", protected(node_action, write=True), methods=["POST"]),
            Route("/api/scenarios", protected(list_scenarios)),
            Route("/api/scenarios/run", protected(run_state)),
            Route("/api/scenarios/stop", protected(stop, write=True), methods=["POST"]),
            Route("/api/scenarios/{name}/run", protected(run, write=True), methods=["POST"]),
        ],
        exception_handlers={HTTPException: http_error},
    )
