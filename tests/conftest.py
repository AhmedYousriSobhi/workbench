import os
import shlex
import subprocess

import pytest

# Host tools a unit test must never run: they act on the machine (containers, units, keys, packets) and are absent on a clean runner.
# A test that needs the behaviour passes a runner or stubs the call; tests/real is the place for the real tool (#473).
HOST_TOOLS = {"systemd-run", "systemctl", "tc", "nsenter", "tcpdump", "kubectl", "sudo"}
# Tools with a harmless stand-in in tests/hostbin: docker answers like a stopped daemon, virsh like no libvirt, ssh-keygen writes placeholder keys.
STUBS = {"docker", "ssh-keygen", "virsh"}
HOSTBIN = os.path.join(os.path.dirname(__file__), "hostbin")


def _tool(args, shell):
    try:
        first = shlex.split(args)[0] if isinstance(args, str) else os.fspath(args[0])
    except (ValueError, IndexError, TypeError):
        return ""
    return os.path.basename(first)


@pytest.fixture(autouse=True)
def _no_host_tools(request, monkeypatch):
    """Fail a unit test that would run a host tool for real. Not applied under tests/real, which exists to run them."""
    if "real" in request.node.path.parts:
        return
    original = subprocess.Popen.__init__

    def guarded(self, args, *a, **kw):
        name = _tool(args, kw.get("shell"))
        if name in STUBS:
            stub = os.path.join(HOSTBIN, name)
            if isinstance(args, str):  # a command line: swap its first word
                args = shlex.quote(stub) + args.lstrip()[len(shlex.split(args)[0]):]
            else:
                args = [stub, *list(args)[1:]]
        elif name in HOST_TOOLS:
            raise RuntimeError(f"hermetic guard: a unit test ran '{name}' for real; pass a runner or stub the call (#473)")
        return original(self, args, *a, **kw)

    monkeypatch.setattr(subprocess.Popen, "__init__", guarded)


@pytest.fixture(autouse=True)
def _no_teleport_from_this_machine(monkeypatch):
    """The page shows Teleport when the workbench last built has it (compose/generated/workbench.yaml). A developer's own
    build must not change what a test sees; a test that wants Teleport sets webui.cluster.teleport_built itself."""
    try:
        from webui import cluster
    except ImportError:
        return
    monkeypatch.setattr(cluster, "teleport_built", lambda: False)
    monkeypatch.setattr(cluster, "jira_real_built", lambda: False)  # same for the real Jira box
    monkeypatch.setattr(cluster, "k8s_built", lambda: False)  # and the Kubernetes node group


@pytest.fixture(autouse=True)
def _no_saved_workbench_from_this_machine(monkeypatch, tmp_path_factory):
    """export.configured() and the CLI read the workbench last built; a developer's own selection (login nodes, parts) must not change a test."""
    from benchctl import workbench

    monkeypatch.setattr(workbench, "SAVED", tmp_path_factory.mktemp("nosaved") / "workbench.yaml")


@pytest.fixture(autouse=True)
def _no_app_reserve_by_default(monkeypatch, tmp_path):
    """The plan reserves memory for the app under test (benchctl/memory.py). Tests that count free memory exactly leave it out;
    a test of the reserve sets WORKBENCH_APP_MB itself."""
    from benchctl import memory
    monkeypatch.setenv("WORKBENCH_APP_MB", "0")
    monkeypatch.setattr(memory, "APP_FILE", tmp_path / "app_reserve.json")  # never the machine's real saved measurement
    monkeypatch.setattr(memory, "OVERHEAD_FILE", tmp_path / "overhead.json")  # nor the Docker overhead saved on this machine


@pytest.fixture(autouse=True)
def _no_git_hook_variables(monkeypatch):
    """Inside a pre-commit hook in a git worktree git exports an absolute GIT_DIR and GIT_INDEX_FILE. Tests that run `git` in a
    temporary directory would then act on the real worktree (stage files, commit "x"); in the main checkout GIT_DIR is relative
    and harmless, which hid it (#351)."""
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _services_answer(monkeypatch):
    """The fake runners in the `up` tests cannot answer `scontrol ping` or an HTTP probe; readiness has its own tests (test_ready.py)."""
    from benchctl import stack
    monkeypatch.setattr(stack, "_answering", lambda names, runner, path, wait: [])


@pytest.fixture(autouse=True)
def _no_docker_state_in_plans(monkeypatch):
    """`plan` subtracts what is already running (`docker ps`): with a developer's stack up, a plan that should not fit does. A test that wants running services passes them in."""
    from benchctl import workbench
    monkeypatch.setattr(workbench, "running_services", lambda: set())
