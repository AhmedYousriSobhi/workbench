"""`benchctl ssh [USER@]NODE`: the argv for a shell on a node (issue 275).

Bare NODE or root@NODE is a root shell through `docker exec` (the nodes take no root SSH login, so this is the root path,
the same as `benchctl exec`). USER@NODE is a real SSH login as that simulated user with the generated key, jumping
(a ProxyCommand) through the first login node that takes a key (the nodes sit behind it, ADR 0011 and 0018). It runs on the host as the
user; the web page never starts it (W-3)."""
import re

from . import compose, logins

NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
PROJECT = "workbench"


def split(target: str) -> tuple[str, str]:
    """`alice@cn001` -> (alice, cn001); `cn001` -> (root, cn001)."""
    user, _, node = target.rpartition("@")
    user = user or "root"
    for what, v in (("user", user), ("node", node)):
        if not NAME.match(v):
            raise ValueError(f"not a valid {what} name: {v!r} (use [USER@]NODE, e.g. cn001 or alice@cn001)")
    return user, node


def argv(target: str, sim: dict, key: str, cmd: list[str] | None = None, tty: bool = True) -> list[str]:
    """What to run. Raises ValueError with one plain message when it cannot be done."""
    user, node = split(target)
    cmd = cmd or []
    if user == "root":
        return ["docker", "exec", *(["-it"] if tty and not cmd else []), f"{PROJECT}-{node}-1", *(cmd or ["bash"])]
    users = [u["name"] for u in sim.get("users", [])]
    if user not in users:
        raise ValueError(f"no simulated user {user!r}; the users are: {', '.join(users) or 'none'}")
    direct = logins.direct(sim)
    if not direct:
        raise ValueError("no login node takes a key (Teleport only). Use `tsh ssh` (see Connections) or `benchctl ssh " + node + "` for root")
    port = compose.SSH_PORT + direct[0][1]
    opts = ["-i", key, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]
    # not -J: its own hop does not take these options, and would stop to ask about the login node's host key
    jump = " ".join(["ssh", *opts, "-p", str(port), "-W", "%h:%p", f"{user}@127.0.0.1"])
    return ["ssh", *opts, "-o", f"ProxyCommand={jump}", *([] if cmd or not tty else ["-t"]), f"{user}@{node}", *cmd]
