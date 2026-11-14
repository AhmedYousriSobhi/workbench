import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("events_log", ROOT / "images/login/events_log.py")
events_log = importlib.util.module_from_spec(spec)
spec.loader.exec_module(events_log)

EV = {"seq": 1, "time": 1791288000.0, "severity": "alert", "host": "cn001", "text": "Device status changed from UP to DOWN"}
SHAPE = re.compile(r"^[A-Z][a-z]{2} [A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d \d{4} \[(notice|warning|alert)\] cn001: Device status changed from \w+ to \w+$")


def test_line_has_the_documented_shape():
    assert SHAPE.match(events_log.line(EV))


def test_single_digit_day_is_space_padded():
    # 2026-10-06 12:00 UTC-ish; only the padding matters, so build the time from the local clock
    import time
    t = time.mktime((2026, 10, 6, 12, 0, 0, 0, 0, -1))
    assert events_log.line({**EV, "time": t}).startswith("Tue Oct  6 12:00:00 2026 [alert]")


def test_step_appends_and_follows(tmp_path):
    log = tmp_path / "events.log"
    log.write_text("")
    feed = [EV, {**EV, "seq": 2, "severity": "notice", "text": "Device status changed from DOWN to UP"}]
    fetch = lambda since: {"seq": 2, "events": [e for e in feed if e["seq"] > since]}
    since = events_log.step(fetch, str(log), 0)
    assert since == 2 and len(log.read_text().splitlines()) == 2
    assert events_log.step(fetch, str(log), since) == 2 and len(log.read_text().splitlines()) == 2


def test_restarted_mock_starts_over(tmp_path):
    log = tmp_path / "events.log"
    fetch = lambda since: {"seq": 1, "events": [e for e in [EV] if e["seq"] > since]}
    assert events_log.step(fetch, str(log), 5) == 1
    assert len(log.read_text().splitlines()) == 1
