"""Read-only Prometheus endpoint. The text is rendered once per control step, so a scrape only
copies bytes (GV-8: no work, and no new allocation of note, on the scrape path)."""
import logging
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("governor")

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def render(gov) -> bytes:
    """Prometheus exposition text for the governor's current state (caller holds gov's lock)."""
    s = gov.signals
    out = [
        "# TYPE workbench_governor_level gauge",
        f"workbench_governor_level {gov.ladder.level}",
        "# TYPE workbench_governor_dry_run gauge",
        f"workbench_governor_dry_run {int(gov.dry_run)}",
        "# TYPE workbench_governor_security_findings gauge",
        f"workbench_governor_security_findings {len(gov.violations)}",
        "# TYPE workbench_governor_groups gauge",
    ]
    by_prio = Counter(g.prio for g in gov.groups)
    out += [f'workbench_governor_groups{{prio="P{p}"}} {n}' for p, n in sorted(by_prio.items())]
    if s:
        out += [
            "# TYPE workbench_host_mem_available_bytes gauge",
            f"workbench_host_mem_available_bytes {s.mem_available}",
            "# TYPE workbench_host_psi_some_avg10 gauge",
            f"workbench_host_psi_some_avg10 {s.psi_some_avg10}",
            "# TYPE workbench_host_psi_full_avg10 gauge",
            f"workbench_host_psi_full_avg10 {s.psi_full_avg10}",
            "# TYPE workbench_host_swap_out_pages_per_second gauge",
            f"workbench_host_swap_out_pages_per_second {s.swap_out_rate}",
        ]
    c = gov.counts
    out += [
        "# TYPE workbench_governor_actions_total counter",
        f"workbench_governor_actions_total {c['actions']}",
        "# TYPE workbench_governor_action_failures_total counter",
        f"workbench_governor_action_failures_total {c['action_failures']}",
        "# TYPE workbench_governor_admit_total counter",
        f'workbench_governor_admit_total{{result="ok"}} {c["admit_ok"]}',
        f'workbench_governor_admit_total{{result="refused"}} {c["admit_refused"]}',
    ]
    return ("\n".join(out) + "\n").encode()


def serve(addr: str, gov) -> ThreadingHTTPServer:
    """Serve GET /metrics on `host:port` in a daemon thread; call .shutdown() to stop."""
    host, _, port = addr.rpartition(":")

    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = gov.metrics
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:  # scrapes every 15 s would flood the journal
            pass

    srv = ThreadingHTTPServer((host, int(port)), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True, name="metrics").start()
    return srv
