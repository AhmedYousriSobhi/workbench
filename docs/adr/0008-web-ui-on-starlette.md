# ADR 0008: the Web UI backend uses Starlette, not FastAPI

Status: accepted (default; changes SPEC W-1's "FastAPI" wording to meet its own 50 MB limit)

## Context
SPEC W-1 requires a FastAPI backend and a total host RAM under 50 MB. Measured on this host (Python 3.12):
`import fastapi` alone takes the process to 39 MB RSS (pydantic), `starlette` + `uvicorn` + the UI code to 29 MB.
With FastAPI the UI idled at 46 MB and reached 51 MB after the Cluster Canvas (live test failed the 50 MB limit).

## Decision
Build `webui/app.py` on Starlette, which is FastAPI's own base. The UI has ten small routes, no request-body models
and no OpenAPI use, so nothing from FastAPI is needed. Auth and the CSRF header check are plain functions wrapped
around each handler; errors return `{"detail": ...}` as before. The UI now sits near 32 MB.

## Consequences
- No automatic request validation or API docs; input checks are explicit (and tested).
- Do not add dependencies that import pydantic. Keep `tests/real/test_webui_real.py` (RSS < 50 MB) as the guard.

## Revisit when
The UI needs rich request models, or the memory budget for it is raised.
