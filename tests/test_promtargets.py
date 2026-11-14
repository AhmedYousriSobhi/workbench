"""What Prometheus scrapes, generated from the selection (issue 458, ADR 0073)."""
import json
from pathlib import Path

import yaml

from benchctl import promtargets

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
BASE = {"gpu-exporter-farm", "prometheus", "grafana", "alertmanager"}


def jobs(services, ready=True):
    return {j["job_name"]: j for j in promtargets.part_jobs(SIM, set(services), ready)}


def test_one_target_per_node_with_the_node_as_instance_and_query_parameter(tmp_path):
    promtargets.prepare(SIM, BASE, out=tmp_path)
    t = json.loads((tmp_path / "prometheus/targets/sim-nodes.json").read_text())
    assert [x["labels"]["instance"] for x in t] == ["cn001", "cn002", "cn003", "cn004", "gpu001", "gpu002"]
    assert all(x["targets"] == ["gpu-exporter-farm:9400"] and x["labels"]["__param_node"] == x["labels"]["instance"] for x in t)


def test_targets_follow_the_node_count(tmp_path):
    promtargets.prepare(SIM, BASE, compute_nodes=2, out=tmp_path)
    t = json.loads((tmp_path / "prometheus/targets/sim-nodes.json").read_text())
    assert [x["labels"]["instance"] for x in t] == ["cn001", "cn002", "gpu001", "gpu002"]


def test_a_part_that_is_not_selected_has_no_job():
    assert jobs(BASE) == {}
    got = jobs(BASE | {"dcgm-exporter", "teleport", "real-gpu", "k8s-scheduler", "k8s-etcd", "kwok-controller", "k8s-controller-manager"})
    assert set(got) == {"dcgm-exporter", "real-gpu", "k8s-controller-manager", "k8s-scheduler", "k8s-etcd", "kwok-controller", "teleport"}
    assert got["dcgm-exporter"]["static_configs"] == [{"targets": ["dcgm-exporter:9400"], "labels": {"instance": "gpu001"}}]
    assert got["teleport"]["static_configs"][0]["targets"] == ["teleport:3434"]
    assert got["k8s-etcd"]["static_configs"][0]["targets"] == ["k8s-etcd:2381"] and "scheme" not in got["k8s-etcd"]
    assert got["k8s-scheduler"]["scheme"] == "https"


def test_more_dcgm_nodes_get_one_target_each():
    sim = {**SIM, "dcgm": {"nodes": 2}}
    got = {j["job_name"]: j for j in promtargets.part_jobs(sim, {"dcgm-exporter", "dcgm-exporter-gpu002"}, True)}
    assert [(c["targets"][0], c["labels"]["instance"]) for c in got["dcgm-exporter"]["static_configs"]] == [
        ("dcgm-exporter:9400", "gpu001"), ("dcgm-exporter-gpu002:9400", "gpu002")]


def test_the_api_server_job_reads_only_the_metrics_token_and_waits_for_it(tmp_path):
    k8s_dir = tmp_path / "k8s"
    k8s_dir.mkdir()
    (k8s_dir / "ca.crt").write_text("CERT\n")
    (k8s_dir / "tokens.csv").write_text("aaa,viewer,viewer-uid\nbbb,metrics,metrics-uid\n")
    out = tmp_path / "gen"
    assert promtargets.prepare(SIM, BASE | {"k8s-apiserver"}, out=out, k8s_dir=k8s_dir) is True
    job = yaml.safe_load((out / "prometheus/scrape-parts.yml").read_text())["scrape_configs"][0]
    assert job["job_name"] == "k8s-apiserver" and job["tls_config"]["server_name"] == "kubernetes"
    assert job["authorization"]["credentials_file"] == "/etc/prometheus/secrets/k8s-metrics.token"
    secrets = out / "secrets/prometheus"
    assert (secrets / "k8s-metrics.token").read_text().strip() == "bbb"  # the metrics user, never viewer
    assert (secrets / "k8s-ca.crt").read_text() == "CERT\n"
    assert sorted(p.name for p in secrets.iterdir()) == ["k8s-ca.crt", "k8s-metrics.token"]
    # without the token there is no job to fail: the job appears once the token exists
    (k8s_dir / "tokens.csv").write_text("aaa,viewer,viewer-uid\n")
    out2 = tmp_path / "gen2"
    promtargets.prepare(SIM, BASE | {"k8s-apiserver"}, out=out2, k8s_dir=k8s_dir)
    assert yaml.safe_load((out2 / "prometheus/scrape-parts.yml").read_text())["scrape_configs"] == []


def test_the_scrape_file_reports_a_change_only_when_the_selection_changes(tmp_path):
    assert promtargets.prepare(SIM, BASE, out=tmp_path) is True  # first write
    assert promtargets.prepare(SIM, BASE, out=tmp_path) is False  # same selection: no reload
    assert promtargets.prepare(SIM, BASE | {"teleport"}, out=tmp_path) is True
    assert promtargets.prepare(SIM, BASE, out=tmp_path) is True  # deselected again


def test_prometheus_reads_what_up_writes():
    """The static config and the mounts point at the files `prepare` writes."""
    prom = yaml.safe_load((ROOT / "observability/prometheus.yml").read_text())
    assert prom["scrape_config_files"] == [f"{promtargets.DIR_IN_CONTAINER}/scrape-*.yml"]
    sim_nodes = next(j for j in prom["scrape_configs"] if j["job_name"] == "sim-nodes")
    assert sim_nodes["file_sd_configs"][0]["files"] == [f"{promtargets.DIR_IN_CONTAINER}/targets/*.json"]
    mounts = yaml.safe_load((ROOT / "compose/observability.yml").read_text())["services"]["prometheus"]["volumes"]
    assert any(m.startswith("./generated/prometheus:") and m.endswith(f"{promtargets.DIR_IN_CONTAINER}:ro") or
               f"{promtargets.DIR_IN_CONTAINER}:ro" in m for m in mounts)
    assert any(f"{promtargets.SECRETS_IN_CONTAINER}:ro" in m for m in mounts)


def test_slurm_is_scraped_on_its_own_endpoint_one_job_per_family():
    """Issue 459, ADR 0074: slurmctld answers /metrics/<family> on its port; users-accts is left out (cardinality)."""
    got = jobs(BASE | {"slurmctld"})
    assert set(got) == {"slurm-jobs", "slurm-nodes", "slurm-partitions", "slurm-scheduler"}
    assert got["slurm-nodes"]["metrics_path"] == "/metrics/nodes"
    assert all(j["static_configs"] == [{"targets": ["slurmctld:6817"]}] for j in got.values())
    assert "slurm-jobs-users-accts" not in got
