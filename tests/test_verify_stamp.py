"""scripts/lib-stamp.sh: validate.sh's pass stamp lets verify.sh skip a second run, but never on a changed tree (issue 237)."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sh(script: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f". {ROOT}/scripts/lib-stamp.sh; {script}"], cwd=cwd, capture_output=True, text=True, check=False)


def repo(tmp_path: Path) -> Path:
    for cmd in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"], ["config", "commit.gpgsign", "false"]):
        subprocess.run(["git", *cmd], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("one\n")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=tmp_path, check=True)
    return tmp_path


def test_a_fresh_stamp_matches_and_reports_its_age(tmp_path):
    r = repo(tmp_path)
    assert sh("stamp_fresh", r).returncode == 1  # no stamp yet
    assert sh("stamp_write", r).returncode == 0
    out = sh("stamp_fresh", r)
    assert out.returncode == 0 and out.stdout.strip().isdigit()


def test_any_change_invalidates_the_stamp(tmp_path):
    r = repo(tmp_path)
    sh("stamp_write", r)
    (r / "a.txt").write_text("two\n")  # tracked edit
    assert sh("stamp_fresh", r).returncode == 1
    sh("stamp_write", r)
    assert sh("stamp_fresh", r).returncode == 0
    (r / "new.py").write_text("x = 1\n")  # untracked file
    assert sh("stamp_fresh", r).returncode == 1
    sh("stamp_write", r)
    (r / "new.py").write_text("x = 2\n")  # untracked content change
    assert sh("stamp_fresh", r).returncode == 1


def test_the_coverage_floor_is_part_of_the_key(tmp_path):
    r = repo(tmp_path)
    sh("stamp_write", r)
    assert sh("COV_FLOOR=50 stamp_fresh", r).returncode == 1


def test_verify_documents_the_new_flags():
    text = (ROOT / "scripts" / "verify.sh").read_text()
    assert "--revalidate" in text and "--skip-browser" in text and "TIME  total" in text
