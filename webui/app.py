"""Web UI backend (SPEC W-1..W-3, W-5, W-6, W-8).

Runs as an ordinary user: it reads the governor over /run/workbench/gov.sock and plays scenarios in-process
(HTTP targets only; Slurm steps need Docker access, which the UI must not have). Everything needs the login token.
Built on Starlette (FastAPI's base) rather than FastAPI itself: FastAPI's pydantic alone costs ~15 MB of the 50 MB
budget (ADR 0008).
"""
import asyncio
import hmac
import json
import re
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
    Response,
    StreamingResponse,
)
from starlette.routing import Route

from benchctl import checks, nhc, scenario

from .cluster import ActionError, Cluster
from .flow import PAUSE_LEVEL, Flow
from .wiki import Wiki, WikiError, esc, page

STATIC = Path(__file__).parent / "static"
COOKIE = "workbench_ui"
DOCS = Path(__file__).resolve().parent.parent / "docs"


SLURM_STATE = {"power-off": "off", "power-on": "on", "clear": "resume"}  # page action -> governor node-state (off/on freeze the node)


def governor_status(socket: str) -> dict:
    """The governor's status, or an {"ok": False, "error": ...} the page shows as 'not reachable'."""
    try:
        return checks.governor_request({"cmd": "status"}, socket)
    except OSError as e:
        return {"ok": False, "error": f"governor not reachable: {e}"}


class Runs:
    """At most one scenario at a time; state is what the page polls."""

    def __init__(self, root: Path, sim: dict, flow: Flow | None = None, slurm=None):
        self.root, self.sim, self.flow, self.slurm = root, sim, flow, slurm
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
                self.flow.note(step.target, outcome.label, step.do.get("nodes") or step.do.get("drain") or step.do.get("resume"))
            done[0] += 1

        def on_expect(outcome) -> None:
            self.state["expects"].append({"label": outcome.label, "status": outcome.status, "detail": outcome.detail})

        try:
            rep = scenario.run(sc, speed=speed, dilation=float(self.sim.get("time_dilation", 1.0)), say=lambda m: None,
                               host=False, should_stop=self.stop_flag.is_set, on_step=on_step, on_expect=on_expect,
                               slurm=self.slurm)
            self.state.update(ok=rep.ok, stopped=rep.stopped)
        finally:
            self.state["running"] = False

    def stop(self) -> dict:
        self.stop_flag.set()
        return {"stopping": bool(self.state.get("running"))}


def create_app(token: str, socket: str = checks.GOV_SOCKET, scenarios: Path = Path("scenarios"),
               sim_yaml: Path = Path("sim.yaml"), interval: float = 1.0, docs: Path = DOCS,
               fetch=None, poster=None, nhc_src: Path = nhc.SRC, nhc_out: Path = nhc.OUT) -> Starlette:
    sim = yaml.safe_load(sim_yaml.read_text())
    flow = Flow()
    runs = Runs(scenarios, sim, flow, slurm=lambda n, s, r: _governed(slurm_state(n, s, r)))
    cluster = Cluster(sim, **{k: v for k, v in (("fetch", fetch), ("poster", poster)) if v})  # test seams: a host stack must not answer or be hit
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

    def build_plan(q) -> tuple:
        from benchctl import workbench

        try:
            spec = workbench.from_dict({k: int(q[k]) for k in ("cpu_nodes", "gpu_nodes", "gpus_per_node") if k in q}
                                       | ({"parts": [p for p in q["parts"].split(",") if p]} if "parts" in q else {})
                                       | ({"bmc_nodes": [n for n in q["bmc_nodes"].split(",") if n]} if "bmc_nodes" in q else {}),
                                       workbench.defaults(sim))
        except (ValueError, workbench.SpecError) as e:
            raise HTTPException(422, str(e)) from e
        if problems := spec.problems():
            raise HTTPException(422, "; ".join(problems))
        est = workbench.estimate(*workbench.effective(sim, spec))
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
        return JSONResponse({"parts": workbench.catalog(built), "nodes": nodes, "bmc_node_mb": compose.BMC_FORWARDER_MB,
                             "limits": {k: list(v) for k, v in workbench.LIMITS.items()}})

    async def plan(request: Request):
        """Estimate only, no side effects: the page cannot start containers. Applying goes through the governor."""
        spec, est, avail, ok, msg = build_plan(request.query_params)
        from benchctl import workbench

        flags = f"--cpu-nodes {spec.cpu_nodes} --gpu-nodes {spec.gpu_nodes} --gpus-per-node {spec.gpus_per_node}" + "".join(
            f" --with {p}" for p in sorted(spec.parts)) + "".join(f" --without {p}" for p in workbench.PARTS if p not in spec.parts)
        if spec.bmc_nodes is not None:
            flags += f" --bmc-nodes {','.join(spec.bmc_nodes)}"
        return JSONResponse({"fits": ok, "message": msg, "total_mb": est["total_mb"], "available_mb": avail,
                             "text": workbench.render(spec, est), "command": f"benchctl up {flags}"})

    async def plan_apply(request: Request):
        """Ask the governor to rebuild the workbench to this plan (it runs `benchctl up` as the user, calm levels only)."""
        spec, _, _, ok, msg = build_plan(request.query_params)
        if not ok:
            raise HTTPException(422, "does not fit: " + msg)
        req = {"cmd": "apply-plan", "cpu_nodes": spec.cpu_nodes, "gpu_nodes": spec.gpu_nodes,
               "gpus_per_node": spec.gpus_per_node, "parts": sorted(spec.parts)}
        if spec.bmc_nodes is not None:
            req["bmc_nodes"] = spec.bmc_nodes
        try:
            res = await asyncio.to_thread(checks.governor_request, req, socket)
        except (OSError, ValueError) as e:
            raise HTTPException(503, "the governor is not reachable: start it with ./scripts/benchctl start") from e
        if not res.get("ok"):
            raise HTTPException(409, res.get("error", "the governor refused"))
        if res.get("dry_run"):
            return JSONResponse({"started": False, "message": "The governor is in dry-run mode or has no --workdir, so nothing was applied. Run the command shown instead."})
        return JSONResponse({"started": True, "message": "Applying. Nodes appear and disappear as they change; progress is in Governor activity."})

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
            info = await asyncio.to_thread(describe.gather, cluster.sim, request.path_params["node"], cluster.fetch)
        except describe.UnknownNode as e:
            raise HTTPException(404, str(e.args[0])) from e
        from benchctl import addressing, export

        node, plan = request.path_params["node"], addressing.plan(cluster.sim)
        try:
            _, services, _ = export.configured(yaml.safe_load(sim_yaml.read_text()))
        except (export.ExportError, OSError, ValueError):
            services = set()
        return JSONResponse({"info": info, "text": describe.render(info), "ip": plan["nodes"].get(node),
                             "bmc_ip": plan["bmc"].get(node) if f"bmc-{node}" in services else None})

    async def connections(request: Request):
        from webui import connections as conn

        return JSONResponse({"entities": conn.entities(cluster.sim)})

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
                                 "X-Warning": " ".join(export.warnings(services))})

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

    async def node_workload(request: Request):
        """Submit a job on this node: the governor runs `benchctl submit` (the page has no Docker, W-3)."""
        q, node = request.query_params, request.path_params["node"]
        try:
            gpus, duration = int(q.get("gpus", "0")), int(q.get("duration", "300"))
        except ValueError as e:
            raise HTTPException(422, "gpus and duration must be whole numbers") from e
        if node not in {n["name"] for n in cluster.snapshot()["nodes"]}:
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
        state = SLURM_STATE.get(request.path_params["action"])
        if state and any(r["status"] == "ok" for r in results):  # the BMC changed; Slurm only knows through the governor
            results.append(await asyncio.to_thread(slurm_state, request.path_params["node"], state))
        for r in results:
            if r["status"] == "ok":
                flow.note(r["target"], f"{request.path_params['action']} on {request.path_params['node']}", [request.path_params["node"]])
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
            Route("/api/settings", protected(settings)),
            Route("/api/plan", protected(plan)),
            Route("/api/plan/apply", protected(plan_apply, write=True), methods=["POST"]),
            Route("/api/nhc", protected(nhc_state)),
            Route("/api/nhc", protected(nhc_change, write=True), methods=["POST"]),
            Route("/api/connections", protected(connections)),
            Route("/api/export/profile", protected(export_profile)),
            Route("/api/nodes/{node}", protected(node_detail)),
            Route("/api/nodes/{node}/workload", protected(node_workload, write=True), methods=["POST"]),
            Route("/api/nodes/{node}/{action}", protected(node_action, write=True), methods=["POST"]),
            Route("/api/scenarios", protected(list_scenarios)),
            Route("/api/scenarios/run", protected(run_state)),
            Route("/api/scenarios/stop", protected(stop, write=True), methods=["POST"]),
            Route("/api/scenarios/{name}/run", protected(run, write=True), methods=["POST"]),
        ],
        exception_handlers={HTTPException: http_error},
    )
