"""Kubeconfig fixtures on the k-login: the files a client's Discover step has to judge (issue 767, ADR 0084 addendum).

A client that runs `kubectl` on every poll refuses a kubeconfig it should not trust, because an exec credential plugin named in the file
would run each time. The cases, each a real file with a real owner and mode in every simulated user's `~/.kube` (never replacing `config`):

- `loose-world.yaml`: writable by everyone (mode 666), the user's own file: refused.
- `loose-group.yaml`: writable by a group that is not the user's private group (mode 660, group `users`): refused.
- `foreign-owner.yaml`: owned by another simulated user, readable (mode 644): refused. Absent with a single simulated user.
- `root-owned.yaml`: owned by root, readable (mode 644): accepted (an administrator's hand-over).
- `exec-token.yaml`: mode 600, the user's own, with an exec credential plugin (`plugins/exec-token.sh`) that prints a token and never needs a terminal
  (`interactiveMode: Never`): accepted, and `kubectl` works.
- `browser-signin.yaml`: mode 600, with a plugin that stands for a browser sign-in (`interactiveMode: Always`, `plugins/browser-signin.sh`): a client
  cannot use it. `kubectl` without a terminal refuses to run the plugin ("standard input is not a terminal"); the plugin's own error needs a terminal.

`write` makes the files into `compose/generated/secrets/k8s/kubeconfigs/` and a `manifest` (`path owner group mode`, one per line) that
`images/login/login.sh` installs for each user; `@HOME@` in a file becomes the user's home. The catalogue below is the one source for the
generator, the live test and the guide's table (`render_table`)."""
from dataclasses import dataclass
from pathlib import Path

from . import k8s

DIR = "kubeconfigs"        # under the Kubernetes secrets directory; mounted at /run/workbench/k8s/kubeconfigs on the k-login
PLUGINS = "plugins"        # under ~/.kube
USERS_GROUP = "users"      # a group every Debian-family image has; not any simulated user's private group
EXEC_API = "client.authentication.k8s.io/v1"   # `interactiveMode` is required in v1 (Kubernetes authentication docs)


@dataclass(frozen=True)
class Fixture:
    name: str       # file name under ~/.kube
    what: str       # one line, for the guide
    owner: str      # `user` (the owner of the home), `other` (another simulated user) or `root`
    group: str      # `user` (the user's private group), `other` or a group name
    mode: str       # octal, as `install -m` and `stat -c %a` print it
    client: str     # the verdict the client's guide gives: `refused`, `accepted` or `unusable`
    kubectl: str    # what `kubectl --kubeconfig FILE get nodes` does without a terminal: `ok` or `error`
    kind: str       # `token`, `exec` or `browser`


FIXTURES: tuple[Fixture, ...] = (
    Fixture("loose-world.yaml", "writable by everyone", "user", "user", "666", "refused", "ok", "token"),
    Fixture("loose-group.yaml", "writable by a group that is not the user's own", "user", USERS_GROUP, "660", "refused", "ok", "token"),
    Fixture("foreign-owner.yaml", "owned by another simulated user, readable", "other", "other", "644", "refused", "ok", "token"),
    Fixture("root-owned.yaml", "owned by root, readable (an administrator's hand-over)", "root", "root", "644", "accepted", "ok", "token"),
    Fixture("exec-token.yaml", "private; an exec plugin prints a token, no terminal needed", "user", "user", "600", "accepted", "ok", "exec"),
    Fixture("browser-signin.yaml", "private; a plugin that needs a browser (interactiveMode Always)", "user", "user", "600", "unusable", "error", "browser"),
)
BY_NAME = {f.name: f for f in FIXTURES}
# plugin scripts: path under ~/.kube, mode (owned by the user)
SCRIPTS = (f"{PLUGINS}/exec-token.sh", f"{PLUGINS}/browser-signin.sh")
BROWSER_URL = "https://sso.sim.invalid/device"
INTERACTIVE_ERROR = "standard input is not a terminal"   # client-go, plugin/pkg/client/auth/exec/exec.go (interactiveMode Always, no terminal)


def exec_token_script(token: str) -> str:
    """A plugin that prints the viewer's token as an ExecCredential and never reads input (what `kubelogin`-style tools do after sign-in)."""
    return ("#!/bin/sh\n# Exec credential plugin of the simulation (issue 767): prints a token, needs no terminal.\n"
            f"printf '%s\\n' '{{\"apiVersion\":\"{EXEC_API}\",\"kind\":\"ExecCredential\",\"status\":{{\"token\":\"{token}\"}}}}'\n")


def browser_script() -> str:
    """The plugin of a browser sign-in: it needs a person at a screen, so it fails where there is none."""
    return ("#!/bin/sh\n# Stands for a plugin that signs in through a browser (issue 767). Needs a terminal and a display; there is neither here.\n"
            f"echo 'To sign in, open {BROWSER_URL} in a browser and enter the code shown there. No browser or display is available.' >&2\n"
            "exit 1\n")


def _exec_user(command: str, interactive: str) -> dict:
    return {"exec": {"apiVersion": EXEC_API, "command": command, "interactiveMode": interactive, "provideClusterInfo": False}}


def build(server: str, ca_b64: str, token: str) -> dict[str, str]:
    """Every file to install, by path relative to the kubeconfigs directory (the manifest is separate)."""
    out: dict[str, str] = {}
    for f in FIXTURES:
        if f.kind == "token":
            out[f.name] = k8s.kubeconfig(server, {"name": "viewer", "auth": {"token": token}}, ca_b64=ca_b64)
        elif f.kind == "exec":
            out[f.name] = k8s.kubeconfig(server, {"name": "viewer-exec", "auth": _exec_user(f"@HOME@/.kube/{PLUGINS}/exec-token.sh", "Never")}, ca_b64=ca_b64)
        else:
            out[f.name] = k8s.kubeconfig(server, {"name": "signin", "auth": _exec_user(f"@HOME@/.kube/{PLUGINS}/browser-signin.sh", "Always")}, ca_b64=ca_b64)
    out[SCRIPTS[0]] = exec_token_script(token)
    out[SCRIPTS[1]] = browser_script()
    return out


def manifest() -> str:
    lines = [f"{f.name} {f.owner} {f.group} {f.mode}" for f in FIXTURES] + [f"{s} user user 700" for s in SCRIPTS]
    return "\n".join(lines) + "\n"


def write(d: Path, server: str, ca_b64: str, token: str) -> Path:
    """Write the files and the manifest under `d` / kubeconfigs (mode 600, directory 700: the login node's root reads them). Rewritten every time."""
    root = d / DIR
    (root / PLUGINS).mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    (root / PLUGINS).chmod(0o700)
    files = build(server, ca_b64, token) | {"manifest": manifest()}
    for rel, text in files.items():
        p = root / rel
        p.write_text(text)
        p.chmod(0o600)
    return root


def render_table() -> str:
    """The guide's table (markdown), so the doc and the catalogue cannot drift."""
    rows = ["| File in `~/.kube` | What it is | Owner, group, mode | Client's verdict | `kubectl get nodes` without a terminal |", "|---|---|---|---|---|"]
    rows.append("| `config` | the normal token kubeconfig, read-only, cluster-wide (not changed by these fixtures) | user, user, 600 | accepted | ok |")
    for f in FIXTURES:
        owner = {"user": "the user", "other": "another user", "root": "root"}[f.owner]
        group = {"user": "the user's group", "other": "that user's group"}.get(f.group, f.group)
        rows.append(f"| `{f.name}` | {f.what} | {owner}, {group}, {f.mode} | {f.client} | {f.kubectl} |")
    return "\n".join(rows)
