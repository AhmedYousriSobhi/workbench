"""A node inventory file for a test (issue 462): the mocks read the generated inventory.json, so a test builds one from a sim.yaml path or dict."""
import hashlib
import json
import tempfile
from pathlib import Path

import yaml

from benchctl import hwinventory

_DIR = Path(tempfile.mkdtemp(prefix="wb-inventory-"))


def inv(sim) -> Path:
    """The inventory.json for `sim` (a path to a sim.yaml, or the dict); the same content gives the same file."""
    cfg = yaml.safe_load(Path(sim).read_text()) if isinstance(sim, (str, Path)) else sim
    body = json.dumps(hwinventory.build(cfg), indent=1) + "\n"
    f = _DIR / (hashlib.sha1(body.encode()).hexdigest()[:12] + ".json")
    f.write_text(body)
    return f
