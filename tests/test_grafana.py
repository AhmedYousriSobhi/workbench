"""The dashboard's threshold colours come from the palette and read on both Grafana themes (issue 651)."""
import json

from palette_lib import RES, ROOT, mp, solid

DASH = json.loads((ROOT / "observability/dashboards/hpc-overview.json").read_text())
PANELS = {p["id"]: p for p in DASH["panels"]}


def colours(pid):
    return [s["color"] for s in PANELS[pid]["fieldConfig"]["defaults"]["thresholds"]["steps"]]


def test_the_dashboard_file_is_what_the_generator_writes():
    assert (ROOT / "observability/dashboards/hpc-overview.json").read_text() == mp.render_dashboard(mp.load())


def test_xid_errors_are_good_at_zero_and_bad_from_one():
    steps = PANELS[5]["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert [s["value"] for s in steps] == [None, 1]
    assert PANELS[5]["options"]["colorMode"] == "value" and PANELS[5]["fieldConfig"]["defaults"]["color"] == {"mode": "thresholds"}


def test_temperature_shows_limits_as_lines_and_keeps_one_colour_per_series():
    d = PANELS[2]["fieldConfig"]["defaults"]
    assert [s["value"] for s in d["thresholds"]["steps"]] == [None, 80, 90]
    assert d["custom"]["thresholdsStyle"] == {"mode": "line"} and "color" not in d


def test_each_threshold_colour_reaches_3_to_1_on_both_grafana_themes():
    for pid in (2, 5):
        for c in colours(pid):
            for ground in mp.GRAFANA_GROUNDS:
                assert mp.contrast(c, ground) >= 3.0, (pid, c, ground)


def test_ok_warn_and_bad_are_three_different_colours_taken_from_the_palette():
    ok, warn, bad = colours(2)
    assert len({ok, warn, bad}) == 3
    dark = RES["dark"]
    for got, tok in ((ok, "grafana_ok"), (warn, "grafana_warn"), (bad, "grafana_bad")):
        assert got == mp.grafana_colour(solid(dark[tok]))
        assert abs(mp.hex_oklch(got)[2] - mp.hex_oklch(solid(dark[tok]))[2]) < 0.05      # the hue of the token is kept
