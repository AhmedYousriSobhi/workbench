"""One definition of the services count (issue 861 A3, ADR 0089): the page counts every part it draws, and the server probes each."""
import re
from pathlib import Path

from webui import cluster

JS = Path(__file__).resolve().parent.parent / "webui" / "static" / "js"


def test_every_always_drawn_service_is_probed():
    """BASE_SERVICES (always drawn in the page) and the server's SERVICES probes list the same parts."""
    text = (JS / "flowstate.js").read_text()
    drawn = set(re.findall(r"\['([a-z0-9-]+)', '[^']+', '[^']+'\]", re.search(r"BASE_SERVICES = \[(.*?)\];", text).group(1)))
    assert drawn == set(cluster.SERVICES)


def test_every_place_uses_the_one_function():
    """No module re-derives the count from the services map; each shows it through servicesCount."""
    for name in ("governor.js", "cluster.js", "overview.js", "homemodel.js"):
        src = (JS / name).read_text()
        assert "servicesCount(" in src, name
        assert not re.search(r"Object\.values\([^)]*services\)\.filter", src), name
        assert "svcs.length" not in src.replace("svcs.length > 0", ""), name
