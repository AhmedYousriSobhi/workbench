"""A TCP forwarder: `forward.py 443:bmc-farm:9000 8443:bmc-farm:9000` listens on each port and pipes the bytes to the target.
One runs per node BMC (ADR 0018): the container's own IP is the BMC's address, the farm keeps all the state. It never
looks at the bytes, so TLS ends at the farm and the same certificate is presented on every address."""
import asyncio
import sys


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


def handler(host: str, port: int):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(host, port)
        except OSError:
            writer.close()
            return
        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))
    return handle


async def main(specs: list[str]) -> None:
    servers = []
    for spec in specs:
        listen, host, port = spec.split(":")
        servers.append(await asyncio.start_server(handler(host, int(port)), "0.0.0.0", int(listen)))
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
