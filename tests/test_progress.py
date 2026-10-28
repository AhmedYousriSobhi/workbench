import re
from pathlib import Path

import pytest

from simctl import progress

DATA = progress.load()


def test_statuses_are_valid_and_every_item_is_explained():
    for ph in DATA["phases"]:
        assert ph["items"], ph["id"]
        for it in ph["items"]:
            assert it["status"] in progress.LABEL
            assert len(it["how"]) > 20, f"{it['name']}: say how it is built"


def test_where_paths_exist_for_built_items():
    for it in progress.all_items(DATA):
        if it["status"] in ("done", "partial"):
            for w in it.get("where", "").split(","):
                if w.strip():
                    assert Path(w.strip()).exists(), f"{it['name']}: {w.strip()} is missing"


def test_architecture_edges_reference_real_nodes_and_groups():
    a = DATA["architecture"]
    ids = {n["id"] for n in a["nodes"]}
    assert len(ids) == len(a["nodes"])
    assert {n["group"] for n in a["nodes"]} <= {g["id"] for g in a["groups"]}
    assert all(n["status"] in progress.LABEL for n in a["nodes"])
    for src, dst, _ in a["edges"]:
        assert src in ids and dst in ids, (src, dst)


def test_phase_status_and_percentages():
    def mk(*s):
        return [{"status": x} for x in s]

    assert progress.phase_status(mk("done", "done")) == "done"
    assert progress.phase_status(mk("todo", "todo")) == "todo"
    assert progress.phase_status(mk("done", "review")) == "review"
    assert progress.phase_status(mk("done", "todo")) == "partial"
    assert progress.pct(mk("done", "review", "partial", "todo")) == 50
    assert progress.pct([]) == 0


def test_mermaid_blocks_are_well_formed():
    for text in (progress.roadmap_mermaid(DATA), progress.architecture_mermaid(DATA)):
        assert text.startswith("flowchart ")
        assert text.count("subgraph ") == text.count("\n    end") or "subgraph" not in text
        assert all(f"classDef {s} " in text for s in progress.LABEL)
        assert text.count("[") == text.count("]") and text.count('"') % 2 == 0
    assert "p0 --> p1" in progress.roadmap_mermaid(DATA)


def test_pr_links_support_single_list_and_none():
    repo = "https://x/r"
    assert progress._pr_links({"pr": 3}, repo) == "[#3](https://x/r/pull/3)"
    assert progress._pr_links({"pr": [3, 4]}, repo).count("pull/") == 2
    assert progress._pr_links({"pr": None}, repo) == "-"


def test_generated_files_are_up_to_date():
    assert progress.stale() == [], "run: python -m simctl.progress"


def test_html_is_self_contained_and_escapes():
    data = {**DATA, "next": ["a <script>alert(1)</script>"]}
    page = progress.render_html(data)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert page.count("cdnjs.cloudflare.com") == 1 and 'startOnLoad:true' in page
    assert re.search(r"<title>[^<]+</title>", page)


def test_unknown_status_is_rejected(tmp_path):
    bad = tmp_path / "p.yaml"
    bad.write_text("phases:\n  - {id: a, title: A, goal: g, items: [{name: n, status: wip, how: h}]}\n")
    with pytest.raises(ValueError):
        progress.load(bad)
