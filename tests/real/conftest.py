import os

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "tests/real" in str(item.fspath):
            item.add_marker(pytest.mark.live)
    if os.environ.get("WORKBENCH_REAL") != "1":
        skip = pytest.mark.skip(reason="real-host tests; run scripts/real-test.sh")
        for item in items:
            if "tests/real" in str(item.fspath):
                item.add_marker(skip)
