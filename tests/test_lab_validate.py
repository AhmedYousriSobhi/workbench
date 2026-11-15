"""Config lab validation (issue 552): render, result shape, each check through a fake executor, the governor verb and the CLI.

No container starts. The tool outputs under tests/fixtures/lab/tool-output are SYNTHETIC (written from the documented message shapes,
not captured); real captures are still to be taken with `./scripts/verify.sh --unit lab --clean`.
"""
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from benchctl import checks, cli, lab, lab_validate
from benchctl import lab_import as li
from governor.core import Governor
from governor.ladder import Ladder

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lab"
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
OUT = FIX / "tool-output"


def out(name: str) -> str:
    return (OUT / name).read_text()


class Fake:
    """Executor stand-in: routes a command to (returncode, output) by substring of its joined text; records every call."""

    def __init__(self, routes=None, running=("workbench-mariadb-1", "workbench-teleport-1", "workbench-k8s-apiserver-1")):
        self.routes, self.calls, self.running = routes or [], [], set(running)

    def __call__(self, argv, stdin=None, timeout=60):
        text = " ".join(argv)
        self.calls.append((argv, stdin))
        if argv[:3] == ["docker", "inspect", "-f"]:
            return (0, "true\n") if argv[-1] in self.running else (1, "Error: No such object")
        for key, reply in self.routes:
            if key in text or (stdin and key in stdin):
                return reply(argv, stdin) if callable(reply) else reply
        return 0, ""

    def text(self):
        return "\n".join(" ".join(a) + (f"\n<stdin>{s}" if s else "") for a, s in self.calls)


def make_lab(tmp_path, which="full", name="t1"):
    src = tmp_path / "export"
    shutil.copytree(FIX / which, src, dirs_exist_ok=True)
    root = tmp_path / "lab"
    lab.import_lab(name, src, root=root, sim=SIM, replace=True)
    return root, root / name


# ---- render
def test_render_keeps_lab_nodes_replaces_lab_owned_and_scrubbed_keys(tmp_path):
    root, d = make_lab(tmp_path)
    info = lab_validate.render("t1", SIM, root)
    conf = (d / "render" / "slurm.conf").read_text()
    keys = li.parse_slurm_conf(conf)["keys"]
    assert "hunter2" not in conf and "another-not-real" not in conf and "scrubbed" not in conf
    assert keys["SlurmctldHost"] != "ctl01" and keys["ClusterName"] == "workbench"  # lab-owned: the workbench's own value
    assert keys["PreemptType"] == "preempt/qos" and keys["PriorityWeightQOS"] == "1000"  # policy: production's
    assert {"StoragePass", "AuthInfo", "SlurmctldHost"} <= set(info["replaced"])
    assert "AccountingStorageType" not in info["restart_keys"] and "PreemptType" in info["restart_keys"]  # lab-owned since issue 553: the lab keeps its own accounting setup
    assert "AccountingStorageType" in info["replaced"]
    parsed = li.parse_slurm_conf(conf)
    assert len(parsed["nodes"]) == info["kept_nodes"] == 6 and all(n["State"] == "UNKNOWN" for n in parsed["nodes"])
    assert all(n["PartitionName"] for n in parsed["partitions"]) and (d / "render" / "cgroup.conf").is_file()
    assert (d / "render" / "topology.conf").is_file() and (d / "render" / "gres.conf").is_file()


def test_render_drops_partitions_and_switches_without_a_lab_node(tmp_path):
    root, d = make_lab(tmp_path)
    (d / "baseline" / "slurm.conf").write_text((d / "baseline" / "slurm.conf").read_text() + "PartitionName=ghost Nodes=cpu001\n")
    (d / "baseline" / "topology.conf").write_text("SwitchName=empty Nodes=zzz[1-2]\nSwitchName=leaf1 Nodes=cpu[001-004]\nSwitchName=top Switches=leaf1,empty\n")
    lab_validate.render("t1", SIM, root, factor=1.0)
    topo = (d / "render" / "topology.conf").read_text()
    assert "empty" not in topo and "leaf1" in topo and "Switches=leaf1" in topo and "empty" not in topo.split("Switches=")[1]


# ---- result shape and summary
def test_result_shape_and_summary_words():
    r = lab_validate.result("x", "cmd", "skipped", ["m"])
    assert set(r) >= {"tool", "command", "ok", "messages", "restart_required"} and r["ok"] is None
    ok, bad = lab_validate.result("a", "c", "passed"), lab_validate.result("b", "c", "failed")
    assert lab_validate.summarise([ok]) == "passed"
    assert lab_validate.summarise([ok, r]) == "passed with 1 skipped"  # never just "passed" when something was skipped
    assert lab_validate.summarise([ok, r, bad]).startswith("failed")
    assert lab_validate.line(r).startswith("SKIP  x: m")


# ---- slurmctld
def test_parse_slurmctld_started_with_errors_is_a_pass_with_warnings():
    started, fatal, warn = lab_validate.parse_slurmctld(out("slurmctld-ok.txt"))
    assert started and not fatal and len(warn) == 10 and "HealthCheckTimeout" in warn[0]  # capped at 10 (captured output)


def test_parse_slurmctld_fatal_fails_and_is_shown():
    started, fatal, _ = lab_validate.parse_slurmctld(out("slurmctld-fatal.txt"))
    assert not started and len(fatal) == 1 and "fatal: failed to initialize node selection plugin" in fatal[0]


def test_slurm_one_shot_is_isolated(tmp_path):
    ex = Fake([("slurmctld -D", (0, out("slurmctld-ok.txt")))])
    r = lab_validate.check_slurm(tmp_path, "ctl-lab", ["PreemptType"], ex)
    argv = next(c[0] for c in ex.calls if c[0][:2] == ["docker", "run"])
    text = " ".join(argv)
    net = argv[argv.index("--network") + 1]
    assert r["status"] == "passed" and r["restart_required"] and len(r["warnings"]) == 11 and "partition QOS" in r["warnings"][0]
    assert net.startswith("workbench-lab-validate-") and net != lab_validate.NETWORK and "--rm" in argv and "--memory" in argv and "--hostname" in argv
    assert "workbench.class" not in text and "/var/spool/slurmctld:" not in text  # never the live state, never a governed label
    assert text.count("-v ") == 1 and f"{tmp_path.resolve()}:/render:ro" in text  # only the rendered config, read-only
    assert "munged" in argv[-1] and "urandom" in argv[-1] and "munge.key" in argv[-1]  # its own munge key, started inside
    assert "StateSaveLocation" not in argv[-1]


def test_slurm_network_is_internal_per_run_and_removed_even_on_error(tmp_path):
    ex = Fake([("slurmctld -D", (0, out("slurmctld-ok.txt")))])
    lab_validate.check_slurm(tmp_path, "ctl-lab", [], ex)
    cmds = [c[0] for c in ex.calls]
    create = next(c for c in cmds if c[:3] == ["docker", "network", "create"])
    assert "--internal" in create and create[-1].startswith("workbench-lab-validate-") and create[-1] != lab_validate.NETWORK
    assert cmds[-1] == ["docker", "network", "rm", create[-1]] and cmds[-2][:2] == ["docker", "run"]
    boom = Fake()
    real = boom.__call__

    def exploding(argv, stdin=None, timeout=60):
        if argv[:2] == ["docker", "run"]:
            raise RuntimeError("docker died")
        return real(argv, stdin, timeout)

    with pytest.raises(RuntimeError):
        lab_validate.check_slurm(tmp_path, "ctl-lab", [], exploding)
    assert any(c[0][:3] == ["docker", "network", "rm"] for c in boom.calls)
    refused = Fake([("network create", (1, "no pool\n"))])
    r = lab_validate.check_slurm(tmp_path, "ctl-lab", [], refused)
    assert r["status"] == "failed" and "throwaway network" in r["messages"][0]


def test_slurmctld_started_line_before_a_late_fatal_is_a_failure():
    started, fatal, _ = lab_validate.parse_slurmctld(out("slurmctld-fatal.txt"))  # captured: "started on cluster" comes first
    assert "started on cluster" in out("slurmctld-fatal.txt") and not started and fatal and "node selection plugin" in fatal[0]


def test_slurm_fatal_fails_with_the_fatal_line(tmp_path):
    r = lab_validate.check_slurm(tmp_path, "ctl-lab", [], Fake([("slurmctld -D", (0, out("slurmctld-fatal.txt")))]))
    assert r["status"] == "failed" and "fatal: failed to initialize" in r["messages"][0] and r["restart_required"] is False
    r = lab_validate.check_slurm(tmp_path, "ctl-lab", [], Fake([("slurmctld -D", (0, "slurmctld: error: nothing useful\n"))]))
    assert r["status"] == "failed" and "within 20 s" in r["messages"][0]
    assert lab_validate.check_slurm(tmp_path, "bad host;", [], Fake())["status"] == "failed"


# ---- accounting
def accounting(tmp_path, ex):
    _, d = make_lab(tmp_path)
    return lab_validate.check_accounting(d, ex, d / "validate-tmp", tmp_path / "secrets"), d


def sacct_routes(db_reply=(0, ""), run=(0, "")):
    return [("CREATE DATABASE", db_reply), ("docker run", run)]


def test_accounting_skipped_when_it_is_not_running(tmp_path):
    r, _ = accounting(tmp_path, Fake(running=()))
    assert r["status"] == "skipped" and r["messages"][0] == "skipped: accounting is not running (start it with `benchctl up --with accounting`)"
    assert lab_validate.summarise([r]) == "passed with 1 skipped"


def test_accounting_passes_and_always_cleans_up(tmp_path):
    ex = Fake(sacct_routes(run=(0, out("sacctmgr-run-ok.txt"))) + [("SHOW DATABASES", (0, "lab_validate_old\nother\n"))])
    r, d = accounting(tmp_path, ex)
    assert r["status"] == "passed" and "2 account(s)" in r["messages"][0] and "2 QOS" in r["messages"][0]
    text = ex.text()
    assert "DROP DATABASE IF EXISTS `lab_validate_old`" in text and "other`" not in text  # leftovers first, nothing else
    assert re.search(r"CREATE DATABASE `lab_validate_[0-9a-f]{8}`", text) and re.search(r"DROP DATABASE IF EXISTS `lab_validate_[0-9a-f]{8}`", text)
    run = next(a for a, _ in ex.calls if a[:2] == ["docker", "run"])
    assert "--env-file" in run and "workbench.class" not in " ".join(run) and run[run.index("--network") + 1] == "workbench_default"
    assert any(a[:3] == ["docker", "rm", "-f"] for a, _ in ex.calls)
    script = run[-1]
    assert "DbdPort=6899" in script and "sacctmgr -i load file=/lab/sacctmgr.dump" in script and "commit" not in script.lower()
    assert "lab_validate_" in script and "hunter2" not in text
    qos = (d / "validate-tmp" / "qos.sh").read_text()
    assert "sacctmgr -i add qos high" in qos and "set Priority=1000" in qos and "Preempt=normal" in qos and "MaxTRESPerUser=gres/gpu=4" in qos
    assert qos.index("add qos high") < qos.index("modify qos")


def test_accounting_load_failure_and_missing_account_fail(tmp_path):
    bad = "@@QOS\n@@LOAD\n sacctmgr: error: Unknown option: Foo=1\n@@LOAD_RC=1\n@@AFTER\n@@END\n"
    r, _ = accounting(tmp_path, Fake(sacct_routes(run=(0, bad))))
    assert r["status"] == "failed" and "Unknown option" in r["messages"][0]
    short = out("sacctmgr-run-ok.txt").replace("Account - 'ml'", "Account - 'other'")
    r, _ = accounting(tmp_path, Fake(sacct_routes(run=(0, short))))
    assert r["status"] == "failed" and "account ml was not loaded" in r["messages"]
    changed = out("sacctmgr-run-ok.txt").replace("Fairshare=10", "Fairshare=7")
    r, _ = accounting(tmp_path, Fake(sacct_routes(run=(0, changed))))
    assert r["status"] == "passed" and any("Fairshare 10 -> 7" in w for w in r["warnings"])
    r, _ = accounting(tmp_path, Fake(sacct_routes(run=(0, "@@DBD_FAILED\nslurmdbd: fatal: no\n"))))
    assert r["status"] == "failed" and "did not come up" in r["messages"][0]


def test_accounting_without_routine_privileges_is_skipped_honestly(tmp_path):
    r, _ = accounting(tmp_path, Fake(sacct_routes(run=(0, out("sacctmgr-run-no-routine-grant.txt")))))  # captured
    assert r["status"] == "skipped" and "CREATE ROUTINE and ALTER ROUTINE" in r["messages"][0]
    assert lab_validate.summarise([r]) == "passed with 1 skipped"


def test_a_real_dump_lists_qos_first_and_sorts_the_account_qos_list(tmp_path):
    real = out("sacctmgr-run-ok.txt").split("@@AFTER\n")[1].split("@@END")[0]
    assert "QOS - 'normal'" in real and "QOS='high,normal'" in real  # captured shapes
    assert lab_validate.diff_dump(li_text(), real) == ([], [])  # QOS 'normal,high' vs 'high,normal' is no change


def li_text() -> str:
    return (FIX / "full" / "sacctmgr.dump").read_text()


def test_grace_time_goes_to_sacctmgr_in_seconds_and_gres_tres_are_declared():
    q = lab_validate.qos_script([{"Name": "high", "GraceTime": "00:00:30"}, {"Name": "n", "GraceTime": "1-00:01:00"}, {"Name": "m", "GraceTime": "90"}])
    assert "GraceTime=30" in q and "GraceTime=86460" in q and "GraceTime=90" in q and "00:00:30" not in q
    assert lab_validate.tres_names("MaxTRESPU=gres/gpu=4 GrpTRES=gres/gpu:h100=8,cpu=4 license/matlab=2") == ["gres/gpu", "gres/gpu:h100", "license/matlab"]
    plain = lab_validate.accounting_script("lab_validate_x", "prodsim")
    assert "slurmctld -D" not in plain and "SlurmctldHost=localhost" in plain  # sacctmgr needs one (captured)
    with_tres = lab_validate.accounting_script("lab_validate_x", "prodsim", ["gres/gpu"])
    assert "AccountingStorageTRES=gres/gpu" in with_tres and "slurmctld -D" in with_tres and "ClusterName=prodsim" in with_tres
    assert with_tres.index("slurmctld -D") < with_tres.index("sh /lab/qos.sh")


def test_accounting_drops_the_database_even_when_the_one_shot_raises(tmp_path):
    def boom(argv, stdin):
        raise RuntimeError("docker died")

    ex = Fake(sacct_routes(run=boom))
    with pytest.raises(RuntimeError):
        accounting(tmp_path, ex)
    assert re.search(r"DROP DATABASE IF EXISTS `lab_validate_[0-9a-f]{8}`", ex.text()) and "docker rm -f lab-validate-" in ex.text()


def test_accounting_that_cannot_create_the_scratch_database_fails_with_the_reason(tmp_path):
    r, _ = accounting(tmp_path, Fake(sacct_routes(db_reply=(1, "ERROR 1044 (42000): Access denied for user 'slurm'@'%' to database 'lab_validate_x'"))))
    assert r["status"] == "failed" and "could not create the scratch database" in r["messages"][0] and "Access denied" in r["messages"][0]


def test_every_accounting_check_drops_leftover_scratch_databases_first(tmp_path):
    ex = Fake(sacct_routes(run=(0, out("sacctmgr-run-ok.txt"))) + [("SHOW DATABASES", (0, "lab_validate_aa11bb22\nlab_validate_cc33dd44\n"))])
    accounting(tmp_path, ex)
    sqls = [s for a, s in ex.calls if s]
    drops = [i for i, s in enumerate(sqls) if s.startswith(("DROP DATABASE IF EXISTS `lab_validate_aa11bb22`", "DROP DATABASE IF EXISTS `lab_validate_cc33dd44`"))]
    create = next(i for i, s in enumerate(sqls) if s.startswith("CREATE DATABASE"))
    assert len(drops) == 2 and max(drops) < create


def test_qos_script_refuses_shell_characters():
    with pytest.raises(lab.LabError):
        lab_validate.qos_script([{"Name": "x;rm -rf /"}])
    with pytest.raises(lab.LabError):
        lab_validate.qos_script([{"Name": "x", "Priority": "1$(id)"}])


def _like(pattern: str) -> re.Pattern:
    """MariaDB database-name pattern semantics in a grant: `\\_` and `\\%` literal, `_` one character, `%` any run."""
    rx, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            rx += re.escape(pattern[i + 1])
            i += 1
        else:
            rx += {"_": ".", "%": ".*"}.get(c, re.escape(c))
        i += 1
    return re.compile(rx)


def test_grant_pattern_escapes_underscores_and_never_matches_the_accounting_database():
    from benchctl import slurmconf

    pat = _like(slurmconf.LAB_DB_PATTERN)
    assert slurmconf.LAB_DB_PATTERN == "lab\\_validate\\_%"
    assert pat.fullmatch("lab_validate_ab12cd34") and pat.fullmatch("lab_validate_")
    for other in ("slurm_acct_db", "labXvalidateXab12", "lab_validate", "mysql", "lab-validate-ab12"):
        assert not pat.fullmatch(other), other
    assert _like("lab_validate_%").fullmatch("labXvalidateXab12")  # unescaped, `_` would be a wildcard


def test_grant_gives_only_what_slurmdbd_needs_and_nothing_global():
    from benchctl import slurmconf

    sql = slurmconf.LAB_GRANT_SQL
    assert sql.startswith("GRANT CREATE, DROP, ALTER, INDEX, SELECT, INSERT, UPDATE, DELETE, LOCK TABLES, CREATE TEMPORARY TABLES ON ")
    assert "ON `lab\\_validate\\_%`.* TO 'slurm'@'%'" in sql
    assert "GRANT OPTION" not in sql.upper() and "*.*" not in sql and "ALL" not in sql.split("ON")[0].upper().split()


def test_the_grant_file_is_generated_for_fresh_databases_and_the_root_password_is_kept(tmp_path):
    from benchctl import compose, slurmconf

    out = tmp_path / "gen" / "docker-compose.yml"
    data = compose.generate(SIM, compose.load_profile("std"))
    compose.write(data, out, SIM, 16)
    assert (tmp_path / "gen" / "slurm" / "lab-grant.sql").read_text() == slurmconf.LAB_GRANT_SQL
    env = (tmp_path / "gen" / "secrets" / "mariadb.env")
    root = next(ln.split("=", 1)[1] for ln in env.read_text().splitlines() if ln.startswith("MARIADB_ROOT_PASSWORD="))
    assert len(root) >= 24 and "RANDOM_ROOT" not in env.read_text()
    live = next(ln.split("=", 1)[1] for ln in env.read_text().splitlines() if ln.startswith("MARIADB_DATABASE="))
    assert live == "slurm_acct_db" and not _like(slurmconf.LAB_DB_PATTERN).fullmatch(live)  # the stack's real accounting database is out of reach
    compose.write(data, out, SIM, 16)
    assert f"MARIADB_ROOT_PASSWORD={root}" in env.read_text()  # a running database keeps matching
    assert env.stat().st_mode & 0o077 == 0


# ---- promtool
def test_promtool_pass_and_fail_and_skip(tmp_path):
    _, d = make_lab(tmp_path)
    ex = Fake([("check rules", (0, out("promtool-ok.txt"))), ("test rules", (0, "Unit Testing:  /rules/alerts.test.yml\n  SUCCESS\n"))])
    a, b = lab_validate.check_promtool(d, ex)
    assert a["status"] == b["status"] == "passed" and a["messages"] == ["SUCCESS: 1 rules found"]
    call = " ".join(ex.calls[0][0])
    assert "--network none" in call and "--entrypoint promtool" in call and "alerts.yml" in call and "alerts.test.yml" not in call
    assert "/rules/alerts.test.yml" in " ".join(ex.calls[1][0])
    _, d2 = make_lab(tmp_path / "b", "broken", "b1")
    a, _ = lab_validate.check_promtool(d2, Fake([("check rules", (1, out("promtool-fail.txt")))]))
    assert a["status"] == "failed" and any("unclosed left parenthesis" in m for m in a["messages"]) and not any("/rules/" in m for m in a["messages"])
    shutil.rmtree(d / "baseline" / "rules")
    assert [r["status"] for r in lab_validate.check_promtool(d, Fake())] == ["skipped", "skipped"]


def test_promtool_image_is_the_pin_of_the_observability_file():
    assert f"image: {lab_validate.PROM_IMAGE}" in (ROOT / "compose" / "observability.yml").read_text()


# ---- kubectl
def test_kubernetes_skipped_pass_fail_and_namespace_downgrade(tmp_path):
    _, d = make_lab(tmp_path)
    assert lab_validate.check_kubernetes(d, SIM, Fake(running=()))["messages"][0].startswith("skipped: Kubernetes is not running")
    ex = Fake([("--dry-run=server", (0, "namespace/team-a created (server dry run)\npriorityclass.scheduling.k8s.io/batch-low created (server dry run)\n"))])
    r = lab_validate.check_kubernetes(d, SIM, ex)
    assert r["status"] == "passed" and "2 object(s)" in r["messages"][0]
    argv, stdin = ex.calls[-1]
    assert "--dry-run=server" in argv and "team-a" in stdin and "kind: Secret" not in stdin  # only what the import kept
    ns = 'Error from server (NotFound): error when creating "STDIN": namespaces "team-a" not found'
    r = lab_validate.check_kubernetes(d, SIM, Fake([("--dry-run=server", (1, ns))]))
    assert r["status"] == "passed" and "team-a" in r["warnings"][0]
    bad = 'The ResourceQuota "gpu-quota" is invalid: spec.hard: Invalid value'
    r = lab_validate.check_kubernetes(d, SIM, Fake([("--dry-run=server", (1, bad))]))
    assert r["status"] == "failed" and "invalid" in r["messages"][0]
    shutil.rmtree(d / "baseline" / "k8s")
    assert lab_validate.check_kubernetes(d, SIM, Fake())["status"] == "skipped"


# ---- Teleport
def test_role_structure_flags_typos_kind_version_and_missing_spec():
    good = {"kind": "role", "version": "v7", "metadata": {"name": "r"}, "spec": {"allow": {"logins": ["a"]}, "options": {"max_session_ttl": "1h"}}}
    assert lab_validate.check_role_structure(good) == ([], [])
    bad = {"kind": "rol", "version": "v7", "metadata": {"name": "r"}, "spec": {"allow": {"lgoins": ["a"]}, "options": {"max_session_tll": "1h"}, "alow": {}}}
    fail, _ = lab_validate.check_role_structure(bad)
    text = " ".join(fail)
    assert "kind must be" in text and "did you mean 'logins'" in text and "did you mean 'max_session_ttl'" in text and "did you mean 'allow'" in text
    assert lab_validate.check_role_structure({**good, "version": ""})[0] and lab_validate.check_role_structure({**good, "version": "x"})[0]
    f, w = lab_validate.check_role_structure({**good, "version": "v9"})
    assert not f and "v9" in w[0]  # a version this lab does not know is reported, Teleport decides
    f, w = lab_validate.check_role_structure({**good, "spec": {"allow": {"zzzz_unrelated": 1}}})
    assert not f and "not known" in w[0]
    assert lab_validate.check_role_structure({"kind": "role", "version": "v7", "metadata": {"name": "r"}})[0] == ["r: no `spec`"]
    assert any("allow" in m for m in lab_validate.check_role_structure({**good, "spec": {"options": {}}})[0])


def test_teleport_structure_only_when_it_is_not_running(tmp_path):
    _, d = make_lab(tmp_path)
    r = lab_validate.check_teleport(d, Fake(running=()))
    assert r["status"] == "passed" and r["note"] == "structure only"
    _, d2 = make_lab(tmp_path / "b", "broken", "b1")
    r = lab_validate.check_teleport(d2, Fake(running=()))
    assert r["status"] == "failed" and r["note"] == "structure only" and any("lgoins" in m for m in r["messages"])


def test_teleport_live_creates_a_temporary_role_and_deletes_it(tmp_path):
    _, d = make_lab(tmp_path)
    ex = Fake()
    r = lab_validate.check_teleport(d, ex)
    assert r["status"] == "passed" and r["note"] == "accepted by Teleport"
    creates = [(a, s) for a, s in ex.calls if a[-2:] == ["tctl", "create"]]
    assert len(creates) == 1 and "--dry-run" not in " ".join(creates[0][0])  # tctl create has none
    name = yaml.safe_load(creates[0][1])["metadata"]["name"]
    assert re.fullmatch(r"lab-validate-[0-9a-f]{6}-0", name) and "hpc-user" not in creates[0][1]
    assert ["docker", "exec", "workbench-teleport-1", "tctl", "rm", f"role/{name}"] in [a for a, _ in ex.calls]


def test_teleport_live_rejection_is_reported_and_nothing_is_left(tmp_path):
    _, d = make_lab(tmp_path)
    ex = Fake([("tctl create", (1, "ERROR: role has invalid logins"))])
    r = lab_validate.check_teleport(d, ex)
    assert r["status"] == "failed" and r["note"] == "rejected by Teleport" and "invalid logins" in r["messages"][0]
    assert not any("tctl" in a and "rm" in a for a, _ in ex.calls)  # nothing was created, so nothing to delete


# ---- secrets, versions
def test_secret_scan_fails_on_a_leftover_and_passes_clean(tmp_path):
    root, d = make_lab(tmp_path)
    lab_validate.render("t1", SIM, root)
    assert lab_validate.check_secrets(d)["status"] == "passed"
    (d / "baseline" / "k8s" / "policy.yaml").write_text("password: realvalue123\n")
    r = lab_validate.check_secrets(d)
    assert r["status"] == "failed" and "realvalue123" not in json.dumps(r) and "policy.yaml" in r["messages"][0]


def test_version_check_marks_a_difference_indicative(tmp_path):
    _, d = make_lab(tmp_path)
    m = json.loads((d / "manifest.json").read_text())
    m["version_match"]["slurm"] = "differs"
    (d / "manifest.json").write_text(json.dumps(m))
    r = lab_validate.check_versions(d)
    assert r["status"] == "passed" and r["note"] == "indicative" and "indicative" in r["messages"][0]


# ---- the whole run
def full_fake():
    return Fake([("slurmctld -D", (0, out("slurmctld-ok.txt"))), ("check rules", (0, out("promtool-ok.txt"))), ("test rules", (0, "SUCCESS")),
                 ("--dry-run=server", (0, "namespace/team-a created (server dry run)")), ("docker run", (0, out("sacctmgr-run-ok.txt")))])


def test_run_writes_the_report_and_all_green_says_passed(tmp_path):
    root, d = make_lab(tmp_path)
    ex = full_fake()
    ex.routes.insert(0, ("--entrypoint sh", (0, out("slurmctld-ok.txt"))))
    # the accounting one-shot is also `docker run ... sh`: route by its script
    ex.routes.insert(0, ("DbdPort", (0, out("sacctmgr-run-ok.txt"))))
    rep = lab_validate.run("t1", SIM, "baseline", ex, root, tmp_path / "secrets")
    assert rep["summary"] == "passed" and [r["tool"] for r in rep["results"]] == [
        "slurm.conf", "sacctmgr load", "promtool check rules", "promtool test rules", "kubectl dry-run", "teleport role", "secret scan", "version check"]
    assert json.loads((d / "validate.json").read_text())["summary"] == "passed" and rep["restart_required"] is True
    assert not (d / "validate-tmp").exists()
    assert "the lab replaced" in lab_validate.text(rep)


def test_run_reports_failure_for_the_broken_set(tmp_path):
    root, _ = make_lab(tmp_path, "broken", "b1")
    ex = Fake([("DbdPort", (0, out("sacctmgr-run-ok.txt"))), ("--entrypoint sh", (0, out("slurmctld-fatal.txt"))), ("check rules", (1, out("promtool-fail.txt"))),
               ("test rules", (1, "FAILED")), ("--dry-run=server", (0, ""))])
    rep = lab_validate.run("b1", SIM, "baseline", ex, root, tmp_path / "secrets")
    status = {r["tool"]: r["status"] for r in rep["results"]}
    assert rep["summary"].startswith("failed") and status["slurm.conf"] == status["promtool check rules"] == status["teleport role"] == "failed"


def test_run_candidate_and_unknown_lab_are_refused(tmp_path):
    root, _ = make_lab(tmp_path)
    with pytest.raises(lab.LabError, match="issue 553"):
        lab_validate.run("t1", SIM, "candidate", Fake(), root)
    with pytest.raises(lab.LabError, match="no lab"):
        lab_validate.run("nope", SIM, "baseline", Fake(), root)
    with pytest.raises(lab.LabError, match="--side"):
        lab_validate.run("t1", SIM, "sideways", Fake(), root)


def test_run_without_slurm_conf_skips_it(tmp_path):
    root, d = make_lab(tmp_path)
    (d / "baseline" / "slurm.conf").unlink()
    rep = lab_validate.run("t1", SIM, "baseline", full_fake(), root, tmp_path / "secrets")
    assert rep["results"][0]["status"] == "skipped"


# ---- CLI
runner = CliRunner()


def test_cli_candidate_exits_3_with_one_line():
    r = runner.invoke(cli.app, ["validate", "lab", "x", "--side", "candidate"])
    assert r.exit_code == lab_validate.NOT_AVAILABLE == 3 and "issue 553" in r.output and len(r.output.strip().splitlines()) == 1


def test_cli_unknown_lab_and_bad_side(monkeypatch):
    monkeypatch.setattr(lab, "ROOT", Path("/nonexistent-lab-root"))
    r = runner.invoke(cli.app, ["validate", "lab", "nope"])
    assert r.exit_code == 1 and "import it first" in r.output
    assert runner.invoke(cli.app, ["validate", "lab", "nope", "--side", "x"]).exit_code == 1


def test_cli_run_here_prints_lines_and_exits_by_result(tmp_path, monkeypatch):
    root, _ = make_lab(tmp_path)
    monkeypatch.setattr(lab, "ROOT", root)
    rep = {"name": "t1", "side": "baseline", "summary": "passed with 1 skipped", "results": [lab_validate.result("kubectl dry-run", "c", "skipped", ["skipped: Kubernetes is not running"])]}
    monkeypatch.setattr(lab_validate, "run", lambda *a, **k: rep)
    r = runner.invoke(cli.app, ["validate", "lab", "t1", "--run-here"])
    assert r.exit_code == 0 and "passed with 1 skipped" in r.output and "SKIP  kubectl dry-run" in r.output
    rep["summary"] = "failed (1 of 1)"
    assert runner.invoke(cli.app, ["validate", "lab", "t1", "--run-here"]).exit_code == 1


def test_cli_goes_through_the_governor_and_shows_one_refusal(tmp_path, monkeypatch):
    root, d = make_lab(tmp_path)
    monkeypatch.setattr(lab, "ROOT", root)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    rep = {"name": "t1", "side": "baseline", "summary": "passed", "results": []}
    (d / "validate.json").write_text(json.dumps(rep))
    sent = []

    def gov(req, path=None, timeout=3):
        sent.append(req["cmd"])
        if req["cmd"] == "lab-validate":
            return {"ok": True, "started": True, "job": 7}
        return {"ok": True, "change": {"job": 7, "label": "lab-validate=t1", "state": "done"}}

    monkeypatch.setattr(checks, "governor_request", gov)
    r = runner.invoke(cli.app, ["validate", "lab", "t1"])
    assert r.exit_code == 0 and sent == ["lab-validate", "status"] and "passed" in r.output
    monkeypatch.setattr(checks, "governor_request", lambda req, path=None, timeout=3: {"ok": False, "error": "refused at L2: stack changes are paused (GV-8)"})
    r = runner.invoke(cli.app, ["validate", "lab", "t1"])
    assert r.exit_code == 1 and r.output.count("refused") == 1 and "L2" in r.output

    def gone(req, path=None, timeout=3):
        raise OSError("no socket")

    monkeypatch.setattr(checks, "governor_request", gone)
    r = runner.invoke(cli.app, ["validate", "lab", "t1"])
    assert r.exit_code == 1 and "governor is not reachable" in r.output


# ---- governor verb
def calm_gov(tmp_path, lab_ok=True, effector=None, avail=4 * 1024**3):
    from governor.signals import FileSampler

    f = tmp_path / "s.json"
    f.write_text(json.dumps({"mem_available": avail, "psi_some": 0, "psi_full": 0}))
    g = Governor(FileSampler(f), Ladder(), [], lambda a: None, False, effector=effector, lab_exists=(lambda n: lab_ok))
    g.step(0)
    return g


def test_governor_runs_lab_validate_at_calm_and_audits_it(tmp_path):
    import threading
    calls, done = [], threading.Event()
    g = calm_gov(tmp_path, effector=lambda a: calls.append(a) or done.set() or 0)
    r = g.handle("lab-validate", {"name": "prod-a"})
    assert r["started"] and done.wait(5) and calls[-1] == ["validate", "lab", "prod-a", "--run-here"]
    assert any("lab-validate=prod-a" in a for a in g.audit)


def test_governor_refuses_lab_validate_with_one_message(tmp_path):
    g = calm_gov(tmp_path, lab_ok=False)
    assert "no lab prod-a on disk" in g.handle("lab-validate", {"name": "prod-a"})["error"]
    g = calm_gov(tmp_path, effector=lambda a: 0)
    g.ladder.level = 2
    assert g.handle("lab-validate", {"name": "prod-a"})["error"].startswith("refused")
    g.ladder.level = 0
    assert "not available" in g.handle("lab-validate", {"name": "prod-a", "side": "candidate"})["error"]
    g.change = {"job": 1, "label": "x", "state": "running", "stages": [], "started": 0, "finished": None}
    assert "one lab run at a time" in g.handle("lab-validate", {"name": "prod-a"})["error"]
    g.change = None
    assert "refused" in calm_gov(tmp_path, effector=lambda a: 0, avail=1 * 1024**3).handle("lab-validate", {"name": "prod-a"})["error"]  # admit: 250 MB would pass the 1 GB floor


def test_control_validates_the_lab_name():
    from governor import control

    ok = lambda cmd, req: {"ok": True}
    for name in ("prod-a", "a", "0x"):
        assert control.dispatch(json.dumps({"cmd": "lab-validate", "name": name}).encode(), ok, set)["ok"]
    for bad in ("Prod", "-a", "a/b", "a;b", "", "x" * 42, 5, None):
        assert control.dispatch(json.dumps({"cmd": "lab-validate", "name": bad}).encode(), ok, set)["ok"] is False
    assert control.dispatch(json.dumps({"cmd": "lab-validate", "name": "a", "side": "../x"}).encode(), ok, set)["ok"] is False
