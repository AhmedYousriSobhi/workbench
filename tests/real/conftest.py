import os

import helpers
import pytest

from benchctl import stack

# Containers that ignore SIGTERM cost docker's 10 s grace each, in two waves: about 21 s per module teardown, 7 minutes in a --full run (issue 310).
os.environ.setdefault("WORKBENCH_STOP_TIMEOUT", "2")


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


def _mark(config, items):
    for item in items:
        if "tests/real" in str(item.fspath):
            item.add_marker(pytest.mark.live)
    if os.environ.get("WORKBENCH_REAL") != "1":
        skip = pytest.mark.skip(reason="real-host tests; run scripts/real-test.sh")
        for item in items:
            if "tests/real" in str(item.fspath):
                item.add_marker(skip)


# Modules that must start their own stack (issue 234): test timers, a custom selection or governor, Teleport, Jira, or the
# stack lifecycle itself. They run after every module that shares the default stack and each begins from a stopped one.
ISOLATED_MODULES = {"test_apply_real", "test_jira_dc_real", "test_nhc_real", "test_pressure_real", "test_slurmresilience_real",
                    "test_k8s_real", "test_access_real", "test_dcgm_real", "test_stack_real", "test_teleport_real", "test_user_mode_real", "test_workbench_real"}


def _isolated(item) -> bool:
    return item.module.__name__.rsplit(".", 1)[-1] in ISOLATED_MODULES


def stop_shared_stack() -> None:
    if helpers.STACK_STARTED:
        os.chdir(helpers.ROOT)
        stack.down(say=lambda m: None)
        helpers.STACK_STARTED = False


@pytest.fixture(scope="module", autouse=True)
def _own_stack(request):
    """An isolated module starts from a stopped stack (a different slurm.conf or selection must not meet a running one)."""
    if request.module.__name__.rsplit(".", 1)[-1] in ISOLATED_MODULES:
        stop_shared_stack()
    yield


@pytest.fixture(scope="session", autouse=True)
def _shared_stack_down():
    yield
    stop_shared_stack()


def pytest_collection_modifyitems(config, items):
    items.sort(key=lambda i: "tests/real" in str(i.fspath) and _isolated(i))  # stable: shared modules first
    _mark(config, items)
