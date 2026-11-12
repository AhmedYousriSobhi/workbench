"""On-demand wire capture (issue 505): the Slurm header parser, pcap reading, message boundaries and latency, the audit filter, and the limits."""
import struct
from pathlib import Path

import pytest
import yaml

from benchctl import capture, slurm_msgtypes
from benchctl.capture import PcapStream, SlurmDecoder, parse_slurm_header

NODE, CTL, OTHER = "10.141.0.1", "10.141.255.1", "10.141.0.9"
PING = next(k for k, v in slurm_msgtypes.TYPES.items() if v == "REQUEST_PING")
RC = next(k for k, v in slurm_msgtypes.TYPES.items() if v == "RESPONSE_SLURM_RC")
REG = next(k for k, v in slurm_msgtypes.TYPES.items() if v == "MESSAGE_NODE_REGISTRATION_STATUS")


def SIM():
    return yaml.safe_load(Path("sim.yaml").read_text())


def slurm_msg(mtype, body=b"", version=slurm_msgtypes.PROTOCOL, extra=0):
    """length prefix + header (version, flags, type, body length, forward count, return count) + body, as slurm_protocol_pack.c writes it."""
    rest = struct.pack("!HHHIHH", version, 0, mtype, len(body) + extra, 0, 0) + body
    return struct.pack("!I", len(rest)) + rest


def frame(src, sport, dst, dport, seq, payload, link=113):
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + 20 + len(payload), 0, 0, 64, 6, 0, bytes(map(int, src.split("."))), bytes(map(int, dst.split("."))))
    tcp = struct.pack("!HHIIBBHHH", sport, dport, seq, 0, 5 << 4, 0x18, 65535, 0, 0)
    pkt = ip + tcp + payload
    if link == 1:
        return b"\0" * 12 + b"\x08\x00" + pkt
    if link == 276:
        return b"\x08\x00" + b"\0" * 18 + pkt
    return b"\0" * 14 + b"\x08\x00" + pkt  # Linux cooked (tcpdump -i any)


def pcap(frames, link=113, snap=None):
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, link)
    for t, f in frames:
        f = f[:snap] if snap else f
        out += struct.pack("<IIII", int(t), int((t % 1) * 1e6), len(f), len(f)) + f
    return out


def decode(frames, link=113, node=NODE, names=None):
    pcs, dec, out = PcapStream(), SlurmDecoder(node, names or {CTL: "slurmctld"}), []
    for pkt in pcs.feed(pcap(frames, link)):
        out += dec.feed(pkt)
    return out


def test_the_header_parser_accepts_only_the_pinned_version_a_known_type_and_a_length_that_holds_the_body():
    h = parse_slurm_header(slurm_msg(PING))
    assert h and h["type"] == "REQUEST_PING" and h["type_id"] == PING and h["version"] == slurm_msgtypes.PROTOCOL
    assert parse_slurm_header(slurm_msg(PING, version=slurm_msgtypes.PROTOCOL - 256)) is None  # another release: not guessed at
    assert parse_slurm_header(slurm_msg(0xFFFE)) is None                                        # a type this release does not define
    assert parse_slurm_header(slurm_msg(PING, extra=500)) is None                               # a body longer than the message
    assert parse_slurm_header(b"\0" * 5) is None and parse_slurm_header(b"GET / HTTP/1.1\r\n\r\n") is None


def test_the_generated_table_is_the_pinned_releases_own():
    assert slurm_msgtypes.VERSION == "26.05.5" and slurm_msgtypes.PROTOCOL == 45 << 8
    assert slurm_msgtypes.TYPES[1001] == "REQUEST_NODE_REGISTRATION_STATUS" and slurm_msgtypes.TYPES[1008] == "REQUEST_PING" and slurm_msgtypes.TYPES[6001] == "REQUEST_LAUNCH_TASKS"
    assert slurm_msgtypes.TYPES[5041] == "REQUEST_KILL_JOBS"  # the header's own comment says 5040; the enum arithmetic (what the compiler uses) gives 5041
    assert len(slurm_msgtypes.TYPES) > 200 and all(isinstance(k, int) and isinstance(v, str) for k, v in slurm_msgtypes.TYPES.items())


@pytest.mark.parametrize("link", [1, 113, 276])
def test_a_request_and_its_answer_become_two_messages_with_a_latency_for_the_answer(link):
    req, rc = slurm_msg(PING), slurm_msg(RC, b"\0\0\0\0")
    got = decode([(100.000, frame(CTL, 40000, NODE, 6818, 1, req, link)), (100.0125, frame(NODE, 6818, CTL, 40000, 1, rc, link))], link)
    assert [(m["dir"], m["type"], m["kind"], m["peer_name"], m["port"]) for m in got] == [("in", "REQUEST_PING", "request", "slurmctld", 6818), ("out", "RESPONSE_SLURM_RC", "response", "slurmctld", 6818)]
    assert got[0]["latency_ms"] is None and got[1]["latency_ms"] == pytest.approx(12.5, abs=0.2)
    assert got[0]["size"] == len(req) and got[1]["size"] == len(rc)


def test_two_messages_on_one_connection_are_told_apart_by_the_length_prefix_and_the_sequence_number():
    a, b = slurm_msg(PING), slurm_msg(REG, b"x" * 40)
    got = decode([(1.0, frame(NODE, 40001, CTL, 6817, 1000, a)), (1.1, frame(NODE, 40001, CTL, 6817, 1000 + len(a), b))])
    assert [m["type"] for m in got] == ["REQUEST_PING", "MESSAGE_NODE_REGISTRATION_STATUS"] and got[1]["body_length"] == 40


def test_the_middle_of_a_long_message_is_not_a_message():
    big = slurm_msg(REG, b"y" * 3000)
    first, rest = big[:1400], big[1400:2800]
    got = decode([(1.0, frame(NODE, 40002, CTL, 6817, 1, first)), (1.0, frame(NODE, 40002, CTL, 6817, 1401, rest))])
    assert [m["type"] for m in got] == ["MESSAGE_NODE_REGISTRATION_STATUS"] and got[0]["size"] == len(big)


def test_a_header_that_does_not_parse_is_unknown_with_only_direction_size_and_timing():
    got = decode([(5.0, frame(OTHER, 50000, NODE, 6818, 1, b"\x16\x03\x01" + b"\0" * 60))], names={OTHER: "login"})
    assert len(got) == 1 and got[0]["type"] == "unknown" and got[0]["type_id"] is None and got[0]["kind"] == "unknown"
    assert (got[0]["dir"], got[0]["size"], got[0]["peer_name"], got[0]["t"]) == ("in", 63, "login", 5.0) and "does not parse" in got[0]["note"]
    assert "body_length" not in got[0]  # nothing guessed


def test_unknown_floods_are_capped_per_connection():
    got = decode([(i, frame(OTHER, 50000, NODE, 6818, 1 + i * 10, b"\x16" * 10)) for i in range(40)])
    assert len(got) == capture.MAX_UNKNOWN_PER_FLOW


def test_the_snaplen_cut_does_not_hide_a_message_or_its_size():
    big = slurm_msg(REG, b"z" * 2000)
    pcs = PcapStream().feed(pcap([(1.0, frame(CTL, 40003, NODE, 6818, 1, big[:1400]))], snap=capture.SNAPLEN))
    got = SlurmDecoder(NODE, {}).feed(pcs[0])
    assert got and got[0]["type"] == "MESSAGE_NODE_REGISTRATION_STATUS" and got[0]["size"] == len(big)  # the size is the length prefix, not the bytes kept


def test_a_pcap_stream_can_arrive_in_small_pieces():
    data = pcap([(1.0, frame(CTL, 40004, NODE, 6818, 1, slurm_msg(PING))), (2.0, frame(NODE, 6818, CTL, 40004, 1, slurm_msg(RC, b"\0\0\0\0")))])
    pcs, dec, out = PcapStream(), SlurmDecoder(NODE, {}), []
    for i in range(0, len(data), 7):
        for pkt in pcs.feed(data[i:i + 7]):
            out += dec.feed(pkt)
    assert [m["type"] for m in out] == ["REQUEST_PING", "RESPONSE_SLURM_RC"]


def test_the_peer_names_come_from_the_addressing_plan():
    sim = SIM()
    names = capture.ip_names(sim)
    plan = capture.addressing.plan(sim)
    assert names[plan["services"]["slurmctld"]] == "slurmctld" and names[plan["nodes"]["cn001"]] == "cn001" and names[next(iter(plan["logins"].values()))] == next(iter(plan["logins"]))


def test_the_tcpdump_command_is_bounded_and_filters_to_the_slurm_ports():
    cmd = capture.tcpdump_command("cn001", 99999, 10**9)
    assert cmd[:4] == ["docker", "exec", "workbench-cn001-1", "timeout"] and cmd[4] == str(capture.MAX_SECONDS)
    assert cmd[cmd.index("-c") + 1] == str(capture.MAX_PACKETS) and cmd[cmd.index("-s") + 1] == str(capture.SNAPLEN) and cmd[-1] == "tcp and (port 6817 or port 6818)"
    assert "-w" in cmd and cmd[cmd.index("-w") + 1] == "-"  # to stdout: nothing written to a file in the node


def test_a_non_slurm_node_is_refused_before_anything_runs():
    with pytest.raises(ValueError):
        capture.run_slurm(SIM(), "no-such-node", lambda m: None, popen=lambda *a, **k: pytest.fail("ran tcpdump"))


def test_run_slurm_streams_messages_to_the_emitter_and_stops_the_process():
    sim = SIM()
    plan = capture.addressing.plan(sim)
    node_ip, ctl = plan["nodes"]["cn001"], plan["services"]["slurmctld"]
    data = pcap([(1.0, frame(ctl, 40005, node_ip, 6818, 1, slurm_msg(PING)))])

    class P:
        def __init__(self, *a, **k):
            self.stdout, self.terminated = type("S", (), {"chunks": [data, b""], "read1": lambda self, n: self.chunks.pop(0)})(), False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self):
            return 0
    got = []
    assert capture.run_slurm(sim, "cn001", got.append, popen=P) == 0
    assert [m["type"] for m in got] == ["REQUEST_PING"] and got[0]["peer_name"] == "slurmctld"


# ---- Kubernetes ---------------------------------------------------------------------------------------------------------------
def event(verb, res, name, ns="", user="kwok", code=200, sub=None, stage="ResponseComplete", ip="10.141.255.40"):
    ref = {"resource": res, "name": name, **({"namespace": ns} if ns else {}), **({"subresource": sub} if sub else {})}
    return {"auditID": f"{verb}{res}{name}", "stage": stage, "verb": verb, "objectRef": ref, "user": {"username": user}, "sourceIPs": [ip], "responseStatus": {"code": code},
            "requestReceivedTimestamp": "2026-11-11T10:00:00.000000Z", "stageTimestamp": "2026-11-11T10:00:00.012500Z"}


def test_the_audit_filter_keeps_the_nodes_lease_node_object_and_pods_and_nothing_else():
    keep = [event("update", "leases", "kw001", "kube-node-lease"), event("patch", "nodes", "kw001", sub="status"), event("get", "pods", "p1", "default")]
    drop = [event("update", "leases", "kw002", "kube-node-lease"), event("patch", "nodes", "kw002"), event("get", "pods", "p9", "default"), event("get", "secrets", "kw001"), event("list", "configmaps", "")]
    pods = {("default", "p1")}
    got = [capture.audit_message(e, "kw001", pods, {"10.141.255.40": "kwok-controller"}) for e in keep + drop]
    assert [m is not None for m in got] == [True] * 3 + [False] * 5
    m = got[0]
    assert (m["type"], m["kind"], m["who"], m["result"], m["object"], m["peer_name"], m["port"]) == ("update leases", "request", "kwok", 200, "kube-node-lease/kw001", "kwok-controller", 6443)
    assert m["latency_ms"] == pytest.approx(12.5, abs=0.01) and m["size"] is None and got[1]["type"] == "patch nodes/status"


def test_audit_lines_are_only_json_audit_events():
    assert capture.parse_audit_line('{"auditID":"a","verb":"get"}') == {"auditID": "a", "verb": "get"}
    for bad in ("", "I1111 apiserver started", "{not json", '{"x": 1}', "[1]"):
        assert capture.parse_audit_line(bad) is None


def test_run_k8s_polls_dedupes_and_stops_at_the_time_limit():
    sim = SIM()
    clock = {"t": 1000.0}
    evs = [event("update", "leases", "kw001", "kube-node-lease"), event("update", "leases", "kw001", "kube-node-lease", stage="RequestReceived")]
    calls = []

    def logs(since):
        calls.append(since)
        return iter(evs)
    out = []
    capture.run_k8s(sim, "kw001", out.append, lambda n: {("default", "p1")}, seconds=7, logs=logs, sleep=lambda s: clock.__setitem__("t", clock["t"] + s), clock=lambda: clock["t"])
    assert len(out) == 1 and len(calls) == 4  # the same audit id twice is one message; the RequestReceived stage is skipped; 7 s at one poll per 2 s
