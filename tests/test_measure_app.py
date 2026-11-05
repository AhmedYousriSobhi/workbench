"""`benchctl measure app` and the saved reserve (issue 301): fake process table, no live app."""
import json

from typer.testing import CliRunner

from benchctl import cli, measure, memory, workbench

MIB = 1024**2


def proc(tmp_path, procs):
    """procs: pid -> (comm, cmdline, Pss kB)."""
    for pid, (comm, cmd, kb) in procs.items():
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode())
        (d / "smaps_rollup").write_text(f"Rss: {kb}\nPss: {kb} kB\n")
    return tmp_path


def test_match_by_name_cmdline_and_pid_and_sum_pss(tmp_path):
    p = proc(tmp_path, {10: ("MyApp", "/opt/myapp --x", 1024), 11: ("helper", "/opt/MYAPP/helper", 2048),
                        12: ("other", "/bin/other", 9999), 13: ("pidonly", "/bin/z", 512)})
    assert measure.app_pids(["myapp"], [], p, skip=set()) == [10, 11]
    assert measure.app_pids(["myapp"], [13], p, skip=set()) == [10, 11, 13]
    assert measure.app_pids(["myapp"], [], p, skip={11}) == [10]  # the benchctl command line itself is skipped
    assert measure.app_bytes(["myapp"], [], p) == (3072 * 1024, 2)
    assert measure.app_bytes(["nothing"], [], p) == (0, 0)


def test_breakdown_names_what_a_match_caught(tmp_path):
    p = proc(tmp_path, {10: ("MyApp", "/opt/myapp", 3072), 11: ("MyApp", "/opt/myapp --child", 1024), 12: ("helper", "/opt/myapp/helper", 512)})
    got = measure.app_breakdown(["myapp"], [], p)
    assert list(got) == ["MyApp", "helper"] and got["MyApp"] == (4096 * 1024, 2) and got["helper"] == (512 * 1024, 1)


def test_summary_peak_typical_and_suggestion():
    s = measure.app_summary([(0, 0), (1000 * MIB, 3), (1200 * MIB, 4), (1100 * MIB, 4)])
    assert s["peak"] == 1200 * MIB and s["typical"] == 1100 * MIB and s["processes"] == 4
    assert s["suggested_mb"] == 1400  # 1200 * 1.1 = 1320, up to 100
    assert measure.app_summary([(0, 0)]) == {}


def test_sampler_runs_for_the_duration_and_stops_on_ctrl_c():
    calls = []
    out = measure.sample_app([], [], 6, 2, take=lambda *a: (calls.append(1) or (MIB, 1)), sleep=lambda s: None)
    assert len(out) == 4

    def boom(s):
        raise KeyboardInterrupt
    assert len(measure.sample_app([], [], 0, 1, take=lambda *a: (MIB, 1), sleep=boom)) == 1


def test_precedence_given_then_saved_then_default(monkeypatch, tmp_path):
    monkeypatch.delenv(memory.APP_ENV)
    assert memory.app_reserve() == (memory.APP_MB, "assumed, not measured")
    memory.save_app(2500)
    mb, note = memory.app_reserve()
    assert mb == 2500 and note.startswith("measured on 20")
    monkeypatch.setenv(memory.APP_ENV, "1500")
    assert memory.app_reserve() == (1500, "given")
    monkeypatch.setenv(memory.APP_ENV, "0")
    assert memory.app_mb() == 0
    monkeypatch.delenv(memory.APP_ENV)
    memory.APP_FILE.unlink()  # deleting the file returns to the default
    assert memory.app_mb() == memory.APP_MB
    memory.APP_FILE.write_text("not json")
    assert memory.app_mb() == memory.APP_MB


def test_plan_says_where_the_number_comes_from(monkeypatch):
    monkeypatch.delenv(memory.APP_ENV)
    base = {"expected_mb": 1000, "need_mb": 1000 + 2500, "app_mb": 2500, "app_note": "measured on 2026-01-02",
            "running_mb": 0, "expected_nodes_mb": 90, "node_count": 6}
    ok, msg = workbench.verdict(base, 10000)
    assert ok and "2500 MB for the app (measured on 2026-01-02)" in msg
    ok, msg = workbench.verdict(base, 3000)
    assert not ok and "benchctl measure app" in msg


def test_cli_exit_codes_and_save(monkeypatch, tmp_path):
    r = CliRunner()
    assert r.invoke(cli.app, ["measure", "app"]).exit_code == 2
    monkeypatch.setattr(measure, "app_bytes", lambda m, p, proc=None: (0, 0))
    res = r.invoke(cli.app, ["measure", "app", "--match", "nosuchapp"])
    assert res.exit_code == 1 and "no running process matches nosuchapp" in res.output
    monkeypatch.setattr(measure, "app_bytes", lambda m, p, proc=None: (1000 * MIB, 2))
    monkeypatch.setattr(measure, "sleep", None, raising=False)
    monkeypatch.setattr(measure.time, "sleep", lambda s: None)
    res = r.invoke(cli.app, ["measure", "app", "--match", "x", "--duration", "4", "--interval", "2", "--save"])
    assert res.exit_code == 0 and "suggested --app-mb 1100" in res.output
    assert json.loads(memory.APP_FILE.read_text())["mb"] == 1100
