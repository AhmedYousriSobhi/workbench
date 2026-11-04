import os

import pytest


@pytest.fixture(scope="module")
def test_timers():
    """Shorter Slurm timers for a module's cluster (issue 236): SlurmdTimeout 10 and HealthCheckInterval 15 instead of 15 and 30.
    List it first in the module's `cluster` fixture so the stack is generated with them; the default timings keep their own
    check in test_stack_real. Restored afterwards, so the next `up` regenerates slurm.conf with the defaults."""
    old = os.environ.get("WORKBENCH_TEST_TIMERS")
    os.environ["WORKBENCH_TEST_TIMERS"] = "1"
    yield
    if old is None:
        os.environ.pop("WORKBENCH_TEST_TIMERS", None)
    else:
        os.environ["WORKBENCH_TEST_TIMERS"] = old


def pytest_collection_modifyitems(config, items):
    if os.environ.get("WORKBENCH_REAL") != "1":
        skip = pytest.mark.skip(reason="real-host tests; run scripts/real-test.sh")
        for item in items:
            if "tests/real" in str(item.fspath):
                item.add_marker(skip)
