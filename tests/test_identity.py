"""scripts/check-identity.sh and its hooks: the placeholder identity that mis-credited commits is refused; values are never printed."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check-identity.sh"
HOOKS = ROOT / ".githooks"
pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash")

OWNER = "octo" + "owner"  # built from parts so this file trips no scan
GOOD = f"123456+{OWNER}@users.noreply.github.com"
STRANGER = "1+x" + "@users.noreply.github.com"  # the incident: a placeholder whose ID belongs to someone else
CORP = "alice" + "@" + "corp.io"


def sh(cwd, *cmd, env=None, stdin=None):
    # Run from a git hook, GIT_DIR / GIT_INDEX_FILE point at the real repository: drop git's own variables.
    base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, input=stdin,
                          env={**base, **(env or {})}, check=False)


def fake_gh(tmp_path, real_id):
    d = tmp_path / f"bin{real_id}"
    d.mkdir(parents=True, exist_ok=True)
    g = d / "gh"
    g.write_text(f"#!/bin/sh\necho {real_id}\n")
    g.chmod(0o755)
    return {"PATH": f"{d}:{os.environ['PATH']}"}


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", fake_gh(tmp_path, 123456)["PATH"])  # hermetic: GitHub "knows" the owner's ID
    r = tmp_path / "r"
    (r / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, r / "scripts")
    sh(r, "git", "init", "-q", "-b", "work")
    sh(r, "git", "remote", "add", "origin", f"https://github.com/{OWNER}/proj.git")
    sh(r, "git", "config", "user.name", "Octo Owner")
    sh(r, "git", "config", "user.email", GOOD)
    return r


def check(r, *args, env=None):
    return sh(r, "bash", "scripts/check-identity.sh", *args, env=env)


def set_identity(r, name, email):
    sh(r, "git", "config", "user.name", name)
    sh(r, "git", "config", "user.email", email)


def test_the_owner_identity_passes(repo):
    assert check(repo).returncode == 0


def test_the_incident_identity_is_refused_without_echoing_it(repo):
    set_identity(repo, "x", STRANGER)
    out = check(repo)
    assert out.returncode == 1 and "IDENTITY:" in out.stderr and "Fix: git config" in out.stderr
    assert STRANGER not in out.stderr and STRANGER not in out.stdout


@pytest.mark.parametrize("name", ["x", "You", "test", ""])
def test_placeholder_names_are_refused_even_with_the_owner_email(repo, name):
    set_identity(repo, name or " ", GOOD)
    if not name:
        sh(repo, "git", "config", "user.name", "")
    assert check(repo).returncode == 1


def test_another_account_and_non_noreply_are_refused(repo):
    set_identity(repo, "Octo Owner", "99+someone" + "@users.noreply.github.com")
    assert check(repo).returncode == 1
    set_identity(repo, "Octo Owner", CORP)
    out = check(repo)
    assert out.returncode == 1 and CORP not in out.stderr


def test_environment_overrides_are_checked_too(repo):
    out = check(repo, env={"GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": STRANGER})
    assert out.returncode == 1 and "author identity refused" in out.stderr


def test_allow_file_admits_another_contributor_but_comments_do_not(repo):
    set_identity(repo, "Alice A", CORP)
    allow = repo / ".git" / "identity-allow"
    allow.write_text("# only a comment\n\n")
    assert check(repo).returncode == 1
    allow.write_text("# a contributor\n^alice@corp\\.io$\n")
    assert check(repo).returncode == 0


def commit(r, env=None):
    (r / "f").write_text(os.urandom(4).hex())
    sh(r, "git", "add", "f")
    return sh(r, "git", "commit", "-q", "-m", "m", "--no-verify", env=env)


def test_unpushed_commits_are_checked_and_pushed_ones_are_not(repo):
    bad = {"GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": STRANGER, "GIT_COMMITTER_NAME": "x", "GIT_COMMITTER_EMAIL": STRANGER}
    assert commit(repo, env=bad).returncode == 0
    out = check(repo, "--unpushed")
    assert out.returncode == 1 and "author refused" in out.stderr and "committer refused" in out.stderr
    sh(repo, "git", "update-ref", "refs/remotes/origin/work", "HEAD")  # now a remote has it
    assert check(repo, "--unpushed").returncode == 0
    assert commit(repo).returncode == 0  # a good commit on top
    assert check(repo, "--unpushed").returncode == 0


def test_pre_commit_hook_refuses_a_wrong_identity(repo):
    shutil.copy(ROOT / "scripts" / "check-privacy.sh", repo / "scripts")
    set_identity(repo, "x", STRANGER)
    out = sh(repo, "bash", str(HOOKS / "pre-commit"))
    assert out.returncode == 1 and "IDENTITY:" in out.stderr and "check-privacy" not in out.stderr


def test_pre_push_hook_refuses_unpushed_commits_with_a_wrong_identity(repo):
    shutil.copy(ROOT / "scripts" / "check-privacy.sh", repo / "scripts")
    bad = {"GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": STRANGER, "GIT_COMMITTER_NAME": "x", "GIT_COMMITTER_EMAIL": STRANGER}
    commit(repo, env=bad)
    out = sh(repo, "bash", str(HOOKS / "pre-push"), stdin="refs/heads/work abc refs/heads/work def\n")
    assert out.returncode == 1 and "IDENTITY:" in out.stderr


def test_the_id_in_the_address_must_be_the_accounts_id_when_github_answers(repo, tmp_path):
    """GitHub credits by ID: the owner's login with someone else's ID would still mis-credit the commit."""
    set_identity(repo, "Octo Owner", f"1+{OWNER}@users.noreply.github.com")
    out = check(repo, env=fake_gh(tmp_path, 123456))
    assert out.returncode == 1 and "ID in the noreply address" in out.stderr
    set_identity(repo, "Octo Owner", GOOD)
    assert check(repo, env=fake_gh(tmp_path, 123456)).returncode == 0
