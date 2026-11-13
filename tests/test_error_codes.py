"""The error-code registry (SPEC AG-6, ADR 0065): one table, checked against the code. Hermetic."""
import re
from pathlib import Path

from benchctl import checks, errors

ROOT = Path(__file__).resolve().parent.parent
SRC = sorted((ROOT / "benchctl").glob("*.py"))
LITERAL = re.compile(r"WB-[A-Z0-9]+(?:-[A-Z0-9]+)*")


def literals_outside_registry():
    found = set()
    for p in SRC:
        text = p.read_text()
        if p.name == "errors.py":
            start = text.index("REGISTRY: dict")
            text = text[:start] + text[text.index("\n}\n", start):]  # the table itself is not a use
        found |= set(LITERAL.findall(text))
    return found - {"WB-DOCTOR"}  # the prefix doctor_code builds on


def test_every_code_is_well_formed_and_carries_its_fix():
    for code, (meaning, fix) in errors.REGISTRY.items():
        assert errors.CODE.match(code), code
        assert meaning.strip() and fix.strip(), f"{code} needs a meaning and a fix"


def test_every_code_used_in_the_source_is_registered_and_every_non_doctor_code_is_used():
    used = literals_outside_registry()
    assert used <= set(errors.REGISTRY), f"unregistered: {sorted(used - set(errors.REGISTRY))}"
    unused = {c for c in errors.REGISTRY if not c.startswith("WB-DOCTOR-")} - used
    assert not unused, f"registered but never raised: {sorted(unused)}"


def test_every_doctor_check_has_a_code(monkeypatch):
    monkeypatch.setattr(checks, "check_tsh", lambda *a, **k: checks.Check("tsh 17.0.1", checks.OK, "fine"))
    names = [c.name for c in checks.run_all()]
    assert names, "doctor ran no checks"
    missing = {errors.doctor_code(n) for n in names} - set(errors.REGISTRY)
    assert not missing, f"add to errors.REGISTRY: {sorted(missing)}"
    assert errors.doctor_code("tsh 17.0.1") == "WB-DOCTOR-TSH" and errors.doctor_code("cgroup v2") == "WB-DOCTOR-CGROUP-V2"


def test_the_codes_page_is_current():
    assert (ROOT / "docs" / "agents" / "ERROR-CODES.md").read_text() == errors.table(), "run: python -m benchctl.errors"


def test_render_is_unchanged_for_people_and_one_line_for_agents(monkeypatch):
    monkeypatch.delenv("BENCHCTL_AGENT", raising=False)
    assert errors.render("WB-REFUSED-MEMORY", "low", "REFUSED: low") == "REFUSED: low"
    monkeypatch.setenv("BENCHCTL_AGENT", "1")
    line = errors.render("WB-REFUSED-MEMORY", "low\non memory", "REFUSED: low")
    assert line == "WB-REFUSED-MEMORY: low on memory | fix: benchctl plan" and "\n" not in line
