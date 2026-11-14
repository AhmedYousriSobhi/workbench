import os
import subprocess

import helpers
import pytest
from stack_reuse import order_key
from verify_times import Recorder

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


def paused_workbench() -> set[str]:
    """Names of the stack's containers that are paused right now (a frozen node, a hung login)."""
    try:
        r = subprocess.run(["docker", "ps", "--filter", "status=paused", "--filter", "name=workbench-", "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(r.stdout.split())


def _thaw_and_fail(who: str, left: set[str]) -> None:
    """A test or module that leaves a container paused breaks every later module with `cannot start a paused container` and the
    log never says who did it (issue 532). Name it, and thaw so the remaining modules still run."""
    for name in sorted(left):
        subprocess.run(["docker", "unpause", name], capture_output=True, check=False)
    pytest.fail(f"{who} left paused: {', '.join(sorted(left))} (thawed here so the later modules can run)", pytrace=False)


@pytest.fixture(autouse=True)
def _no_container_left_paused(request):
    before = paused_workbench()
    yield
    if left := paused_workbench() - before:
        _thaw_and_fail(request.node.nodeid, left)


@pytest.fixture(scope="module", autouse=True)
def _no_container_left_paused_by_module(request):
    before = paused_workbench()
    yield
    if left := paused_workbench() - before:
        _thaw_and_fail(f"module {request.module.__name__} (fixture setup or teardown)", left)


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
# stack lifecycle itself. test_real starts real governors that discover every labelled container, so they would freeze the
# shared stack's cn004 (issue 530). They run after every module that shares the default stack and each begins from a stopped one.
ISOLATED_MODULES = {"test_real", "test_apply_real", "test_jira_dc_real", "test_nhc_real", "test_pressure_real", "test_slurmresilience_real",
                    "test_k8s_real", "test_access_real", "test_dcgm_real", "test_real_gpu_real", "test_stack_real", "test_teleport_real", "test_user_mode_real", "test_workbench_real", "test_topology_real", "test_qos_real", "test_mig_real", "test_objectstore_real"}


def _isolated(item) -> bool:
    return item.module.__name__.rsplit(".", 1)[-1] in ISOLATED_MODULES


def stop_shared_stack() -> None:
    if helpers.STACK_STARTED:
        os.chdir(helpers.ROOT)
        stack.down(say=lambda m: None)
        helpers.STACK_STARTED = False
        helpers.STACK_KEY = None


@pytest.fixture(scope="module", autouse=True)
def _own_stack(request):
    """An isolated module starts from a stopped stack (a different slurm.conf or selection must not meet a running one).
    A module with `STACK_REUSE = True` is isolated only by its configuration (issue 576): bring_up compares stack keys and does the
    down and up itself when they differ, so two modules with the same key (the test timers) share one cold start. It must leave the
    cluster as it found it, like a shared module, and ends with release(), not stack.down."""
    if request.module.__name__.rsplit(".", 1)[-1] in ISOLATED_MODULES and not getattr(request.module, "STACK_REUSE", False):
        stop_shared_stack()
    yield


@pytest.fixture(scope="session", autouse=True)
def _shared_stack_down():
    yield
    stop_shared_stack()


def pytest_collection_modifyitems(config, items):
    RECORDER.reuse = {i.module.__name__.rsplit(".", 1)[-1] for i in items if getattr(i.module, "STACK_REUSE", False)}
    # stable: shared modules first, then the STACK_REUSE modules grouped by STACK_GROUP (same key side by side), then the cold starts
    items.sort(key=lambda i: order_key(i.module.__name__.rsplit(".", 1)[-1], ISOLATED_MODULES if "tests/real" in str(i.fspath) else set(),
                                       bool(getattr(i.module, "STACK_REUSE", False)), getattr(i.module, "STACK_GROUP", "")))
    _mark(config, items)


# Per-module wall time, including the module's stack up and down (issue 576): written to compose/generated/verify-times.json.
RECORDER = Recorder(ISOLATED_MODULES)


def _module_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0].rsplit("/", 1)[-1].removesuffix(".py")


def pytest_runtest_logstart(nodeid, location):
    RECORDER.start(_module_of(nodeid))


def pytest_runtest_logfinish(nodeid, location):
    RECORDER.finish(_module_of(nodeid))


def pytest_runtest_logreport(report):
    if report.failed:
        RECORDER.outcome(_module_of(report.nodeid), True)


def pytest_terminal_summary(terminalreporter):
    if os.environ.get("WORKBENCH_REAL") != "1" or not RECORDER.mods:
        return
    from datetime import datetime
    RECORDER.write(helpers.ROOT / "compose" / "generated" / "verify-times.json", datetime.now().isoformat(timespec="seconds"))
    terminalreporter.write_line(RECORDER.slow_line())
