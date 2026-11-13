"""Stack keys, reuse and module order for the live suite (issue 576, V-3): tests/real/stack_reuse.py and the modules that opt in."""
import importlib.util
import re
from pathlib import Path

REAL = Path(__file__).resolve().parent / "real"
spec = importlib.util.spec_from_file_location("stack_reuse", REAL / "stack_reuse.py")
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)
SIM = {"slurm": {"nodes": 4}, "qos": [{"name": "a"}]}


def test_equal_inputs_give_equal_keys():
    assert sr.stack_key(SIM, "min", None, {}) == sr.stack_key({"qos": [{"name": "a"}], "slurm": {"nodes": 4}}, "min", None, {})  # key order is irrelevant


def test_the_key_changes_with_every_input():
    base = sr.stack_key(SIM, "min", None, {})
    assert sr.stack_key({**SIM, "qos": []}, "min", None, {}) != base            # sim content
    assert sr.stack_key(SIM, "std", None, {}) != base                           # profile
    assert sr.stack_key(SIM, "min", {"groups": ["accounting"]}, {}) != base     # selection and enabled parts
    assert sr.stack_key(SIM, "min", None, {"WORKBENCH_TEST_TIMERS": "1"}) != base  # test timers


def test_the_key_reads_the_environment_by_default(monkeypatch):
    monkeypatch.delenv("WORKBENCH_TEST_TIMERS", raising=False)
    plain = sr.stack_key(SIM, "min")
    monkeypatch.setenv("WORKBENCH_TEST_TIMERS", "1")
    assert sr.stack_key(SIM, "min") != plain


def test_order_is_shared_then_reuse_by_group_then_cold():
    iso = {"cold_a", "reuse_t1", "reuse_t2", "reuse_q", "cold_b"}
    mods = [("cold_a", False, ""), ("shared_x", False, ""), ("reuse_t1", True, "timers"), ("reuse_q", True, "qos"), ("reuse_t2", True, "timers"), ("cold_b", False, ""), ("shared_y", False, "")]
    got = [m for m, *_ in sorted(mods, key=lambda m: sr.order_key(m[0], iso, m[1], m[2]))]
    assert got == ["shared_x", "shared_y", "reuse_q", "reuse_t1", "reuse_t2", "cold_a", "cold_b"]  # sorted() is stable: ties keep file order


def test_a_reuse_flag_on_a_shared_module_changes_nothing():
    assert sr.order_key("shared", set(), True, "g") == (0, "")


def test_the_converted_modules_share_a_group_and_release_instead_of_stopping():
    for name in ("test_nhc_real", "test_slurmresilience_real"):
        src = (REAL / f"{name}.py").read_text()
        assert re.search(r"^STACK_REUSE = True", src, re.MULTILINE) and re.search(r'^STACK_GROUP = "timers"', src, re.MULTILINE), name
        assert "release()" in src and "stack.down" not in src, f"{name} must leave the stack to the next module"
        assert "test_timers" in src, f"{name}: the timers are what its key differs by"
