"""On-demand capture of one wire's messages (issue 505, ADR 0042). Off unless the governor starts it.

Two sources, one message shape:
- Slurm: `tcpdump` runs inside the focused node's container (`docker exec`), limited by time and packet count, and writes a pcap stream
  to stdout. The Slurm message header is decoded by a small parser keyed to the pinned release (slurm_msgtypes.py, generated from that
  release's own msg_type.h); a message whose header does not parse is "unknown" and keeps only direction, size and timing. Nothing from
  a message body is read or kept.
- Kubernetes: the kube-apiserver audit log (metadata only, narrow policy) read with `docker logs`, filtered to the node: lease and node
  objects named after it and pods whose spec.nodeName is the node.

A node is told apart by its fixed address (ADR 0018): the peer IP is mapped to a name through the addressing plan. Messages stay in memory
(the governor's ring); this module writes nothing to disk. Every message is a dict:
  {t, dir (out|in), peer, peer_name, port, size|None, type, type_id, kind (request|response|message|unknown), latency_ms|None, note?}
"""
import json
import struct
import subprocess
import time
from collections.abc import Callable, Iterator

from . import addressing, slurm_msgtypes

SLURM_PORTS = (6817, 6818)  # slurmctld, slurmd
SLURM_HEADER = 14  # u32 length, then u16 version, u16 flags, u16 msg_type, u32 body_length (src/common/slurm_protocol_pack.c, pack_header)
SNAPLEN = 256  # bytes of each packet kept: the header is all that is read
MAX_UNKNOWN_PER_FLOW = 5
DEFAULT_SECONDS, MAX_SECONDS, MAX_PACKETS = 300, 300, 20000
LINK = {1: 14, 113: 16, 276: 20}  # pcap link types: Ethernet, Linux cooked (tcpdump -i any), Linux cooked v2


def ip_names(sim: dict) -> dict[str, str]:
    """{ip: name} for every login node, Slurm node and service of the addressing plan."""
    p = addressing.plan(sim)
    out = {ip: n for group in ("services", "nodes", "logins") for n, ip in p[group].items()}
    out.update({ip: n for n, ip in p.get("ipmi_logins", {}).items()})
    return out


def kind_of(name: str | None) -> str:
    if not name:
        return "unknown"
    return "request" if name.startswith("REQUEST_") else "response" if name.startswith("RESPONSE_") else "message"


# ---- Slurm -------------------------------------------------------------------------------------------------------------------
def parse_slurm_header(data: bytes) -> dict | None:
    """The header at the start of a Slurm message, or None. Accepts only the pinned protocol version, a known message type and a length
    that can hold the header and the body; anything else is not guessed at."""
    if len(data) < SLURM_HEADER:
        return None
    length, version, flags, mtype, body = struct.unpack("!IHHHI", data[:SLURM_HEADER])
    if version != slurm_msgtypes.PROTOCOL or mtype not in slurm_msgtypes.TYPES or length < SLURM_HEADER + body:  # the header after the length prefix is at least 14 bytes (10, the forward and return counts), then the body
        return None
    return {"length": length, "version": version, "flags": flags, "type_id": mtype, "type": slurm_msgtypes.TYPES[mtype], "body_length": body}


class Packet:
    __slots__ = ("data", "dport", "dst", "plen", "seq", "sport", "src", "t")

    def __init__(self, t, src, sport, dst, dport, seq, plen, data):
        self.t, self.src, self.sport, self.dst, self.dport, self.seq, self.plen, self.data = t, src, sport, dst, dport, seq, plen, data


class PcapStream:
    """Feed it bytes from `tcpdump -w -` as they arrive; `feed` returns the TCP packets completed so far (IPv4 only)."""

    def __init__(self) -> None:
        self.buf, self.link, self.endian, self.nano = b"", None, "<", False

    def feed(self, chunk: bytes) -> list[Packet]:
        self.buf += chunk
        out: list[Packet] = []
        if self.link is None:
            if len(self.buf) < 24:
                return out
            magic = self.buf[:4]
            self.endian = "<" if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
            self.nano = magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d")
            self.link = struct.unpack(self.endian + "I", self.buf[20:24])[0] & 0xFFFFFFF
            self.buf = self.buf[24:]
        while len(self.buf) >= 16:
            sec, frac, incl, _orig = struct.unpack(self.endian + "IIII", self.buf[:16])
            if len(self.buf) < 16 + incl:
                break
            frame, self.buf = self.buf[16:16 + incl], self.buf[16 + incl:]
            pkt = self._tcp(sec + frac / (1e9 if self.nano else 1e6), frame)
            if pkt:
                out.append(pkt)
        return out

    def _tcp(self, t: float, frame: bytes) -> Packet | None:
        off = LINK.get(self.link)
        if off is None or len(frame) < off + 20:
            return None
        proto = struct.unpack("!H", frame[12:14])[0] if self.link == 1 else struct.unpack("!H", frame[14:16])[0] if self.link == 113 else struct.unpack("!H", frame[0:2])[0]
        if proto != 0x0800:
            return None
        ip = frame[off:]
        ihl = (ip[0] & 0x0F) * 4
        if ip[0] >> 4 != 4 or ip[9] != 6 or len(ip) < ihl + 20:
            return None
        total = struct.unpack("!H", ip[2:4])[0]
        tcp = ip[ihl:]
        sport, dport, seq = struct.unpack("!HHI", tcp[:8])
        doff = (tcp[12] >> 4) * 4
        plen = max(0, total - ihl - doff)
        return Packet(t, ".".join(map(str, ip[12:16])), sport, ".".join(map(str, ip[16:20])), dport, seq, plen, tcp[doff:doff + plen])


class SlurmDecoder:
    """Turns TCP packets of a node into messages. A message starts where the previous one on that connection ends (the length prefix and
    the TCP sequence number say where); a connection joined mid-message resyncs on the next packet that parses."""

    def __init__(self, node_ip: str, names: dict[str, str]) -> None:
        self.ip, self.names, self.flows, self.open = node_ip, names, {}, {}

    def feed(self, pkt: Packet) -> list[dict]:
        if pkt.plen == 0:
            return []
        key = (pkt.src, pkt.sport, pkt.dst, pkt.dport)
        flow = self.flows.setdefault(key, {"next": None, "unknown": 0})
        starts = flow["next"] is None or ((pkt.seq - flow["next"]) & 0xFFFFFFFF) < 0x80000000  # exactly where the last message ended, or later (a gap: resync)
        if not starts:
            return []  # the middle of a message already counted
        hdr = parse_slurm_header(pkt.data)
        out = pkt.src == self.ip
        peer = pkt.dst if out else pkt.src
        svc = pkt.dport if pkt.dport in SLURM_PORTS else pkt.sport  # the Slurm service port of this connection (6817 slurmctld, 6818 slurmd), not the caller's ephemeral one
        msg = {"t": round(pkt.t, 3), "dir": "out" if out else "in", "peer": peer, "peer_name": self.names.get(peer), "port": svc, "latency_ms": None}
        if hdr is None:
            flow["next"] = (pkt.seq + pkt.plen) & 0xFFFFFFFF
            flow["unknown"] += 1
            if flow["unknown"] > MAX_UNKNOWN_PER_FLOW:
                return []
            return [{**msg, "size": pkt.plen, "type": "unknown", "type_id": None, "kind": "unknown", "note": "the header does not parse as a Slurm " + slurm_msgtypes.VERSION + " message"}]
        flow["next"] = (pkt.seq + 4 + hdr["length"]) & 0xFFFFFFFF
        msg.update(size=4 + hdr["length"], type=hdr["type"], type_id=hdr["type_id"], kind=kind_of(hdr["type"]), version=hdr["version"], flags=hdr["flags"], body_length=hdr["body_length"])
        rev = (pkt.dst, pkt.dport, pkt.src, pkt.sport)
        asked = self.open.pop(rev, None)  # the first message back on the same connection answers the request that was open
        if asked is not None:
            msg["latency_ms"] = round((pkt.t - asked) * 1000, 1)
        if msg["kind"] == "request":
            self.open[key] = pkt.t
        return [msg]


# ---- Kubernetes --------------------------------------------------------------------------------------------------------------
def audit_message(event: dict, node: str, pods: set[tuple[str, str]], names: dict[str, str]) -> dict | None:
    """One audit event as a message, when it concerns `node`: a lease or node object named after it, or one of its pods."""
    obj = event.get("objectRef") or {}
    res, name, ns = obj.get("resource"), obj.get("name"), obj.get("namespace") or ""
    if not ((res == "nodes" and name == node) or (res == "leases" and name == node) or (res == "pods" and (ns, name) in pods)):
        return None
    t = _iso(event.get("stageTimestamp") or event.get("requestReceivedTimestamp"))
    began = _iso(event.get("requestReceivedTimestamp"))
    ips = event.get("sourceIPs") or []
    sub = obj.get("subresource")
    return {"t": round(t, 3) if t else None, "dir": "out", "peer": ips[0] if ips else None, "peer_name": names.get(ips[0]) if ips else None, "port": 6443, "size": None,
            "type": f"{event.get('verb')} {res}{'/' + sub if sub else ''}", "type_id": None, "kind": "request", "latency_ms": round((t - began) * 1000, 1) if t and began else None,
            "who": (event.get("user") or {}).get("username"), "result": (event.get("responseStatus") or {}).get("code"), "object": f"{ns + '/' if ns else ''}{name}"}


def _iso(s: str | None) -> float | None:
    if not s:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(s).timestamp()  # Python 3.11 reads the trailing Z
    except ValueError:
        return None


def parse_audit_line(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        ev = json.loads(line)
    except ValueError:
        return None
    return ev if isinstance(ev, dict) and "auditID" in ev else None


# ---- runners (the only parts that touch Docker) ------------------------------------------------------------------------------
def container_of(node: str) -> str:
    return f"workbench-{node}-1"


def tcpdump_command(node: str, seconds: int, packets: int) -> list[str]:
    s, n = max(1, min(int(seconds), MAX_SECONDS)), max(1, min(int(packets), MAX_PACKETS))
    return ["docker", "exec", container_of(node), "timeout", str(s), "tcpdump", "-i", "any", "-nn", "-U", "-s", str(SNAPLEN), "-c", str(n), "-w", "-",
            "tcp and (port 6817 or port 6818)"]


def run_slurm(sim: dict, node: str, emit: Callable[[dict], None], seconds: int = DEFAULT_SECONDS, packets: int = MAX_PACKETS,
              popen=subprocess.Popen, stop: Callable[[], bool] = lambda: False) -> int:
    """Capture Slurm messages at `node` until the time or packet limit, or `stop()`. Returns the tcpdump exit code."""
    plan = addressing.plan(sim)
    ip = plan["nodes"].get(node)
    if not ip:
        raise ValueError(f"{node} is not a Slurm node of this cluster")
    dec, pcap, count = SlurmDecoder(ip, ip_names(sim)), PcapStream(), [0]
    proc = popen(tcpdump_command(node, seconds, packets), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while not stop():
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
            if not chunk:
                break
            for pkt in pcap.feed(chunk):
                for m in dec.feed(pkt):
                    count[0] += 1
                    emit(m)
    finally:
        if proc.poll() is None:
            proc.terminate()
    code = proc.wait()
    if code not in (0, 124, -15) and not count[0]:  # 124: `timeout` ended it, the normal end; anything else with nothing seen is a failure to say so
        err = (proc.stderr.read() if proc.stderr else b"").decode(errors="replace").strip()[-200:]
        raise RuntimeError(f"tcpdump in {container_of(node)} exited {code}" + (f": {err}" if err else ""))
    return code


def audit_logs(since: float, runner: Callable = subprocess.run) -> Iterator[dict]:
    r = runner(["docker", "logs", "--since", str(int(since)), "workbench-k8s-apiserver-1"], capture_output=True, text=True, timeout=15, check=False)
    for line in (r.stdout + r.stderr).splitlines():
        if ev := parse_audit_line(line):
            yield ev


def run_k8s(sim: dict, node: str, emit: Callable[[dict], None], pods_of: Callable[[str], set[tuple[str, str]]], seconds: int = DEFAULT_SECONDS,
            stop: Callable[[], bool] = lambda: False, sleep: Callable[[float], None] = time.sleep, logs: Callable = audit_logs, clock: Callable[[], float] = time.time) -> None:
    """Poll the audit log every 2 s for `node`'s events until the time limit or `stop()`. `pods_of(node)` lists its pods, refreshed every 30 s."""
    names, end, seen, pods, pods_at = ip_names(sim), clock() + min(seconds, MAX_SECONDS), set(), set(), 0.0
    last = clock() - 5
    while clock() < end and not stop():
        if clock() - pods_at > 30:
            pods, pods_at = pods_of(node), clock()
        for ev in logs(last):
            if ev.get("auditID") in seen or ev.get("stage") != "ResponseComplete":
                continue
            seen.add(ev.get("auditID"))
            if len(seen) > 5000:
                seen.clear()
            if m := audit_message(ev, node, pods, names):
                emit(m)
        last = clock() - 2
        sleep(2)


def k8s_pods(node: str) -> set[tuple[str, str]]:
    """(namespace, name) of the pods bound to `node`, read as the read-only viewer."""
    from . import k8s

    got = k8s.api_json(f"/api/v1/pods?fieldSelector=spec.nodeName%3D{node}")
    return {(i["metadata"]["namespace"], i["metadata"]["name"]) for i in (got or {}).get("items", [])}


def run(sim: dict, node: str, emit: Callable[[dict], None], seconds: int = DEFAULT_SECONDS, packets: int = MAX_PACKETS, stop: Callable[[], bool] = lambda: False) -> None:
    """Capture the wire of one node: Slurm messages for a Slurm node, the node's audit events for a Kubernetes node."""
    from . import k8s

    if node in addressing.plan(sim)["nodes"]:
        run_slurm(sim, node, emit, seconds, packets, stop=stop)
    elif node in {n for n, _ in k8s.node_list(sim)}:
        run_k8s(sim, node, emit, k8s_pods, seconds, stop=stop)
    else:
        raise ValueError(f"{node} is not a node of this cluster")
