"""Web UI backend (SPEC W-1..W-3, W-5, W-6, W-8).

Runs as an ordinary user: it reads the governor over /run/hpcsim/gov.sock and plays scenarios in-process
(HTTP targets only; Slurm steps need Docker access, which the UI must not have). Everything needs the login token.
"""
import asyncio
import hmac
import json
import threading
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from simctl import checks, scenario

STATIC = Path(__file__).parent / "static"
COOKIE = "hpcsim_ui"


class Runs:
    """At most one scenario at a time; state is what the page polls."""

    def __init__(self, root: Path, sim: dict):
        self.root, self.sim = root, sim
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
               sim_yaml: Path = Path("sim.yaml"), interval: float = 1.0) -> FastAPI:
    app = FastAPI(title="opti-sim", docs_url=None, redoc_url=None, openapi_url=None)
    runs = Runs(scenarios, yaml.safe_load(sim_yaml.read_text()))

    def authed(request: Request) -> None:
        got = request.cookies.get(COOKIE) or request.headers.get("authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(got.encode(), token.encode()):
            raise HTTPException(401, "login required: open /login?token=<token>")

    def csrf(request: Request) -> None:  # a cross-site form cannot set this header
        if request.headers.get("x-requested-with") != "opti-sim":
            raise HTTPException(403, "missing X-Requested-With")

    def governor() -> dict:
        try:
            return checks.governor_request({"cmd": "status"}, socket)
        except OSError as e:
            return {"ok": False, "error": f"governor not reachable: {e}"}

    @app.get("/login")
    def login(token: str = ""):
        if not hmac.compare_digest(token.encode(), token.encode()):
            raise HTTPException(401, "wrong token")
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict")
        return resp

    @app.get("/", dependencies=[Depends(authed)])
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status", dependencies=[Depends(authed)])
    def status():
        return governor()

    @app.get("/api/events", dependencies=[Depends(authed)])
    async def events():
        async def stream():
            while True:
                yield f"data: {json.dumps(await asyncio.to_thread(governor))}\n\n"
                await asyncio.sleep(interval)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/scenarios", dependencies=[Depends(authed)])
    def list_scenarios():
        return [{"name": s["name"], "description": s.get("description", ""), "steps": len(s.get("steps", []))}
                for s in scenario.list_all(scenarios)]

    @app.get("/api/scenarios/run", dependencies=[Depends(authed)])
    def run_state():
        return runs.state

    @app.post("/api/scenarios/stop", dependencies=[Depends(authed), Depends(csrf)])
    def stop():
        return runs.stop()

    @app.post("/api/scenarios/{name}/run", dependencies=[Depends(authed), Depends(csrf)])
    def run(name: str, speed: float = 1.0):
        if not 0.1 <= speed <= 1000:
            raise HTTPException(422, "speed must be between 0.1 and 1000")
        return runs.start(name, speed)

    return app
