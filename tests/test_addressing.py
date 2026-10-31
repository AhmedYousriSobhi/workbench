"""The addressing plan: fixed private IPs for every container, one formula shared with bcm-mock (ADR 0018)."""
import importlib.util
from pathlib import Path

import pytest
import yaml

from benchctl import addressing, compose, stack

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def test_nodes_get_sequential_addresses_in_node_order_and_services_a_fixed_block():
    p = addressing.plan(SIM)
    assert list(p["nodes"].items())[:3] == [("cn001", "10.141.0.1"), ("cn002", "10.141.0.2"), ("cn003", "10.141.0.3")]
    assert p["nodes"]["gpu001"] == "10.141.100.1" and p["bmc"]["gpu001"] == "10.148.100.1" and p["bmc"]["cn004"] == "10.148.0.4"
    assert p["login"] == "10.141.255.254" and p["services"]["slurmctld"] == "10.141.255.1"
    assert len(set(p["nodes"].values()) | set(p["services"].values()) | {p["login"]}) == len(p["nodes"]) + len(p["services"]) + 1


def test_scaling_one_kind_never_renumbers_the_other():
    more = dict(SIM, compute={**SIM["compute"], "count": 16})
    assert addressing.plan(more)["nodes"]["gpu001"] == addressing.plan(SIM)["nodes"]["gpu001"]
    assert addressing.plan(SIM, 16)["nodes"]["cn016"] == "10.141.0.16"


def test_a_big_cluster_rolls_into_the_next_third_octet_and_never_reaches_the_service_block():
    assert addressing.host_ip(addressing.INTERNAL, 253) == "10.141.0.254" and addressing.host_ip(addressing.INTERNAL, 254) == "10.141.1.1"
    assert addressing.host_ip(addressing.INTERNAL, 0, "gpu") == "10.141.100.1"
    with pytest.raises(addressing.AddressingError):
        addressing.host_ip(addressing.INTERNAL, 254 * 100)
    with pytest.raises(addressing.AddressingError):
        addressing.host_ip(addressing.INTERNAL, 254 * 100, "gpu")


def test_bcm_mock_shows_the_same_addresses_as_the_plan():
    spec = importlib.util.spec_from_file_location("bcm", ROOT / "components" / "bcm-mock" / "bcm.py")
    bcm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bcm)
    state = bcm.Cluster(bcm.load_nodes(ROOT / "sim.yaml"))
    p = addressing.plan(SIM)
    assert {n: state.ip(n) for n in p["nodes"]} == p["nodes"]


def test_every_generated_service_has_its_fixed_address_and_the_network_has_the_subnet():
    prof = compose.load_profile("std")
    data = compose.generate(SIM, prof)
    p = addressing.plan(SIM, prof["compute_nodes"])
    assert data["networks"]["default"]["ipam"]["config"] == [{"subnet": "10.141.0.0/16"}]
    unplanned = set()
    for name, svc in data["services"].items():
        want = addressing.address_of(p, name)
        if want is None:
            unplanned.add(name)
            continue
        assert svc["networks"]["default"]["ipv4_address"] == want, name
    assert unplanned == {"accounting-init"}  # a one-shot job that exits; it needs no fixed address
    assert "aliases" in data["services"]["bmc-farm"]["networks"]["default"]  # the node aliases survive


def test_observability_file_repeats_the_planned_addresses():
    text = (ROOT / "compose" / "observability.yml").read_text()
    for svc in ("prometheus", "grafana", "gpu-exporter-farm"):
        assert f"ipv4_address: {addressing.plan(SIM)['services'][svc]}" in text


def test_overlap_check_flags_a_vpn_route_but_not_docker_bridges():
    routes = ("default via 192.168.1.1 dev wlp2s0\n10.0.0.0/8 via 10.8.0.1 dev tun0\n"
              "10.141.0.0/16 dev br-abc123 proto kernel scope link\n172.17.0.0/16 dev docker0\n192.168.1.0/24 dev wlp2s0\n")
    assert addressing.overlaps(routes) == ["10.0.0.0/8 (dev tun0) overlaps 10.141.0.0/16", "10.0.0.0/8 (dev tun0) overlaps 10.148.0.0/16"]
    assert addressing.overlaps("default via 1.1.1.1 dev eth0\n192.168.0.0/24 dev eth0\n") == []


class R:
    def __init__(self, out="", rc=0):
        self.stdout, self.stderr, self.returncode = out, "", rc


def test_up_refuses_an_existing_network_with_another_subnet_and_names_the_fix():
    msg = stack.network_problem(lambda cmd: R("172.20.0.0/16 "))
    assert "172.20.0.0/16" in msg and "benchctl down" in msg
    assert stack.network_problem(lambda cmd: R("10.141.0.0/16 ")) is None


def test_up_refuses_a_new_network_that_overlaps_a_host_route():
    def runner(cmd):
        return R(rc=1) if cmd[:3] == ["docker", "network", "inspect"] else R("10.0.0.0/8 via 10.8.0.1 dev tun0\n")
    assert "already routes" in stack.network_problem(runner)
    assert stack.network_problem(lambda cmd: R(rc=1) if cmd[0] == "docker" else R("default via 1.1.1.1 dev eth0\n")) is None
