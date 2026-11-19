"""`benchctl start` stops an older web page that holds the port and that the pid file does not know (issue 913)."""
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "benchctl-launch"


def _orphan_fn(port: str) -> str:
    line = next(ln for ln in SCRIPT.read_text().splitlines() if ln.startswith("orphan_ui_pid()"))
    return f'port={port}\n{line}\norphan_ui_pid\n'


def _find(port: str) -> str:
    return subprocess.run(["bash", "-c", _orphan_fn(port)], capture_output=True, text=True, check=False).stdout.strip()


def test_finds_a_page_started_by_hand_and_ignores_other_ports_and_processes():
    port = "18913"
    # argv is exactly the page's (a shell waiting on an open stdin), as `python -m webui PORT` has no trailing argument
    proc = subprocess.Popen(["bash", "-c", f'exec -a ".venv/bin/python -m webui {port}" bash'], stdin=subprocess.PIPE)
    try:
        time.sleep(0.3)
        assert _find(port) == str(proc.pid)
        assert _find("18914") == ""  # another port is not ours to stop
        assert _find("1891") == ""  # a prefix of the port does not match
    finally:
        proc.kill()
        proc.wait()
    assert _find(port) == ""


def test_start_ui_stops_the_orphan_before_it_binds():
    text = SCRIPT.read_text()
    assert text.index("orphan_ui_pid)") < text.index('nohup .venv/bin/python -m webui "$port"')
