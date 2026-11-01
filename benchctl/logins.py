"""Login nodes (SPEC C-18, ADR 0023, issue 189): how many, and how each one authenticates.

`sim.yaml` `login:` is a list of auth modes, one per login node; the first node is `login` (the name everything
already uses), then `login2`, `login3`, ... A mode says how a client gets in:
- `ssh`: key login on a port published on 127.0.0.1 (the first node keeps 2222, the next ones 2223, ...)
- `teleport`: no published port and no key login; reachable only through the Teleport proxy
- `both`: the two together
Everything that needs the login nodes (addresses, compose, Teleport registration, the client profile, the page) asks
this module, so a number or a mode is decided in one place."""
import difflib

MODES = ("ssh", "teleport", "both")
DIRECT = ("ssh", "both")        # modes with a published SSH port and key login
VIA_TELEPORT = ("teleport", "both")
LABEL = {"ssh": "direct SSH", "teleport": "Teleport only", "both": "direct SSH and Teleport"}
MAX_LOGINS = 8
DEFAULT = ["ssh"]


class LoginError(ValueError):
    pass


def name(index: int) -> str:
    """`login` for the first node, then `login2`, `login3`, ..."""
    return "login" if index == 0 else f"login{index + 1}"


def is_login(node: str) -> bool:
    return node == "login" or (node.startswith("login") and node[5:].isdigit() and int(node[5:]) >= 2)


def parse(values: list[str] | str | None) -> list[str] | None:
    """`--login ssh,teleport` (or a repeated flag, or a list from a file) -> ['ssh', 'teleport']; None when nothing was
    asked. Raises LoginError with one message for the first problem."""
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    modes = [m.strip() for v in values for m in str(v).split(",") if m.strip()]
    for m in modes:
        if m not in MODES:
            close = difflib.get_close_matches(m, MODES, n=1)
            hint = f" Did you mean '{close[0]}'?" if close else ""
            raise LoginError(f"unknown login mode '{m}'.{hint} Valid: {', '.join(MODES)}.")
    if not 1 <= len(modes) <= MAX_LOGINS:
        raise LoginError(f"a cluster needs 1 to {MAX_LOGINS} login nodes, not {len(modes)}")
    return modes


def modes(sim: dict) -> list[str]:
    """The auth mode of each login node, in order (sim.yaml `login:`, default one `ssh` node)."""
    return parse(sim.get("login")) or list(DEFAULT)


def nodes(sim: dict) -> list[tuple[str, str]]:
    """[(name, mode)] for every login node."""
    return [(name(i), m) for i, m in enumerate(modes(sim))]


def direct(sim: dict) -> list[tuple[str, int]]:
    """[(name, index)] of the nodes a client can reach by key login (the published port is SSH_PORT + index)."""
    return [(name(i), i) for i, m in enumerate(modes(sim)) if m in DIRECT]


def via_teleport(sim: dict) -> list[str]:
    return [name(i) for i, m in enumerate(modes(sim)) if m in VIA_TELEPORT]


def needs_teleport(sim: dict) -> bool:
    return bool(via_teleport(sim))


def problems(sim: dict, parts: set[str] | list[str]) -> list[str]:
    """Why this layout cannot work: a Teleport login with the Teleport part off."""
    out = []
    for n, m in nodes(sim):
        if m in VIA_TELEPORT and "teleport" not in parts:
            out.append(f"login node {n} authenticates with {m} but the teleport part is off (add --access teleport)")
    return out
