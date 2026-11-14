"""Requests is the fifth zoom level (issue 650): the rail, the state and the stylesheet agree. scripts/ui_shots.py drives it in a browser."""
import re

from palette_lib import ROOT

STATIC = ROOT / "webui/static"
ZOOM = (STATIC / "js/zoom.js").read_text()


def test_the_rail_has_a_requests_stop_after_traffic():
    html = (STATIC / "index.html").read_text()
    assert html.index('data-level="traffic"') < html.index('data-level="requests"') < html.index('id="zr-in"')
    assert "ZOOM_LEVELS = ['fleet', 'cluster', 'pool', 'traffic', 'requests']" in ZOOM


def test_the_tab_shows_one_block_at_a_time_in_the_circuit_view():
    css = (STATIC / "app.css").read_text()
    assert "#tab-traffic[data-tl=links] > .panel > .tan { display:none; }" in css
    assert re.search(r"#tab-traffic\[data-tl=requests\] > \.panel > :not\(\.tan\):not\(\.zback\) \{ display:none; \}", css)
    assert "tl.dataset.tl = S.trReq ? 'requests' : 'links'" in ZOOM and "removeAttribute('data-tl')" in ZOOM     # the classic diagram keeps both


def test_leaving_the_tab_and_back_and_escape_end_at_the_links():
    assert "if (!onTraffic()) S.trReq = false;" in ZOOM
    assert "zoomTo(S.trReq ? 'traffic' : infraLevel())" in ZOOM
    assert "levelNow() === 'requests') zoomTo('traffic')" in ZOOM
    assert "trReq" in (STATIC / "js/state.js").read_text()
