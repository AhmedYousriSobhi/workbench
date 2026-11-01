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
