"""The optional object store, tier 3 of ADR 0078 (issue 672)."""
import stat
from pathlib import Path

import yaml

from benchctl import compose, memory, objectstore, promtargets, workbench

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
GROUPS = ["core", "access", "objectstore"]


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_prepare_writes_the_config_and_keeps_the_server_secrets_private(tmp_path):
    vals = objectstore.prepare(tmp_path)
    cfg = (tmp_path / "objectstore/garage.toml").read_text()
    assert 'replication_factor = 1' in cfg and f'rpc_secret = "{vals["rpc_secret"]}"' in cfg and 's3_region = "garage"' in cfg
    assert vals["secret_key"] not in cfg and vals["access_key"] not in cfg  # the S3 key reaches Garage through the environment only
    sec = tmp_path / "secrets/objectstore"
    for name in ("access_key", "secret_key", "rpc_secret", "metrics_token", "server.env"):
        assert mode(sec / name) == 0o600, name
    assert mode(tmp_path / "objectstore/garage.toml") == 0o600
    env = (sec / "s3.env").read_text()
    assert "AWS_ENDPOINT_URL=http://objectstore:3900" in env and "AWS_DEFAULT_REGION=garage" in env and vals["access_key"] in env
    assert "rpc_secret" not in env and vals["rpc_secret"] not in env and vals["metrics_token"] not in env


def test_a_second_prepare_keeps_the_keys(tmp_path):
    first = objectstore.prepare(tmp_path)
    assert objectstore.prepare(tmp_path) == first  # data written with a key stays readable


def test_the_service_is_unprivileged_limited_and_off_by_default():
    assert "objectstore" not in compose.generate(SIM, {"groups": ["core", "access"]})["services"]
    out = compose.generate(SIM, {"groups": GROUPS})["services"]
    svc = out["objectstore"]
    assert svc["image"] == "dxflrs/garage:v2.5.0" and svc["labels"]["workbench.class"] == "P6"
    assert svc["mem_limit"] == f"{objectstore.LIMIT_MB}m" and not svc.get("privileged") and "cap_add" not in svc
    assert svc["user"].split(":")[0] != "0" and svc["env_file"] == ["./secrets/objectstore/server.env"]
    assert svc["ports"] == ["127.0.0.1:3900:3900"]  # localhost only
    assert "GARAGE_DEFAULT_SECRET_KEY" not in str(svc.get("environment", {}))  # no secret in the generated compose file
    assert "--single-node" in svc["command"] and "--default-bucket" in svc["command"]


def test_every_node_and_login_gets_the_credentials_read_only():
    out = compose.generate(SIM, {"groups": GROUPS, "compute_nodes": 2})["services"]
    want = "./secrets/objectstore/s3.env:/run/workbench/objectstore/s3.env:ro"
    clients = [n for n, s in out.items() if s["labels"].get("workbench.node") or n.startswith("login")]
    assert clients and all(want in out[n]["volumes"] for n in clients)
    assert not any(want in s.get("volumes", []) for n, s in out.items() if n not in clients)


def test_the_part_is_known_to_the_workbench_but_never_turned_on_by_all():
    assert workbench.PARTS["objectstore"] == ("objectstore", None) and "objectstore" in workbench.PART_INFO
    assert "objectstore" in workbench.BY_NAME_ONLY and workbench.PART_CATEGORY["objectstore"] == "storage"
    assert "objectstore" in memory.MEASURED_MB


def test_prometheus_scrapes_garage_with_the_metrics_token(tmp_path):
    base = {"gpu-exporter-farm", "prometheus", "grafana", "alertmanager"}
    assert "objectstore" not in {j["job_name"] for j in promtargets.part_jobs(SIM, base)}
    promtargets.prepare(SIM, base | {"objectstore"}, out=tmp_path)
    job = {j["job_name"]: j for j in promtargets.part_jobs(SIM, base | {"objectstore"})}["objectstore"]
    assert job["static_configs"][0]["targets"] == ["objectstore:3903"]
    token = (tmp_path / "secrets/prometheus/objectstore-metrics.token").read_text().strip()
    assert token == (tmp_path / "secrets/objectstore/metrics_token").read_text().strip()
    assert job["authorization"]["credentials_file"].endswith("objectstore-metrics.token")
