"""Impact selection for `verify.sh --changed` (issue 576): scripts/impact.py over tests/real/impact.yaml."""
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("impact", ROOT / "scripts" / "impact.py")
impact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(impact)
RULES = impact.load_rules()


def pick(*files):
    return impact.select(list(files), RULES)[0]


def test_every_live_module_is_in_a_rule():
    named = {t for r in RULES if r["tests"] != "ALL" for t in r["tests"]}
    live = {p.stem for p in (ROOT / "tests" / "real").glob("test_*.py")}
    assert not live - named, f"add these live modules to a rule in tests/real/impact.yaml: {sorted(live - named)}"
    assert not named - live, f"impact.yaml names modules that do not exist: {sorted(named - live)}"


def test_every_source_path_has_a_rule_or_selects_all():
    """A new folder must not silently select nothing: an unmatched path means ALL."""
    assert pick("brand_new_folder/x.py") == "ALL"


def test_docs_only_selects_nothing():
    assert pick("docs/guides/getting-started.md", "README.md", "docs/manual/img/a.png") == []


def test_unit_tests_only_select_nothing():
    assert pick("tests/test_docs.py", "tests/hostbin/docker", "webui/tests/js/a.test.mjs") == []


def test_a_changed_live_module_selects_itself():
    assert pick("tests/real/test_qos_real.py") == ["test_qos_real"]


def test_unknown_live_helper_files_select_all():
    assert pick("tests/real/helpers.py") == "ALL" and pick("tests/real/conftest.py") == "ALL"


def test_union_of_rules_and_first_match_wins():
    got = pick("benchctl/nhc.py", "benchctl/k8s.py")
    assert set(got) == {"test_nhc_real", "test_nhc_browser", "test_k8s_real", "test_wire_capture_real", "test_compose_real"}
    assert pick("images/node-base/nhc.d/checks.conf") == ["test_nhc_browser", "test_nhc_real"]  # not swallowed by images/node-base/**
    assert pick("images/node-base/Dockerfile") == "ALL"


def test_all_wins_over_a_module_list():
    assert pick("benchctl/nhc.py", "benchctl/stack.py") == "ALL"


def test_glob_rules():
    assert impact.matches("docs/a/b/c.md", "docs/**") and impact.matches("README.md", "*.md")
    assert not impact.matches("docs/README.md", "*.md")  # * does not cross a slash
    assert impact.matches("webui/app.py", "webui/*.py") and not impact.matches("webui/static/js/a.py", "webui/*.py")


def test_explain_names_file_rule_and_modules():
    _, why = impact.select(["governor/core.py", "x/y.py"], RULES)
    assert any(w.startswith("governor/core.py -> ") and "rule: governor" in w for w in why)
    assert any("no impact rule for x/y.py" in w for w in why)


def test_a_broken_rules_file_is_an_error(tmp_path):
    bad = tmp_path / "r.yaml"
    bad.write_text("- {paths: [a]}\n")
    try:
        impact.load_rules(bad)
    except ValueError:
        return
    raise AssertionError("a rule without `tests` must be refused")


def test_cli_prints_all_when_git_cannot_answer():
    r = subprocess.run(["python3", str(ROOT / "scripts" / "impact.py"), "--base", "no-such-ref-576"], capture_output=True, text=True, check=False)
    assert r.stdout.strip() == "ALL" and "selecting everything" in r.stderr


def test_changed_sees_committed_staged_and_untracked(tmp_path):
    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    (tmp_path / "a").write_text("1")
    git("add", "a")
    git("commit", "-qm", "a")
    git("checkout", "-qb", "work")
    (tmp_path / "b").write_text("1")
    git("add", "b")
    git("commit", "-qm", "b")
    (tmp_path / "a").write_text("2")
    (tmp_path / "c").write_text("1")
    impact.ROOT = tmp_path
    try:
        assert impact.changed("main") == ["a", "b", "c"]
    finally:
        impact.ROOT = ROOT


def test_real_jira_rides_along_with_all_only_when_the_change_names_it():
    def out(*files):
        return subprocess.run(["python3", str(ROOT / "scripts" / "impact.py"), "--files", *files], capture_output=True, text=True, check=True).stdout.split()
    assert out("benchctl/stack.py") == ["ALL"]  # an infrastructure change does not pull real Jira in
    assert out("benchctl/stack.py", "benchctl/jira_real.py") == ["ALL", "+test_jira_dc_real"]
    assert out("benchctl/jira_real.py") == ["test_jira_dc_real"]
    assert out("benchctl/stack.py", "tests/real/test_jira_dc_real.py") == ["ALL", "+test_jira_dc_real"]
