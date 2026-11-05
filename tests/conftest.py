import pytest


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
