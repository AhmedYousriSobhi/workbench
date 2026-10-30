"""Publish audit events as Grafana annotations (SPEC GV-6). Best-effort and off the control loop.

The token comes from the environment (never a file or a log line). Posting is paused from L3 up so the
worker thread does not allocate sockets while the host is starving (GV-8); events queue and flush later.
"""
import json
import logging
import queue
import threading
import time
import urllib.request

log = logging.getLogger("governor.annotate")


def http_post(url: str, token: str, body: dict, timeout: float = 2.0) -> None:
    req = urllib.request.Request(
        url.rstrip("/") + "/api/annotations", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout):
        pass


class Annotator:
    MAX_QUEUE = 500

    def __init__(self, url: str, token: str, post=http_post, tags: tuple[str, ...] = ("workbench", "governor")):
        self.url, self.token, self.post, self.tags = url, token, post, list(tags)
        self.q: queue.Queue = queue.Queue(self.MAX_QUEUE)
        self.paused = threading.Event()
        self.failed = False
        self.sent = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="annotator", daemon=True)
        self._thread.start()

    def emit(self, text: str) -> None:
        try:
            self.q.put_nowait({"time": int(time.time() * 1000), "text": text, "tags": self.tags})
        except queue.Full:
            pass  # drop rather than grow: annotations are advisory

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.paused.is_set():
                self._stop.wait(0.5)
                continue
            try:
                item = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            while self.paused.is_set() and not self._stop.is_set():  # paused while we were waiting
                self._stop.wait(0.2)
            try:
                self.post(self.url, self.token, item)
                self.sent += 1
                self.failed = False
            except Exception as e:  # noqa: BLE001  never let the annotator die
                if not self.failed:
                    log.warning("grafana annotation failed: %s", type(e).__name__)
                self.failed = True

    def close(self) -> None:
        self._stop.set()
