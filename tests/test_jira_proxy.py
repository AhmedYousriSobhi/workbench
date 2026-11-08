"""The fault proxy in front of the real Jira (issue 305), against a tiny fake upstream."""
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from benchctl import jira_proxy


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        got = self.rfile.read(n) if n else b""
        raw = json.dumps({"path": self.path, "auth": self.headers.get("Authorization"), "body": got.decode()}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Seen", "yes")
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = answer


@pytest.fixture
def stack():
    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    slept = []
    proxy = jira_proxy.FaultProxy(port=0, upstream=("127.0.0.1", up.server_address[1]), sleep=slept.append).start()
    yield proxy, slept
    proxy.stop()
    up.shutdown()
    up.server_close()


def call(proxy, path="/rest/api/2/myself", method="GET", body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def test_passes_requests_through_with_headers_and_body(stack):
    proxy, _ = stack
    status, headers, body = call(proxy, "/rest/api/2/search", "POST", b'{"jql":"x"}', {"Authorization": "Bearer t"})
    seen = json.loads(body)
    assert status == 200 and headers["X-Seen"] == "yes"
    assert seen == {"path": "/rest/api/2/search", "auth": "Bearer t", "body": '{"jql":"x"}'}


def test_503_and_429_apply_to_the_next_n_rest_calls_then_clear(stack):
    proxy, _ = stack
    proxy.fault.set({"mode": "503", "count": 2, "retry_after": 7})
    assert call(proxy, "/status")[0] == 200  # not a REST path: never failed, and not counted
    first = call(proxy)
    assert first[0] == 503 and first[1]["Retry-After"] == "7"
    assert call(proxy)[0] == 503
    assert call(proxy)[0] == 200
    proxy.fault.set({"mode": "429", "count": 1, "retry_after": 30})
    status, headers, _ = call(proxy)
    assert status == 429 and headers["Retry-After"] == "30"


def test_slow_waits_then_gives_the_real_answer(stack):
    proxy, slept = stack
    proxy.fault.set({"mode": "slow", "count": 1, "delay": 8})
    assert call(proxy)[0] == 200 and slept == [8.0]
    assert call(proxy)[0] == 200 and slept == [8.0]


def test_off_clears_and_a_bad_mode_is_refused(stack):
    proxy, _ = stack
    proxy.fault.set({"mode": "503", "count": 5})
    proxy.fault.set(None)
    assert call(proxy)[0] == 200
    with pytest.raises(ValueError, match="mode must be"):
        proxy.fault.set({"mode": "explode"})


def test_a_dead_upstream_is_a_502_not_a_hang():
    proxy = jira_proxy.FaultProxy(port=0, upstream=("127.0.0.1", 1)).start()
    try:
        assert call(proxy)[0] == 502
    finally:
        proxy.stop()


def test_shared_proxy_is_one_per_process(monkeypatch):
    real = jira_proxy.FaultProxy
    monkeypatch.setattr(jira_proxy, "_running", None)
    monkeypatch.setattr(jira_proxy, "FaultProxy", lambda: real(port=0))
    made = jira_proxy.shared()
    try:
        assert jira_proxy.shared() is made
    finally:
        made.stop()
