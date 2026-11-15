"""The demo client for the mocks (issue 650): its calls, its User-Agent and its behaviour when a mock is down."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location("mock_sim", Path(__file__).resolve().parent.parent / "images/login/mock_sim.py")
mock_sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mock_sim)


def test_calls_cover_the_three_mocks_and_name_the_nodes():
    urls = [u for _, u, _ in mock_sim.calls(["cn001", "cn002"])]
    assert any("jira-mock" in u for u in urls) and any("bcm-mock" in u for u in urls)
    assert "http://bmc-farm:8000/cn001/redfish/v1/" in urls and "http://bmc-farm:8000/cn002/redfish/v1/" in urls
    bodies = [json.loads(b) for _, _, b in mock_sim.calls(["cn001"]) if b]
    assert {"service": "cmdevice", "call": "getDevices"} in bodies


def test_send_identifies_itself_and_returns_the_status():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["ua"] = self.headers["User-Agent"]
            self.send_response(404 if self.path == "/missing" else 200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    assert mock_sim.send("GET", base + "/ok", None) == 200 and seen["ua"] == mock_sim.UA
    assert mock_sim.send("GET", base + "/missing", None) == 404
    srv.shutdown()
    assert mock_sim.send("GET", base + "/ok", None) == 0          # a mock that is down is not an error to the client
