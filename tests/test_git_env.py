import os


def test_git_hook_variables_do_not_reach_a_test():
    """Inside a pre-commit hook in a git worktree they point at the real checkout; the autouse fixture in conftest.py
    removes them, so a temporary repo made by a test is its own repo (#351)."""
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"):
        assert var not in os.environ
