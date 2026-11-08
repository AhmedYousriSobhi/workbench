"""Login nodes (SPEC C-18, ADR 0023, issue 189): how many, and how each one authenticates.

`sim.yaml` `login:` is a list of auth modes, one per login node; the first node is `login` (the name everything
already uses), then `login2`, `login3`, ... A mode says how a client gets in:
- `ssh`: key login on a port published on 127.0.0.1 (the first node keeps 2222, the next ones 2223, ...)
- `teleport`: no published port and no key login; reachable only through the Teleport proxy
- `both`: the two together
A mode may carry the scheduler the node serves, `AUTH[:KIND]` (`ssh:slurm`, `ssh:k8s`, `teleport:both`): an s-login
(Slurm client tools), a k-login (kubectl and the read-only kubeconfigs) or both. A bare `ssh` is `both`: it serves every
scheduler that is built, as before (ADR 0034).
Everything that needs the login nodes (addresses, compose, Teleport registration, the client profile, the page) asks
this module, so a number or a mode is decided in one place."""
import difflib

MODES = ("ssh", "teleport", "both")
DIRECT = ("ssh", "both")        # modes with a published SSH port and key login
VIA_TELEPORT = ("teleport", "both")
KINDS = ("slurm", "k8s", "both")
KIND_LABEL = {"slurm": "s-login (Slurm)", "k8s": "k-login (Kubernetes)", "both": "login (Slurm and Kubernetes)"}
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
        a, colon, k = m.partition(":")
        if a not in MODES:
            close = difflib.get_close_matches(a, MODES, n=1)
            hint = f" Did you mean '{close[0]}'?" if close else ""
            raise LoginError(f"unknown login mode '{a}'.{hint} Valid: {', '.join(MODES)} (optionally :{'|'.join(KINDS)}).")
        if colon and k not in KINDS:
            close = difflib.get_close_matches(k, KINDS, n=1)
            hint = f" Did you mean '{close[0]}'?" if close else ""
            raise LoginError(f"unknown login type '{k}' in '{m}'.{hint} Valid: {', '.join(KINDS)}.")
    if not 1 <= len(modes) <= MAX_LOGINS:
        raise LoginError(f"a cluster needs 1 to {MAX_LOGINS} login nodes, not {len(modes)}")
    return modes


def auth(entry: str) -> str:
    """`ssh:k8s` -> `ssh`."""
    return entry.partition(":")[0]


def kind(entry: str) -> str:
    """`ssh:k8s` -> `k8s`; no kind is `both`."""
    return entry.partition(":")[2] or "both"


def serves(entry: str, scheduler: str) -> bool:
    """Whether a login entry serves `slurm` or `k8s`."""
    return kind(entry) in (scheduler, "both")


def title(entry: str) -> str:
    """The plain name of the login's type, for pages and tables."""
    return KIND_LABEL[kind(entry)]


def modes(sim: dict) -> list[str]:
    """The entry of each login node, in order (sim.yaml `login:`, default one `ssh` node). An entry is `AUTH` or `AUTH:KIND`."""
    return parse(sim.get("login")) or list(DEFAULT)


def nodes(sim: dict) -> list[tuple[str, str]]:
    """[(name, auth mode)] for every login node; the kind is `kinds`."""
    return [(name(i), auth(m)) for i, m in enumerate(modes(sim))]


def kinds(sim: dict) -> dict[str, str]:
    """{name: slurm|k8s|both} for every login node."""
    return {name(i): kind(m) for i, m in enumerate(modes(sim))}


def direct(sim: dict) -> list[tuple[str, int]]:
    """[(name, index)] of the nodes a client can reach by key login (the published port is SSH_PORT + index)."""
    return [(name(i), i) for i, m in enumerate(modes(sim)) if auth(m) in DIRECT]


def via_teleport(sim: dict) -> list[str]:
    return [name(i) for i, m in enumerate(modes(sim)) if auth(m) in VIA_TELEPORT]


def needs_teleport(sim: dict) -> bool:
    return bool(via_teleport(sim))


def problems(sim: dict, parts: set[str] | list[str]) -> list[str]:
    """Why this layout cannot work: a Teleport login with the Teleport part off, a k-login with the k8s part off."""
    out = []
    for n, m in nodes(sim):
        if m in VIA_TELEPORT and "teleport" not in parts:
            out.append(f"login node {n} authenticates with {m} but the teleport part is off (add --access teleport)")
    for n, k in kinds(sim).items():
        if k == "k8s" and "k8s" not in parts:
            out.append(f"login node {n} is a k-login (Kubernetes) but the k8s part is off (add --with k8s)")
    return out
