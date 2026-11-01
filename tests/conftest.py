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


@pytest.fixture(autouse=True)
def _no_saved_workbench_from_this_machine(monkeypatch, tmp_path_factory):
    """export.configured() and the CLI read the workbench last built; a developer's own selection (login nodes, parts) must not change a test."""
    from benchctl import workbench

    monkeypatch.setattr(workbench, "SAVED", tmp_path_factory.mktemp("nosaved") / "workbench.yaml")
