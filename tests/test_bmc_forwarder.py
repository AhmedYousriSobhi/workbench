"""components/bmc-forwarder: the per-node BMC address forwards bytes to the farm untouched (ADR 0018, issue 454)."""
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GO = shutil.which("go")
pytestmark = pytest.mark.skipif(GO is None, reason="the Go toolchain builds the forwarder; the live image build covers it otherwise")


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("fwd") / "forward"
    subprocess.run([GO, "build", "-trimpath", "-o", str(out), "forward.go"], cwd=ROOT / "components/bmc-forwarder", check=True,
                   env={"CGO_ENABLED": "0", "HOME": str(out.parent), "GOCACHE": str(out.parent / "cache"), "GOFLAGS": "-mod=mod", "PATH": "/usr/bin:/bin"})
    return out


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def echo_server() -> tuple[int, socket.socket]:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()

    def one(c: socket.socket) -> None:
        with c:
            while data := c.recv(65536):
                c.sendall(b"echo:" + data)

    def loop() -> None:
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=one, args=(c,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return srv.getsockname()[1], srv


def start(binary, *specs):
    p = subprocess.Popen([str(binary), *specs])
    port = int(specs[0].split(":")[0])
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return p


def test_bytes_go_through_both_ways_on_every_listen_port_and_a_dead_target_just_closes(binary):
    tport, srv = echo_server()
    a, b, dead = free_port(), free_port(), free_port()
    p = start(binary, f"{a}:127.0.0.1:{tport}", f"{b}:127.0.0.1:{tport}")
    q = start(binary, f"{dead}:127.0.0.1:1")  # nothing listens on port 1
    try:
        for port in (a, b):
            with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
                c.sendall(b"hello")
                assert c.recv(100) == b"echo:hello"
        with socket.create_connection(("127.0.0.1", a), timeout=5) as c:  # a large payload arrives whole, in order
            payload = bytes(range(256)) * 4096
            c.sendall(payload)
            c.shutdown(socket.SHUT_WR)  # half-close: the reply still comes back
            got = b""
            while chunk := c.recv(65536):
                got += chunk
            assert got.replace(b"echo:", b"") == payload
        with socket.create_connection(("127.0.0.1", dead), timeout=5) as c:
            assert c.recv(10) == b""
    finally:
        p.kill()
        q.kill()
        srv.close()


def test_a_bad_spec_or_a_busy_port_exits_with_a_message(binary):
    assert subprocess.run([str(binary), "nonsense"], check=False, capture_output=True).returncode == 2
    assert subprocess.run([str(binary)], check=False, capture_output=True).returncode == 2
    held = socket.socket()
    held.bind(("0.0.0.0", 0))
    held.listen()
    r = subprocess.run([str(binary), f"{held.getsockname()[1]}:127.0.0.1:1"], check=False, capture_output=True, timeout=10)
    held.close()
    assert r.returncode == 1 and b"address already in use" in r.stderr


def test_the_image_is_a_static_binary_on_scratch():
    text = (ROOT / "components/bmc-forwarder/Dockerfile").read_text()
    assert "FROM scratch" in text and "CGO_ENABLED=0" in text and "USER 65534" in text
