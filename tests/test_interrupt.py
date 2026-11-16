"""scripts/lib-interrupt.sh: Ctrl+C, SIGTERM or SIGHUP on a wrapper stops its whole child tree, then its cleanup runs (issue 819).

A fake wrapper starts a fake long child: a shell that starts a Python grandchild. Nothing here touches Docker or the cluster."""
import os
import signal
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "scripts" / "lib-interrupt.sh"
SEL = ROOT / "scripts" / "lib-selection.sh"

# The grandchild: MODE teardown ends 0.5 s after SIGINT (pytest running its fixtures' teardown); MODE ignore never ends on
# SIGINT (a teardown that hangs). It writes its pid, then a line per SIGINT.
GRANDCHILD = """
import os, signal, sys, time
out, mode = sys.argv[1], sys.argv[2]
def on_int(*_):
    open(out + "/gc-int", "a").write("int\\n")
    if mode == "teardown":
        time.sleep(0.5); open(out + "/teardown", "w").write("ran"); sys.exit(0)
signal.signal(signal.SIGINT, on_int)
open(out + "/gc.pid", "w").write(str(os.getpid()))
while True:
    time.sleep(0.1)
"""

CHILD = """#!/usr/bin/env bash
# the long step: a grandchild in the same session, like pytest's own subprocesses
python3 "$OUT/gc.py" "$OUT" "$MODE" &
echo $$ > "$OUT/child.pid"
until [ -s "$OUT/gc.pid" ]; do sleep 0.05; done
[ "$MODE" = exit ] && { sleep 30 & exit 3; }   # ends on its own with code 3, leaving the grandchild and a sleep behind
wait
"""

WRAPPER = """
. "$LIB"; . "$SEL"
cleanup() { echo once >> "$OUT/cleanup"; [ -n "${SELECTION_DIR:-}" ] && echo restored >> "$OUT/restore"; selection_restore; IR_DONE="restored"; }
ir_trap cleanup
selection_save
ir_bg "$OUT/child.sh" >"$OUT/child.log" 2>&1
echo "$IR_PID" > "$OUT/group"
ir_wait "$IR_PID"
"""


def _pgid_members(pgid: int) -> list[int]:
    found = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                stat = (d / "stat").read_text()
            except OSError:
                continue
            fields = stat.rsplit(")", 1)[1].split()  # after "pid (comm)": state ppid pgrp ...
            if int(fields[2]) == pgid and fields[0] != "Z":
                found.append(int(d.name))
    return found


def _wait_for(path: Path, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while not path.exists() or not path.read_text().strip():
        assert time.monotonic() < end, f"{path.name} never appeared"
        time.sleep(0.05)


def _start(tmp_path: Path, mode: str, grace: int = 30) -> subprocess.Popen:
    (tmp_path / "gc.py").write_text(GRANDCHILD)
    child = tmp_path / "child.sh"
    child.write_text(CHILD)
    child.chmod(0o755)
    (tmp_path / "sel").mkdir()
    (tmp_path / "sel" / "workbench.yaml").write_text("cpu_nodes: 4\n")
    env = {**os.environ, "LIB": str(LIB), "SEL": str(SEL), "OUT": str(tmp_path), "MODE": mode, "IR_GRACE": str(grace),
           "SELECTION_FILES": str(tmp_path / "sel" / "workbench.yaml")}
    env.pop("IR_MANAGED", None)
    # A session of its own so a stray signal never reaches the test runner; a default SIGINT even when the runner was started
    # with it ignored (a script's background job), since a shell cannot trap a signal ignored on entry.
    reset = "import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp('bash', ['bash', '-c', sys.argv[1]])"
    return subprocess.Popen(["python3", "-c", reset, WRAPPER], env=env, stderr=subprocess.PIPE, text=True, start_new_session=True)


def _finish(p: subprocess.Popen, tmp_path: Path, timeout: float = 20.0) -> tuple[int, str]:
    try:
        _, err = p.communicate(timeout=timeout)
        return p.returncode, err
    finally:
        for g in (p.pid, int((tmp_path / "group").read_text() or 0) if (tmp_path / "group").exists() else 0):
            if g:
                try:
                    os.killpg(g, signal.SIGKILL)  # only on a failure: the test must not leave its own tree behind either
                except ProcessLookupError:
                    pass


def _assert_nothing_left(tmp_path: Path) -> None:
    group = int((tmp_path / "group").read_text())
    assert _pgid_members(group) == [], "a process of the child tree is still running"
    assert int((tmp_path / "gc.pid").read_text()) not in _pgid_members(group)


def _ready(tmp_path: Path) -> None:
    _wait_for(tmp_path / "group")
    _wait_for(tmp_path / "gc.pid")
    _wait_for(tmp_path / "child.pid")


def _cleaned_up_once(tmp_path: Path) -> None:
    assert (tmp_path / "cleanup").read_text() == "once\n"
    assert (tmp_path / "restore").read_text() == "restored\n"
    assert (tmp_path / "sel" / "workbench.yaml").read_text() == "cpu_nodes: 4\n"


def test_ctrl_c_lets_the_child_tear_down_then_cleans_up(tmp_path):
    p = _start(tmp_path, "teardown")
    _ready(tmp_path)
    (tmp_path / "sel" / "workbench.yaml").write_text("cpu_nodes: 16\n")  # the tests changed the selection
    p.send_signal(signal.SIGINT)
    rc, err = _finish(p, tmp_path)
    assert rc == 130, err
    assert (tmp_path / "teardown").read_text() == "ran"  # exactly one SIGINT reached the grandchild, and it was not ignored
    assert (tmp_path / "gc-int").read_text() == "int\n"
    _assert_nothing_left(tmp_path)
    _cleaned_up_once(tmp_path)
    assert err.count("STOPPED") == 1 and "by SIGINT" in err and "ended after" in err and "restored" in err


def test_a_second_ctrl_c_kills_a_child_that_ignores_the_first_at_once(tmp_path):
    p = _start(tmp_path, "ignore", grace=60)
    _ready(tmp_path)
    p.send_signal(signal.SIGINT)
    _wait_for(tmp_path / "gc-int")  # the first one arrived and was ignored
    t0 = time.monotonic()
    p.send_signal(signal.SIGINT)
    rc, err = _finish(p, tmp_path)
    assert rc == 130, err
    assert time.monotonic() - t0 < 10  # not the 60 s grace
    _assert_nothing_left(tmp_path)
    _cleaned_up_once(tmp_path)
    assert "second signal" in err


def test_a_child_that_ignores_sigint_is_terminated_after_the_grace(tmp_path):
    p = _start(tmp_path, "ignore", grace=1)
    _ready(tmp_path)
    p.send_signal(signal.SIGINT)
    rc, err = _finish(p, tmp_path)
    assert rc == 130, err
    _assert_nothing_left(tmp_path)
    _cleaned_up_once(tmp_path)
    assert "did not end in 1s and were terminated" in err


def test_sigterm_stops_the_tree_and_exits_143(tmp_path):
    p = _start(tmp_path, "teardown")
    _ready(tmp_path)
    p.send_signal(signal.SIGTERM)
    rc, err = _finish(p, tmp_path)
    assert rc == 143, err
    assert (tmp_path / "teardown").read_text() == "ran"  # SIGTERM to the wrapper is still a SIGINT to the child: it tears down
    _assert_nothing_left(tmp_path)
    _cleaned_up_once(tmp_path)
    assert "by SIGTERM" in err


def test_a_normal_end_passes_the_code_through_and_leaves_nothing(tmp_path):
    p = _start(tmp_path, "exit")
    rc, err = _finish(p, tmp_path)
    assert rc == 3, err
    _assert_nothing_left(tmp_path)  # the stray sleep and the grandchild were in the child's session: stopped
    _cleaned_up_once(tmp_path)
    assert "STOPPED" not in err


def test_ir_run_inside_a_managed_tree_execs_in_place():
    script = f'. "{LIB}"; echo $$; IR_MANAGED=1 ir_run bash -c \'echo $$\''
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10, check=False)
    outer, inner = r.stdout.split()
    assert outer == inner  # no new session: the parent's helper stops it with the rest of the tree


def test_the_scripts_start_their_long_children_through_the_helper():
    verify = (ROOT / "scripts" / "verify.sh").read_text()
    assert ". scripts/lib-interrupt.sh" in verify and "ir_trap verify_cleanup" in verify
    assert 'ir_bg "$@"' in verify and "ir_bg bash -c" in verify  # the steps and validate.sh in the background
    assert ' & pid=$!' not in verify and ' & vpid=$!' not in verify
    for s in ("real-test.sh", "real-test-root.sh"):
        text = (ROOT / "scripts" / s).read_text()
        assert "lib-interrupt.sh" in text and "ir_run " in text and "exec python3" not in text and 'exec "$py"' not in text
