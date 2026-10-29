"""scripts/check-privacy.sh and the tracked git hooks: every rule fires, placeholders pass, values are never printed."""
import getpass
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check-privacy.sh"
HOOKS = ROOT / ".githooks"
pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash")

# Built from parts so this file does not trip the guard it tests.
HOME_PATH = "/ho" + "me/" + "alice" + "/work"
TOKEN = "gh" + "p_" + "A" * 30
EMAIL = "alice" + "@" + "corp.io"
NOREPLY = "1+x" + "@users.noreply.github.com"


def sh(cwd, *cmd, env=None, stdin=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, input=stdin,
                          env={**os.environ, **(env or {})}, check=False)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "r"
    (r / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, r / "scripts")
    sh(r, "git", "init", "-q", "-b", "work")
    sh(r, "git", "config", "user.email", NOREPLY)
    sh(r, "git", "config", "user.name", "x")
    return r


def stage(r, name, text="hello\n"):
    p = r / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    sh(r, "git", "add", "-f", name)


def staged(r, *args, env=None):
    return sh(r, "bash", "scripts/check-privacy.sh", "--staged", *args, env=env)


def test_clean_commit_passes(repo):
    stage(repo, "a.md", "nothing personal here\n")
    out = staged(repo)
    assert out.returncode == 0 and "privacy: ok" in out.stdout


@pytest.mark.parametrize("text,rule", [
    (f"see {HOME_PATH}/x\n", "home-path"),
    (f"mail {EMAIL}\n", "email"),
    (f"token {TOKEN}\n", "secret"),
    ("-----BEGIN RSA PRIVATE " + "KEY-----\n", "secret"),
    ("url https://bob:hunter2" + "@host.io/x\n", "secret"),
])
def test_each_content_rule_fires_without_echoing_the_value(repo, text, rule):
    stage(repo, "doc.md", text)
    out = staged(repo)
    assert out.returncode == 1
    assert f"doc.md:1 [{rule}]" in out.stderr
    leaked = [v for v in (HOME_PATH, EMAIL, TOKEN, "hunter2") if v in out.stdout + out.stderr]
    assert not leaked


@pytest.mark.parametrize("text", [
    "/ho" + "me/user/x and /ho" + "me/you/y\n",  # placeholders
    "mail someone@example.com or " + NOREPLY + "\n",
    "see https://lore.kernel.org/all/20230601" + "@gmail.com/\n",  # an email inside a URL
    "clone git" + "@github.com:owner/repo.git\n",
    '"Members' + '@odata.count": 2\n',  # Redfish JSON key
    "url https://user:pw" + "@host/x\n",  # placeholder user in a documented example
])
def test_placeholders_and_public_strings_pass(repo, text):
    stage(repo, "doc.md", text)
    assert staged(repo).returncode == 0


def test_allowlist_accepts_an_exact_public_string(repo):
    stage(repo, "doc.md", f"maintainer {EMAIL}\n")
    assert staged(repo).returncode == 1
    (repo / ".privacy-allow").write_text(f"# public\n{EMAIL}\n")
    assert staged(repo).returncode == 0


def test_local_username_is_blocked_unless_generic(repo):
    me = getpass.getuser()
    if len(me) < 4 or me in {"root", "admin", "ubuntu", "user", "runner", "vagrant", "hpcsim", "test", "docker"}:
        pytest.skip("generic account name")
    stage(repo, "doc.md", f"written by {me} on this box\n")
    out = staged(repo)
    assert out.returncode == 1 and "[local-username]" in out.stderr and me not in out.stderr.replace("local-username", "")


def test_local_deny_file_blocks_private_terms(repo):
    (repo / ".git" / "privacy-deny").write_text("# mine\nacme-internal-[0-9]+\n")
    stage(repo, "doc.md", "ticket acme-internal-42\n")
    out = staged(repo)
    assert out.returncode == 1 and "[local-deny]" in out.stderr and "acme-internal-42" not in out.stderr


@pytest.mark.parametrize("name,bad", [
    (".claude/skills/x/SKILL.md", True), ("graphify-out/graph.json", True), (".env", True), ("id_ed25519", True),
    ("keys/server.pem", True), (".env.example", False), ("docs/claude.md", False),
])
def test_local_only_paths(repo, name, bad):
    stage(repo, name)
    out = staged(repo)
    assert (out.returncode == 1) is bad
    if bad:
        assert f"{name} [local-only-path]" in out.stderr


def test_symlinks_must_stay_inside_the_repo(repo):
    (repo / "real.txt").write_text("x\n")
    (repo / "ok").symlink_to("real.txt")
    sh(repo, "git", "add", "real.txt", "ok")
    assert staged(repo).returncode == 0
    (repo / "skills").symlink_to("/ho" + "me/alice/.agents/skills/")
    (repo / "up").symlink_to("../elsewhere")
    sh(repo, "git", "add", "skills", "up")
    out = staged(repo)
    assert out.returncode == 1 and "skills [symlink-leaves-repo]" in out.stderr and "up [symlink-leaves-repo]" in out.stderr
    assert "alice" not in out.stderr


def test_identity_must_be_noreply_when_committing(repo):
    stage(repo, "a.md")
    sh(repo, "git", "config", "user.email", EMAIL)
    out = staged(repo)
    assert out.returncode == 1 and "[identity-not-noreply]" in out.stderr and EMAIL not in out.stderr
    assert staged(repo, env={"PRIVACY_ALLOW_EMAIL": "1"}).returncode == 0


def test_commit_message_is_scanned(repo, tmp_path):
    msg = tmp_path / "MSG"
    msg.write_text("fix: thing (#1)\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n")
    assert sh(repo, "bash", "scripts/check-privacy.sh", "--message", str(msg)).returncode == 0
    msg.write_text(f"fix: thing (#1)\n\nused {TOKEN} to test\n")
    out = sh(repo, "bash", "scripts/check-privacy.sh", "--message", str(msg))
    assert out.returncode == 1 and "[secret]" in out.stderr and TOKEN not in out.stderr


def test_the_tracked_tree_is_clean():
    out = sh(ROOT, "bash", "scripts/check-privacy.sh", "--all")
    assert out.returncode == 0, out.stderr


# ---- hooks ----

@pytest.mark.parametrize("text,ok", [
    ("fix: thing (#12)\n", True), ("docs: x\n\nCloses #7\n", True), ("Merge branch 'dev' into x\n", True),
    ("fixup! fix: thing\n", True), ("fix: thing without an issue\n", False), ("# only a comment\n", False),
])
def test_commit_msg_hook_requires_an_issue_reference(tmp_path, text, ok):
    msg = tmp_path / "MSG"
    msg.write_text(text)
    out = sh(ROOT, "bash", str(HOOKS / "commit-msg"), str(msg))
    assert (out.returncode == 0) is ok, out.stderr


def test_commit_msg_hook_runs_the_privacy_scan(tmp_path):
    msg = tmp_path / "MSG"
    msg.write_text(f"fix: thing (#1)\n\n{HOME_PATH}\n")
    assert sh(ROOT, "bash", str(HOOKS / "commit-msg"), str(msg)).returncode == 1


@pytest.mark.parametrize("ref,ok", [("refs/heads/feat/x", True), ("refs/heads/main", False), ("refs/heads/dev", False)])
def test_pre_push_hook_refuses_main_and_dev(ref, ok):
    out = sh(ROOT, "bash", str(HOOKS / "pre-push"), stdin=f"refs/heads/x abc {ref} def\n")
    assert (out.returncode == 0) is ok, out.stderr


@pytest.mark.parametrize("branch", ["main", "dev"])
def test_pre_commit_hook_refuses_protected_branches(tmp_path, branch):
    r = tmp_path / "r"
    r.mkdir()
    sh(r, "git", "init", "-q", "-b", branch)
    out = sh(r, "bash", str(HOOKS / "pre-commit"))
    assert out.returncode == 1 and "Refusing to commit" in out.stderr
