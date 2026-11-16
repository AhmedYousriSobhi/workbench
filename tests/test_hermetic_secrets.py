"""The unit suite never touches the checkout's real compose/generated/secrets (#750): its stub SSH keys once crashed sshd in a live run."""
from pathlib import Path

from benchctl import stack

REAL = Path(__file__).resolve().parent.parent / "compose" / "generated" / "secrets"


def _snapshot(root: Path):
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob("*")) if root.exists() else None


def test_secret_paths_point_at_tmp_in_unit_tests(tmp_path):
    for p in (stack.SSH_DIR, stack.SLURM_KEYS, stack.BMC_CREDENTIALS):
        assert not Path(p).resolve().is_relative_to(REAL.parent), p
        assert Path(p).is_absolute()


def test_default_ensure_calls_leave_real_secrets_untouched():
    before = _snapshot(REAL)
    d = stack.ensure_ssh_keys()  # default path, the ssh-keygen stand-in writes the placeholder keys
    stack.ensure_slurm_keys()
    stack.ensure_bmc_credentials()
    assert (d / "id_ed25519").exists()
    assert _snapshot(REAL) == before
