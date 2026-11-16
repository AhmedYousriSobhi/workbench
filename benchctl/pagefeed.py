"""A `benchctl scenario run` shown on the web page, best effort (ADR 0086 phase 4, issue 811).

When the page answers, the run's step results go to it (POST /api/scenarios/cli on 127.0.0.1 only, the page token in a header) and the
page draws them like one of its own runs, marked "run from the CLI". The page builds the steps from its own copy of the scenario; a post
carries the scenario's name, a fingerprint of its plan, and per step or expectation only a status, a short detail and a time.

Best effort: a background thread posts, with a short timeout, so the run's timing never waits for the page. No page (no token file, or
nothing listening) means no output at all, exactly as before. A page that refuses (busy with its own run, another token, another copy of the
scenario) or answers too slowly gets one short notice, then the run goes on without it. Nothing is written to disk.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from . import scenario

TOKEN = Path("compose/generated/secrets/ui_token")  # the page's login token, 0600 (webui/__main__.py); read, never printed
TIMEOUT = 1.0  # seconds a post may take; a slower page loses this run's steps, never the run's time
BEAT = 10.0  # seconds between keep-alive posts while no step happens (the page drops a run that is quiet for 30 s)
FLUSH = 1.0  # seconds the end of the run waits, at most, for its last posts
QUEUE = 512  # events waiting for the page; more means the page cannot keep up, and the feed stops


def page_url() -> str:
    return f"http://127.0.0.1:{os.environ.get('BENCHCTL_UI_PORT', '8088')}/api/scenarios/cli"


def send(url: str, token: str, body: dict, timeout: float = TIMEOUT) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "X-Requested-With": "workbench", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(4096) or b"{}")


def _why(e: urllib.error.HTTPError) -> str:
    try:
        detail = str(json.loads(e.read(1024)).get("detail", ""))
    except (ValueError, AttributeError, OSError):
        detail = ""
    if e.code in (401, 403):
        return f"it refused the page token (HTTP {e.code}; is it another checkout's page?)"
    return (detail or f"HTTP {e.code}")[:200]


class Feed:
    """Post one run's events to the page from a background thread. `step`, `expect` and `close` never block for longer than FLUSH (close)."""

    def __init__(self, sc: dict, speed: float, say: Callable[[str], None], token_file: Path | None = None, url: str | None = None,
                 post: Callable = send, clock: Callable[[], float] = time.monotonic, beat: float = BEAT, timeout: float = TIMEOUT):
        self.say, self.post, self.clock, self.beat, self.timeout = say, post, clock, beat, timeout
        self.url = url or page_url()
        self.q: queue.Queue = queue.Queue(QUEUE)
        self.done = 0
        self.t0 = clock()
        self.thread: threading.Thread | None = None
        try:
            self.token = (token_file or TOKEN).read_text().strip()
        except OSError:
            self.token = ""  # the page never ran here: no feed, no output
        if not self.token:
            return
        steps = scenario.plan(sc)
        self.start = {"event": "start", "name": sc["name"], "speed": speed, "key": scenario.plan_key(steps)}
        self.thread = threading.Thread(target=self._loop, daemon=True, name="pagefeed")
        self.thread.start()

    def _t(self) -> float:
        return round(self.clock() - self.t0, 1)

    def _put(self, ev: dict) -> None:
        if self.thread is None or not self.thread.is_alive():
            return
        try:
            self.q.put_nowait(ev)
        except queue.Full:
            self.thread = None  # the page cannot keep up: the rest of the run is not shown (said by the page: no word for 30 s)

    def step(self, step, outcome) -> None:
        """scenario.run's on_step: the i-th step of the plan has its result."""
        self._put({"event": "step", "i": self.done, "status": outcome.status, "detail": outcome.detail, "t": self._t()})
        self.done += 1

    def expect(self, outcome) -> None:
        self._put({"event": "expect", "label": outcome.label, "status": outcome.status, "detail": outcome.detail, "t": self._t()})

    def close(self, ok: bool | None, stopped: bool) -> None:
        """The run ended (or Ctrl+C stopped it): the last posts get at most FLUSH seconds."""
        thread = self.thread
        self._put({"event": "end", "ok": ok, "stopped": stopped})
        if thread is not None:
            thread.join(FLUSH)

    def _send(self, ev: dict) -> dict | None:
        """The page's answer, or None once the feed is off (said once when the page refused or was slow; silent when no page listens)."""
        try:
            return self.post(self.url, self.token, ev, self.timeout)
        except urllib.error.HTTPError as e:
            self.say(f"web page: this run is not shown there: {_why(e)}")
        except (TimeoutError, urllib.error.URLError, OSError, ValueError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, TimeoutError) or "timed out" in str(reason):
                self.say(f"web page: no answer within {self.timeout:g} s, so the rest of this run is not shown there")
        return None

    def _loop(self) -> None:
        got = self._send(self.start)
        run = got.get("run") if isinstance(got, dict) else None
        if not isinstance(run, str):
            return
        while True:
            try:
                ev = self.q.get(timeout=self.beat)
            except queue.Empty:
                ev = {"event": "beat"}
            if self._send({**ev, "run": run}) is None or ev["event"] == "end":
                return
