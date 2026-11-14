"""Declared storage (issue 461, ADR 0078): the mounts, the exporter's series, the df and lfs shims, the shared volume, the scenarios."""
import copy
import importlib.util
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import yaml

from benchctl import compose, hwinventory, scenario, storage

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


exporter = load("components/gpu-exporter-farm/exporter.py", "exporter_storage")
fakefs = load("images/node-base/fakefs.py", "fakefs")


def with_storage(**mounts):
    sim = copy.deepcopy(SIM)
    sim["storage"] = {"mounts": [{"path": p, **kw} for p, kw in mounts.items()]}
    return sim


def test_without_a_storage_section_two_mounts_apply_and_only_scratch_is_shared():
    mounts = storage.config(SIM)
    assert [m["path"] for m in mounts] == ["/home", "/scratch"] and [m["path"] for m in storage.shared(SIM)] == ["/scratch"]
    assert {m["fstype"] for m in mounts} == {"nfs4", "lustre"}


def test_every_mistake_gets_its_own_message():
    bad = [({"/": {}}, "system directory"), ({"/scratch": {"kind": "zfs"}}, "kind must be one of"), ({"/scratch": {"used_pct": 120}}, "used_pct"),
           ({"/scratch": {"size_tb": 0}}, "size_tb"), ({"/scratch": {"colour": 1}}, "unknown key"), ({"/scratch": {"host_path": "/data"}}, "only for a shared mount"),
           ({"/scratch": {"shared": True, "host_path": "../x"}}, "absolute directory"), ({"/scratch": {"quota": {"tb": 1}}}, "quota takes gb and files")]
    for mounts, text in bad:
        with pytest.raises(storage.StorageError, match=text):
            storage.config(with_storage(**mounts))
    sim = with_storage()
    sim["storage"]["mounts"] = [{"path": "/data"}, {"path": "/data/"}]
    with pytest.raises(storage.StorageError, match="declared twice"):
        storage.config(sim)


def test_a_shared_mount_is_a_named_volume_or_a_bind_to_the_host_path():
    named, mounts = storage.volumes(with_storage(**{"/data": {"shared": True}}))
    assert named == {"shared-data": {}} and mounts == ["shared-data:/data"]
    named, mounts = storage.volumes(with_storage(**{"/data": {"shared": True, "host_path": "/srv/datasets"}}))
    assert named == {} and mounts == ["/srv/datasets:/data"]


def test_the_inventory_carries_the_mounts_and_the_users_and_no_host_path():
    inv = hwinventory.build(with_storage(**{"/data": {"shared": True, "host_path": "/srv/datasets"}}))
    assert [m["path"] for m in inv["storage"]["mounts"]] == ["/data"] and "host_path" not in inv["storage"]["mounts"][0]
    assert inv["users"] == [u["name"] for u in SIM["users"]]


def test_compose_mounts_the_shared_volume_on_nodes_and_logins_only():
    svc = compose.generate(SIM, compose.load_profile("min"))
    services = svc["services"]
    assert svc["volumes"]["shared-scratch"] == {}
    for name in ("cn001", "gpu001", "login"):
        assert "shared-scratch:/scratch" in services[name]["volumes"] and services[name]["environment"]["WORKBENCH_SHARED_MOUNTS"] == "/scratch", name
    assert not any("shared-scratch" in str(services[n].get("volumes")) for n in ("slurmctld", "prometheus") if n in services)


def test_a_shared_home_is_one_more_volume():
    sim = copy.deepcopy(SIM)
    sim["storage"] = {"mounts": [{"path": "/home", "shared": True}, {"path": "/scratch", "shared": True}]}
    services = compose.generate(sim, compose.load_profile("min"))["services"]
    assert services["cn001"]["environment"]["WORKBENCH_SHARED_MOUNTS"] == "/home /scratch"


def state(**kw):
    inv = hwinventory.build(SIM)
    return exporter.State({"cn001": 0, "cn002": 0, "gpu001": 8}, mounts=inv["storage"]["mounts"], users=["alice", "bob"], **kw)


def line(text, metric, node, mount):
    return [ln for ln in text.splitlines() if ln.startswith(metric + "{") and f'node="{node}"' in ln and f'mountpoint="{mount}"' in ln]


def test_the_series_follow_the_declaration_with_size_free_inodes_and_no_randomness():
    st = state()
    a, b = st.render(), st.render()
    size = line(a, "node_filesystem_size_bytes", "cn001", "/scratch")[0]
    assert size.endswith(f" {500 * 1024**4}") and 'fstype="lustre"' in size and 'device="mgs01@tcp:/scratch"' in size
    assert line(a, "node_filesystem_free_bytes", "cn001", "/scratch") == line(b, "node_filesystem_free_bytes", "cn001", "/scratch")  # not random any more
    assert line(a, "node_filesystem_files", "cn001", "/home")[0].endswith(" 2000000000")
    assert line(a, "node_filesystem_files_free", "cn001", "/home")[0].endswith(f" {int(2_000_000_000 * 0.70)}")
    assert line(a, "node_filesystem_device_error", "cn001", "/scratch")[0].endswith(" 0")


def test_a_fill_level_changes_the_series_and_the_view_until_reset():
    st = state()
    st.inject({"node": "cn001", "storage": {"mount": "/scratch", "used_pct": 99, "inodes_used_pct": 90}})
    free = line(st.render(only="cn002"), "node_filesystem_free_bytes", "cn002", "/scratch")[0]
    assert int(free.rsplit(" ", 1)[1]) == 500 * 1024**4 - int(500 * 1024**4 * 99 / 100)  # the mount is shared: every node sees it
    view = {m["path"]: m for m in st.storage_view("cn001")["mounts"]}
    assert view["/scratch"]["files_used"] == int(4_000_000_000 * 0.9) and view["/home"]["used"] == int(20 * 1024**4 * 0.35)
    st.inject({"node": "cn001", "storage": {"mount": "/scratch", "reset": True}})
    assert {m["path"]: m for m in st.storage_view("cn001")["mounts"]}["/scratch"]["files_used"] == int(4_000_000_000 * 0.25)


def test_a_stale_mount_drops_that_nodes_size_series_and_sets_the_device_error():
    st = state()
    st.inject({"node": "cn002", "storage": {"mount": "/scratch", "stale": True}})
    text = st.render()
    assert not line(text, "node_filesystem_size_bytes", "cn002", "/scratch") and line(text, "node_filesystem_size_bytes", "cn001", "/scratch")
    assert line(text, "node_filesystem_device_error", "cn002", "/scratch")[0].endswith(" 1")
    assert {m["path"]: m["stale"] for m in st.storage_view("cn002")["mounts"]}["/scratch"] is True
    st.inject({"node": "cn002", "clear": True})
    assert line(st.render(), "node_filesystem_size_bytes", "cn002", "/scratch")  # a node's clear brings the mount back


def test_injection_refuses_an_unknown_mount_or_a_level_out_of_range():
    st = state()
    for req in ({"mount": "/nope", "used_pct": 1}, {"mount": "/scratch", "used_pct": 101}):
        with pytest.raises(ValueError):
            st.inject({"node": "cn001", "storage": req})


def test_a_user_over_the_quota_is_marked_in_the_view():
    st = state()
    assert not st.user_quota("/scratch", "alice")["exceeded"]
    st.inject({"node": "cn001", "storage": {"mount": "/scratch", "user": "alice", "used_gb": 20500}})
    assert st.user_quota("/scratch", "alice")["exceeded"] and not st.user_quota("/scratch", "bob")["exceeded"]


def run_shim(fn, argv, view, monkeypatch, real_df=""):
    monkeypatch.setattr(fakefs, "view", lambda: view)
    monkeypatch.setattr(fakefs.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": real_df, "returncode": 0})())
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(argv)
    return code, out.getvalue(), err.getvalue()


def view_of(st=None, node="cn001"):
    return (st or state()).storage_view(node)["mounts"]


def test_df_prints_gnu_columns_for_a_declared_path(monkeypatch):
    code, out, _ = run_shim(fakefs.df, ["-h", "/scratch"], view_of(), monkeypatch)
    rows = [ln.split() for ln in out.splitlines()]
    assert code == 0 and rows[0] == ["Filesystem", "Size", "Used", "Avail", "Use%", "Mounted", "on"]
    assert rows[1] == ["mgs01@tcp:/scratch", "500T", "200T", "300T", "40%", "/scratch"]
    _, out, _ = run_shim(fakefs.df, ["-i", "/home"], view_of(), monkeypatch)
    assert out.splitlines()[0].split()[:5] == ["Filesystem", "Inodes", "IUsed", "IFree", "IUse%"] and out.splitlines()[1].split()[-2:] == ["30%", "/home"]
    _, out, _ = run_shim(fakefs.df, ["-T", "/scratch"], view_of(), monkeypatch)
    assert out.splitlines()[1].split()[1] == "lustre" and "1K-blocks" in out.splitlines()[0]


def test_df_without_a_path_lists_the_real_rows_and_the_declared_ones_aligned(monkeypatch):
    real = "Filesystem 1K-blocks Used Available Use% Mounted on\noverlay 100 50 50 50% /\n"
    _, out, _ = run_shim(fakefs.df, [], view_of(), monkeypatch, real)
    lines = out.splitlines()
    assert len(lines) == 4 and lines[1].startswith("overlay") and {ln.split()[-1] for ln in lines[1:]} == {"/", "/home", "/scratch"}
    assert len({ln.index("%") for ln in lines}) == 1  # the right-aligned Use% column lines up


def test_df_on_a_stale_mount_fails_the_way_the_filesystem_does(monkeypatch):
    st = state()
    st.inject({"node": "cn001", "storage": {"mount": "/scratch", "stale": True}})
    code, out, err = run_shim(fakefs.df, ["/scratch"], view_of(st), monkeypatch)
    assert code == 1 and out == "" and "df: /scratch: Cannot send after transport endpoint shutdown" in err
    st.inject({"node": "cn001", "storage": {"mount": "/home", "stale": True}})
    assert "Stale file handle" in run_shim(fakefs.df, ["/home"], view_of(st), monkeypatch)[2]


def test_df_hands_over_to_the_real_df_for_an_unknown_flag_or_when_nothing_answers(monkeypatch):
    called = []
    monkeypatch.setattr(fakefs.os, "execv", lambda path, argv: called.append(argv))
    monkeypatch.setattr(fakefs, "view", lambda: view_of())
    fakefs.df(["--output=source"])
    monkeypatch.setattr(fakefs, "view", lambda: None)
    fakefs.df(["-h", "/scratch"])
    assert called == [[fakefs.REAL_DF, "--output=source"], [fakefs.REAL_DF, "-h", "/scratch"]]


def test_lfs_df_lists_the_mdt_the_osts_and_a_summary_and_only_for_lustre(monkeypatch):
    code, out, _ = run_shim(fakefs.lfs, ["df", "-h", "/scratch"], view_of(), monkeypatch)
    names = [ln.split()[0] for ln in out.splitlines()]
    assert code == 0 and names[0] == "UUID" and names[1] == "scratch-MDT0000_UUID" and names[2:6] == [f"scratch-OST{i:04x}_UUID" for i in range(4)] and names[-1] == "filesystem_summary:"
    assert out.splitlines()[-1].split()[1:5] == ["500T", "200T", "300T", "40%"]
    code, _, err = run_shim(fakefs.lfs, ["df", "/home"], view_of(), monkeypatch)
    assert code == 1 and "not on a Lustre filesystem" in err
    _, out, _ = run_shim(fakefs.lfs, ["df", "-i", "/scratch"], view_of(), monkeypatch)
    assert out.splitlines()[-1].split()[1:5] == ["4000000000", "1000000000", "3000000000", "25%"]


def test_lfs_quota_shows_the_user_and_the_star_and_grace_when_over(monkeypatch):
    monkeypatch.setattr(fakefs.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(fakefs.pwd, "getpwnam", lambda n: type("P", (), {"pw_uid": 1001})())
    _, out, _ = run_shim(fakefs.lfs, ["quota", "-u", "alice", "-h", "/scratch"], view_of(), monkeypatch)
    assert out.splitlines()[0] == "Disk quotas for usr alice (uid 1001):" and "*" not in out
    st = state()
    st.inject({"node": "cn001", "storage": {"mount": "/scratch", "user": "alice", "used_gb": 20500}})
    code, out, err = run_shim(fakefs.lfs, ["quota", "-u", "alice", "/scratch"], view_of(st), monkeypatch)
    row = out.splitlines()[-1].split()
    assert code == 0 and row[1].endswith("*") and row[4] == "6d23h59m" and "over the quota" in err
    assert run_shim(fakefs.lfs, ["quota", "-g", "x", "/scratch"], view_of(), monkeypatch)[0] == 1
    assert run_shim(fakefs.lfs, ["setstripe"], view_of(), monkeypatch)[0] == 1


def test_human_rounds_up_like_gnu_df():
    assert fakefs.human(500 * 1024**4) == "500T" and fakefs.human(9.5 * 1024**3) == "9.5G" and fakefs.human(1500) == "1.5K" and fakefs.human(10 * 1024 + 1) == "11K"


def test_the_exporter_serves_the_view_for_any_node_name_including_a_login_node():
    st = state()
    handler = exporter.make_handler(st)
    sent = {}

    class H(handler):
        def __init__(self, path):
            self.path = path
        def _send(self, code, body, ctype="text/plain"):
            sent.update(code=code, body=body)

    H("/sim/storage?node=login").do_GET()
    assert sent["code"] == 200 and {m["path"] for m in json.loads(sent["body"])["mounts"]} == {"/home", "/scratch"}


def test_the_storage_scenarios_validate_and_their_expectations_read_the_view(monkeypatch):
    for name in ("disk-full", "quota-exceeded", "stale-mount"):
        assert not scenario.validate(scenario.load(name), SIM), name
    st = state()
    st.inject({"node": "cn001", "storage": {"mount": "/scratch", "used_pct": 99, "inodes_used_pct": 90, "user": "alice", "used_gb": 20500}})
    st.inject({"node": "cn002", "storage": {"mount": "/scratch", "stale": True}})
    monkeypatch.setattr(scenario, "storage_mount", lambda node, mount: next(m for m in st.storage_view(node)["mounts"] if m["path"] == mount))
    holds = lambda args: scenario.expectation({"storage.mount": args})[1]()
    assert holds({"node": "cn001", "mount": "/scratch", "min_used_pct": 99, "min_inodes_used_pct": 90, "quota_exceeded": "alice"})
    assert not holds({"node": "cn001", "mount": "/scratch", "min_used_pct": 100}) and not holds({"node": "cn001", "mount": "/scratch", "quota_exceeded": "bob"})
    assert holds({"node": "cn002", "mount": "/scratch", "stale": True}) and not holds({"node": "cn001", "mount": "/scratch", "stale": True})
    monkeypatch.setattr(scenario, "storage_mount", lambda node, mount: None)
    assert not holds({"node": "cn001", "mount": "/scratch"})


def test_remove_storage_state_removes_named_volumes_only_and_refuses_while_running(monkeypatch):
    from typer.testing import CliRunner

    from benchctl import cli, inventory

    calls = []
    assert storage.reset(SIM, runner=lambda cmd, **k: calls.append(cmd)) == ["workbench_shared-scratch"] and calls == [["docker", "volume", "rm", "-f", "workbench_shared-scratch"]]
    assert storage.reset(with_storage(**{"/data": {"shared": True, "host_path": "/srv/d"}}), runner=lambda *a, **k: calls.append(a)) == [] and len(calls) == 1  # a host directory is never touched
    monkeypatch.setattr(inventory, "service_rows", lambda: [{"name": "login", "state": "running"}])
    r = CliRunner().invoke(cli.app, ["remove", "storage-state", "--yes"])
    assert r.exit_code == 1 and "benchctl down" in r.output
