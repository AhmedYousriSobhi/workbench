"""Docker Engine API over its Unix socket, on a connection opened at startup: no fork on the mitigation path (GV-8, issue 452).

The rootless governor used to run `docker pause|unpause|kill|stop|start`, which forks a process exactly when memory is
scarce. This client sends the same requests the CLI sends (POST /containers/NAME/pause ...) over one kept-alive socket.
A dropped connection is reopened once (a socket, never a process). Standard library only.
"""
import http.client
import json
import os
import socket
from urllib.parse import quote

DEFAULT_SOCKET = "/var/run/docker.sock"
STOP_GRACE_S = 2  # docker stop -t 2, as the CLI path
OK = {"pause": (204,), "unpause": (204,), "kill": (204,), "stop": (204, 304), "start": (204, 304)}  # 304: already in that state


class DockerError(RuntimeError):
    """The daemon refused (status and its own message, e.g. 'Container x is already paused') or cannot be reached."""


def socket_path(host: str | None = None) -> str | None:
    """The Unix socket named by $DOCKER_HOST (unix:///path), the default socket when unset, None for any other transport."""
    host = os.environ.get("DOCKER_HOST", "") if host is None else host
    if not host:
        return DEFAULT_SOCKET
    return host.removeprefix("unix://") if host.startswith("unix://") else None


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("docker", timeout=timeout)
        self.path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class DockerAPI:
    def __init__(self, path: str = DEFAULT_SOCKET, timeout: float = 20):
        self.path = path
        self.conn = _UnixConnection(path, timeout)
        self.conn.connect()  # fail now, before pressure exists: the caller falls back to the CLI

    def _post(self, url: str) -> tuple[int, str]:
        for attempt in (0, 1):
            try:
                self.conn.request("POST", url)
                resp = self.conn.getresponse()
                body = resp.read()
                break
            except (OSError, http.client.HTTPException) as e:
                self.conn.close()  # reopened by the next request; once more only
                if attempt:
                    raise DockerError(f"docker socket {self.path}: {e}") from e
        try:
            message = json.loads(body).get("message", "") if body else ""
        except ValueError:
            message = body.decode(errors="replace").strip()
        return resp.status, message

    def call(self, op: str, name: str) -> None:
        """pause, unpause, kill, stop or start one container by name; raises DockerError carrying the daemon's message."""
        if op not in OK:
            raise ValueError(f"unsupported docker operation {op!r}")
        url = f"/containers/{quote(name, safe='')}/{op}" + (f"?t={STOP_GRACE_S}" if op == "stop" else "")
        status, message = self._post(url)
        if status not in OK[op]:
            raise DockerError(message or f"HTTP {status}")

    def close(self) -> None:
        self.conn.close()
