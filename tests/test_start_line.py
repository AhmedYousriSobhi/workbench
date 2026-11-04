"""`benchctl start` names what it starts and what it replaces (#215)."""
from benchctl import workbench


def saved(**kw):
    return workbench.Spec(**{"cpu_nodes": 2, "gpu_nodes": 3, "parts": ["jira", "bmc"], **kw})


def test_nothing_saved_just_says_what_starts():
    assert workbench.start_line(None, None, False) == "Starting the min size preset."
    assert workbench.start_line(None, "std", False) == "Starting the std size preset."
    assert workbench.start_line(None, None, True) == "Starting the default workbench plus the parts you named."


def test_a_saved_workbench_is_named_as_replaced():
    line = workbench.start_line(saved(), None, False)
    assert "min size preset" in line and "replaces the workbench last built" in line
    assert "2 compute + 3 GPU nodes" in line and "jira, bmc" in line and "apply -f FILE" in line


def test_parts_start_from_the_default_not_the_saved_one():
    assert "default workbench plus the parts" in workbench.start_line(saved(), None, True)


def test_a_saved_workbench_with_no_parts_reads_plainly():
    assert "parts none" in workbench.start_line(saved(parts=[]), "min", False)
