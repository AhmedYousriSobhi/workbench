import json
import subprocess
from pathlib import Path

from governor.core import Governor
from governor.executors import Executor, discover_docker
from governor.ladder import Ladder
from governor.memlock import MitigationPath
from governor.planner import Action, Group, undo
from governor.signals import GB, Signals


def fake_cgroup(tmp: Path, name: str, current: int = 1000) -> Path:
    d = tmp / name
    d.mkdir()
    for f in ("cgroup.freeze", "memory.high", "memory.reclaim"):
        (d / f).write_text("")
    (d / "memory.current").write_text(str(current))
    return d


def test_cgroup_writes(tmp_path):
    d = fake_cgroup(tmp_path, "cn1", 1000)
    ex = Executor([Group("cn1", 4)], {"cn1": d}, MitigationPath(), arrivals_flag=str(tmp_path / "flag"))
    ex(Action("freeze", "cn1", 3))
    assert (d / "cgroup.freeze").read_text() == "1"
    ex(Action("thaw", "cn1", 0))
    assert (d / "cgroup.freeze").read_text() == "0"
    ex(Action("throttle", "cn1", 2))
    assert (d / "memory.high").read_text() == "800"
    ex(Action("unthrottle", "cn1", 0))
    assert (d / "memory.high").read_text() == "max"
    ex(Action("reclaim", "cn1", 3))
    assert (d / "memory.reclaim").read_text() == "500"


def test_arrivals_flag(tmp_path):
    flag = tmp_path / "run" / "flag"
    ex = Executor([], {}, MitigationPath(), arrivals_flag=str(flag))
    ex(Action("stop-arrivals", "load", 1))
    assert flag.exists()
    ex(Action("resume-arrivals", "load", 0))
    assert not flag.exists()


def test_stop_save_use_cli():
    calls = []

    def fake(cmd, timeout=15):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ex = Executor([Group("cn1", 4), Group("vm", 6, "vm")], {}, MitigationPath(), run_cmd=fake)
    ex(Action("stop", "cn1", 4))
    ex(Action("save", "vm", 4))
    assert calls[0][:2] == ["docker", "stop"] and calls[1][:2] == ["virsh", "managedsave"]


def test_discover_docker_parses_inspect():
    insp = [{"Name": "/cn1", "Id": "abc", "Config": {"Labels": {"hpcsim.class": "P4"}},
             "HostConfig": {"CgroupParent": "hpcsim-p4.slice"}}]

    def fake(cmd, timeout=15):
        out = "abc\n" if cmd[1] == "ps" else json.dumps(insp)
        return subprocess.CompletedProcess(cmd, 0, out, "")

    groups, cgs = discover_docker(fake)
    assert groups == [Group("cn1", 4)]
    assert cgs["cn1"] == Path("/sys/fs/cgroup/hpcsim-p4.slice/docker-abc.scope")


def test_undo_reverses_in_reverse_order():
    groups = [Group("a", 4), Group("b", 5)]
    verbs = [(a.verb, a.target) for a in undo(3, 0, groups)]
    assert ("thaw", "a") in verbs and ("unthrottle", "b") in verbs and ("resume-arrivals", "*") in verbs
    assert not any(v == "stop" for v, _ in verbs)
    assert undo(0, 3, groups) == []
    assert all(a.verb != "freeze" for a in undo(3, 2, groups))  # L2 keeps throttle


def test_failed_action_does_not_abort_plan():
    class S:
        def sample(self, now):
            return Signals(4 * GB, 0, 6.0)

    seen = []

    def ex(a):
        seen.append(a.verb)
        if a.verb == "freeze":
            raise RuntimeError("boom")

    g = Governor(S(), Ladder(), [Group("a", 4), Group("b", 5)], ex, dry_run=False)
    g.step(0)
    assert "stop" in seen and any(m.startswith("FAILED") for m in g.audit)


def test_deescalation_runs_undo():
    class S:
        s = Signals(4 * GB, 0, 6.0)

        def sample(self, now):
            return self.s

    s = S()
    g = Governor(s, Ladder(), [Group("a", 4)], lambda a: None, dry_run=True)
    g.step(0)
    s.s = Signals(4 * GB, 0, 0)
    for t in (20, 80, 140, 200, 260):
        g.step(t)
    assert any("thaw a" in m for m in g.audit) and any("resume-arrivals" in m for m in g.audit)


def test_reclaim_eagain_is_partial_not_failure(tmp_path):
    d = fake_cgroup(tmp_path, "cn1", 1000)

    class Eagain(MitigationPath):
        def write(self, path, data):
            raise BlockingIOError(11, "Resource temporarily unavailable")

    ex = Executor([Group("cn1", 4)], {"cn1": d}, Eagain())
    ex(Action("reclaim", "cn1", 3))  # must not raise
