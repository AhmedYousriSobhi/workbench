import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("HPCSIM_REAL") != "1":
        skip = pytest.mark.skip(reason="real-host tests; run scripts/real-test.sh")
        for item in items:
            if "tests/real" in str(item.fspath):
                item.add_marker(skip)
