"""components/bmc-farm/forward.py: the per-node BMC address forwards bytes to the farm untouched (ADR 0018)."""
import asyncio
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("forward", ROOT / "components/bmc-farm/forward.py")
forward = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forward)


def test_bytes_go_through_both_ways_and_a_dead_target_just_closes():
    async def scenario() -> tuple[bytes, bytes]:
        async def echo(reader, writer):
            writer.write(b"echo:" + await reader.read(100))
            await writer.drain()
            writer.close()
        target = await asyncio.start_server(echo, "127.0.0.1", 0)
        tport = target.sockets[0].getsockname()[1]
        front = await asyncio.start_server(forward.handler("127.0.0.1", tport), "127.0.0.1", 0)
        fport = front.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", fport)
        w.write(b"hello")
        await w.drain()
        got = await r.read(100)
        w.close()
        dead = await asyncio.start_server(forward.handler("127.0.0.1", 1), "127.0.0.1", 0)  # nothing listens on port 1
        r2, w2 = await asyncio.open_connection("127.0.0.1", dead.sockets[0].getsockname()[1])
        closed = await r2.read(10)
        w2.close()
        for s in (target, front, dead):
            s.close()
        return got, closed
    got, closed = asyncio.run(scenario())
    assert got == b"echo:hello" and closed == b""
