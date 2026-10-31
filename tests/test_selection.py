"""scripts/lib-selection.sh: verify.sh keeps the user's workbench selection across tests that forget their own (issue 162)."""
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib-selection.sh"


def bash(cwd: Path, script: str) -> str:
    r = subprocess.run(["bash", "-c", f'. "{LIB}"; {script}'], cwd=cwd, check=False, capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_the_selection_is_put_back_and_files_that_did_not_exist_are_removed(tmp_path):
    gen = tmp_path / "compose" / "generated"
    gen.mkdir(parents=True)
    (gen / "workbench.yaml").write_text("cpu_nodes: 4\n")
    (gen / "profile").write_text("workbench")
    out = bash(tmp_path, 'selection_save; echo "kept:$selection_kept"; '
                         'echo "cpu_nodes: 16" > compose/generated/workbench.yaml; rm compose/generated/profile; '
                         'echo x > compose/generated/sim.yaml; selection_restore')
    assert "kept: workbench.yaml profile" in out
    assert (gen / "workbench.yaml").read_text() == "cpu_nodes: 4\n" and (gen / "profile").read_text() == "workbench"
    assert not (gen / "sim.yaml").exists()  # created by the tests, not the user's


def test_nothing_saved_means_nothing_restored_and_the_tests_files_are_removed(tmp_path):
    (tmp_path / "compose" / "generated").mkdir(parents=True)
    out = bash(tmp_path, 'selection_save; echo "kept:$selection_kept"; echo x > compose/generated/workbench.yaml; selection_restore')
    assert "kept:" in out and "workbench.yaml" not in out
    assert not (tmp_path / "compose" / "generated" / "workbench.yaml").exists()


def test_verify_sh_saves_first_and_restores_on_exit():
    text = (LIB.parent / "verify.sh").read_text()
    assert text.index("selection_save") < text.index('step "validate.sh"')
    assert any("trap" in ln and "selection_restore" in ln and "EXIT" in ln for ln in text.splitlines())
