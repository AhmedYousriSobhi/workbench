import subprocess
from pathlib import Path

import yaml

from benchctl import compose, slurmconf

SIM = yaml.safe_load(Path("sim.yaml").read_text())


def test_nodes_and_partitions_follow_sim_yaml():
    conf = slurmconf.slurm_conf(SIM, 16)
    assert "NodeName=cn[001-016] CPUs=8 RealMemory=4096" in conf
    assert "NodeName=gpu[001-002]" in conf and "Gres=gpu:h200:8" in conf
    assert "PartitionName=cpu Nodes=cn[001-016] Default=YES" in conf
    assert "PartitionName=gpu Nodes=gpu[001-002]" in conf


def test_single_node_range_and_no_gpu():
    sim = {**SIM, "gpu": {"nodes": 0}, "compute": {**SIM["compute"], "count": 1}}
    conf = slurmconf.slurm_conf(sim)
    assert "NodeName=cn001 " in conf and "gpu" not in conf.replace("GresTypes=gpu", "")
    assert slurmconf.gres_conf(sim).count("NodeName") == 0


def test_gres_only_names_gpu_nodes():
    assert "NodeName=gpu[001-002] Name=gpu Type=h200 File=/dev/workbench-gpu[0-7]" in slurmconf.gres_conf(SIM)
    assert "NodeName=ALL" not in slurmconf.gres_conf(SIM)


def test_conf_names_equal_compose_services():
    conf_nodes = {n for n, _ in compose.node_names(SIM, 4)}
    svcs = compose.generate(SIM, compose.load_profile("min"))["services"]
    assert conf_nodes <= set(svcs) and "slurmctld" in svcs
    assert svcs["slurmctld"]["hostname"] == "slurmctld" == slurmconf.CTLD


def test_write_creates_config_next_to_compose(tmp_path):
    compose.write(compose.generate(SIM, compose.load_profile("min")), tmp_path / "c.yml", SIM, 4)
    assert (tmp_path / "slurm" / "slurm.conf").read_text().startswith("# GENERATED")
    assert (tmp_path / "slurm" / "gres.conf").exists()


# --- slurmrestd and accounting -------------------------------------------------------------

def _std():
    return compose.generate(SIM, compose.load_profile("std"))["services"]


def test_accounting_config_only_in_accounting_profiles():
    assert "AccountingStorageType" not in slurmconf.slurm_conf(SIM, 4)
    assert "AccountingStorageType=accounting_storage/slurmdbd" in slurmconf.slurm_conf(SIM, 4, True)
    assert "AuthAltTypes=auth/jwt" in slurmconf.slurm_conf(SIM, 4)  # slurmrestd needs it either way
    min_svcs = compose.generate(SIM, compose.load_profile("min"))["services"]
    assert "slurmrestd" in min_svcs and not {"mariadb", "slurmdbd", "accounting-init"} & set(min_svcs)


def test_slurmrestd_is_jwt_only_and_unprivileged():
    r = _std()["slurmrestd"]
    cmd = r["command"][-1]
    assert "rest_auth/jwt" in cmd and cmd.startswith("exec /opt/slurm/sbin/slurmrestd")
    assert r["environment"]["SLURM_JWT"] == "daemon"
    assert r["environment"]["SLURMRESTD_SECURITY"] == "disable_unshare_sysv,disable_unshare_files"
    assert not {"cap_add", "privileged", "security_opt"} & set(r)
    assert not any(p for p in r.get("ports", []))  # internal network only


def test_mariadb_on_capped_tmpfs_and_wired_before_slurmdbd():
    s = _std()
    assert s["mariadb"]["tmpfs"] == ["/var/lib/mysql:size=100m"]
    assert int(s["mariadb"]["mem_limit"].rstrip("m")) > 100  # tmpfs counts against the cgroup
    assert s["slurmdbd"]["depends_on"] == {"mariadb": {"condition": "service_healthy"}}
    assert s["slurmctld"]["depends_on"] == ["slurmdbd"]
    assert s["accounting-init"]["restart"] == "no"
    assert "StoragePass" not in str(s)  # the password never appears in the compose file


def test_users_have_stable_uids_on_every_slurm_container():
    s = _std()
    assert compose.users_env(SIM) == "alice:1001 bob:1002"
    for name in ("slurmctld", "slurmrestd", "cn001", "gpu001"):
        assert s[name]["environment"]["WORKBENCH_USERS"] == "alice:1001 bob:1002"


def test_accounting_script_is_idempotent_and_from_sim_yaml():
    sim = {**SIM, "users": [{"name": "alice", "qos": "normal"}, {"name": "carol", "qos": "high"}]}
    sh = slurmconf.accounting_script(sim)
    assert "add qos high || true" in sh and "add qos normal" not in sh
    assert "add user alice account=sim qos=normal || true" in sh
    assert "add user carol account=sim qos=high || true" in sh
    assert sh.count("|| true") == sh.count("sacctmgr -i add")


def test_secrets_are_private_stable_and_ignored_by_git(tmp_path):
    out = tmp_path / "gen" / "docker-compose.yml"
    data = compose.generate(SIM, compose.load_profile("std"))
    compose.write(data, out, SIM, 16)
    env = tmp_path / "gen" / "secrets" / "mariadb.env"
    conf = tmp_path / "gen" / "secrets" / "slurmdbd" / "slurmdbd.conf"
    pw = next(ln.split("=", 1)[1] for ln in env.read_text().splitlines() if ln.startswith("MARIADB_PASSWORD="))
    assert len(pw) >= 24 and f"StoragePass={pw}" in conf.read_text()
    assert env.stat().st_mode & 0o077 == 0 and conf.stat().st_mode & 0o077 == 0
    assert (tmp_path / "gen" / "secrets").stat().st_mode & 0o077 == 0
    compose.write(data, out, SIM, 16)  # regenerate: a running database must keep matching
    assert f"MARIADB_PASSWORD={pw}" in env.read_text()
    assert "AuthAltTypes=auth/jwt" in conf.read_text()  # slurmdbd must accept restd's JWT
    ignored = subprocess.run(["git", "check-ignore", "-q", "compose/generated/secrets/mariadb.env"],
                             check=False)
    assert ignored.returncode == 0
