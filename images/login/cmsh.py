#!/usr/bin/env python3
"""Fake read-only `cmsh` for the login node, which doubles as the BCM head node (ADR 0013, issue 66).

Answers the fixed one-shot scripts a monitoring client runs (`cmsh -c "device; list"`, `device; status`,
`category; list`, `jobqueue; list`, `monitoring; healthchecks; list`) from bcm-mock's node state, so a node a scenario
took down shows DOWN. Anything that would change the cluster is refused. The table layout follows real cmsh closely
enough for a parser; it is an assumed subset until a real capture exists. The interactive console is a read-only subset
(modes, list/status, `..`, exit).
"""
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request

BCM_URL = os.environ.get("WORKBENCH_BCM_URL", "http://bcm-mock:8081/json")
HEAD_IP = "10.141.255.254"
HEALTHCHECKS = [("DeviceIsUp", "Checks whether the device is up"), ("ManagedServicesOk", "Checks that the managed services run"),
                ("failedprejob", "Checks for jobs that failed their prejob health check")]
MUTATING = {"add", "append", "clear", "clone", "commit", "foreach", "power", "reboot", "remove", "rename", "run", "set",
            "shutdown", "swap", "unset", "use", "refresh", "removefrom", "reset", "services", "ssh"}


class Unreachable(Exception):
    pass


def call(service: str, name: str, args: list | None = None):
    req = urllib.request.Request(BCM_URL, data=json.dumps({"service": service, "call": name, "args": args or []}).encode(),
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Unreachable(str(e)) from e
    if body.get("error"):
        raise Unreachable(body["error"])
    return body["result"]


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Columns padded to their widest cell, a dashed rule under the header, as cmsh prints."""
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    fmt = lambda cells: " ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()
    return "\n".join([fmt(headers), " ".join("-" * w for w in widths), *(fmt(r) for r in rows)])


def state(s: str) -> str:
    return "[" + s.center(8) + "]"


def devices() -> list[dict]:
    head = {"hostname": socket.gethostname(), "childType": "HeadNode", "category": "", "ip": HEAD_IP, "status": "UP",
            "mac": "FA:16:3E:00:00:FE"}
    nodes = [{**d, "mac": f"FA:16:3E:00:00:{i:02X}"} for i, d in enumerate(call("cmdevice", "getDevices"), 1)]
    return [head, *nodes]


def device_list() -> str:
    rows = [[d["childType"], d["hostname"], d["mac"], d["category"], d["ip"], "internalnet", state(d["status"])] for d in devices()]
    return table(["Type", "Hostname (key)", "MAC (key)", "Category", "IP", "Network", "Status"], rows)


def device_status() -> str:
    ds = devices()
    width = max(len(d["hostname"]) for d in ds) + 4
    return "\n".join(f"{d['hostname']} {'.' * (width - len(d['hostname']))} {state(d['status'])}" for d in ds)


def category_list() -> str:
    ds = devices()
    rows = [[c, f"default-image-{c}", str(sum(1 for d in ds if d['category'] == c))] for c in sorted({d["category"] for d in ds[1:]})]
    return table(["Name (key)", "Software image", "Nodes"], rows)


def jobqueue_list() -> str:
    r = subprocess.run(["sinfo", "-h", "-o", "%R %D"], capture_output=True, text=True, check=False)
    rows = [["slurm", *line.split()] for line in r.stdout.splitlines() if line.strip()]
    return table(["Type", "Name (key)", "Nodes"], rows)


def healthchecks_list() -> str:
    return table(["Name (key)", "Description"], [list(h) for h in HEALTHCHECKS])


def device_show(name: str) -> str:
    d = next((x for x in devices() if x["hostname"] == name), None)
    if d is None:
        raise Unreachable(f"no such device: {name}")
    rows = [["Hostname", d["hostname"]], ["Type", d["childType"]], ["Category", d["category"]], ["IP", d["ip"]],
            ["MAC", d["mac"]], ["Network", "internalnet"], ["Status", d["status"]]]
    return table(["Parameter", "Value"], rows)


def category_show(name: str) -> str:
    ds = devices()
    if name not in {d["category"] for d in ds[1:]}:
        raise Unreachable(f"no such category: {name}")
    members = [d["hostname"] for d in ds if d["category"] == name]
    return table(["Parameter", "Value"], [["Name", name], ["Software image", f"default-image-{name}"], ["Nodes", ",".join(members)]])


SCRIPTS = {("device", "list"): device_list, ("device", "status"): device_status, ("category", "list"): category_list,
           ("jobqueue", "list"): jobqueue_list, ("monitoring", "healthchecks", "list"): healthchecks_list}


def run(script: str) -> tuple[int, str, str]:
    """(exit status, stdout, stderr) for one `-c` script."""
    parts = tuple(p.strip() for p in script.split(";") if p.strip())
    if not parts:
        return 0, "", ""
    for p in parts:
        verb = p.split()[0]
        if verb in MUTATING:
            return 1, "", f"Error: this cmsh is read-only (workbench); '{p}' would change the cluster\n"
    fn = SCRIPTS.get(parts)
    if fn is None and len(parts) == 2 and len(parts[1].split()) == 2 and parts[1].split()[0] == "show":
        fn = {"device": device_show, "category": category_show}.get(parts[0])
        if fn:
            arg = parts[1].split()[1]
            fn = (lambda f=fn, a=arg: f(a))
    if fn is None:
        return 1, "", f"Error: unknown command: {'; '.join(parts)}\n"
    try:
        return 0, fn() + "\n", ""
    except Unreachable as e:
        return 1, "", f"Unable to connect to CMDaemon: {e}\n"


ROOT_MODES = {"device", "category", "jobqueue", "monitoring"}
COMMANDS = {  # what each mode offers, with the line `help` prints
    (): {"device": "enter device mode (nodes)", "category": "enter category mode (node categories)",
         "jobqueue": "enter job queue mode (Slurm partitions)", "monitoring": "enter monitoring mode",
         "help": "show this list", "exit": "leave cmsh", "quit": "leave cmsh"},
    ("device",): {"list": "list devices", "status": "show the status of every device", "show": "show one device: show <name>"},
    ("category",): {"list": "list categories", "show": "show one category: show <name>"},
    ("jobqueue",): {"list": "list job queues"},
    ("monitoring",): {"healthchecks": "enter health checks mode"},
    ("monitoring", "healthchecks"): {"list": "list health checks"},
}
COMMON = {"..": "go up one level", "help": "show this list", "exit": "leave cmsh", "quit": "leave cmsh"}


def commands(path: list[str]) -> dict[str, str]:
    return {**COMMANDS.get(tuple(path), {}), **({} if not path else COMMON)}


def help_text(path: list[str]) -> str:
    cmds = commands(path)
    width = max(map(len, cmds))
    return "\n".join([f"Commands here ({'->'.join(path) or 'top level'}), read-only:", *(f"  {c.ljust(width)}  {d}" for c, d in cmds.items()),
                      "Several commands on one line are separated by ;"])


def completions(path: list[str], buffer: str, text: str) -> list[str]:
    """Tab candidates for `text`, the word being typed at the end of `buffer`."""
    stmt = buffer.rsplit(";", 1)[-1].lstrip()
    words = stmt.split()
    first = len(words) == 0 or (len(words) == 1 and not stmt.endswith(" "))
    if first:
        pool = list(commands(path))
    elif words[0] == "show" and path in (["device"], ["category"]):
        try:
            ds = devices()
            pool = [d["hostname"] for d in ds] if path == ["device"] else sorted({d["category"] for d in ds[1:]})
        except Unreachable:
            pool = []
    else:
        pool = []
    return sorted(c for c in pool if c.startswith(text))


def setup_readline(path: list[str]) -> None:
    """Tab completion and line editing when a person types (a terminal); a pipe has no use for it."""
    try:
        import readline
    except ImportError:
        return
    readline.set_completer_delims(" \t;")
    readline.parse_and_bind("tab: complete")
    readline.set_completer(lambda text, n: (completions(path, readline.get_line_buffer(), text) + [None])[n])


def console(stdin, stdout, stderr) -> int:
    """Interactive session: `[host]% device` enters a mode, `list` prints, `..` goes up, `exit` leaves. Read-only."""
    host, path = socket.gethostname(), []
    tty = stdin.isatty() if hasattr(stdin, "isatty") else False
    if tty:
        setup_readline(path)
    while True:
        prompt = f"[{'->'.join([host, *path])}]% "
        if tty:  # input() is what engages readline: tab completion, history, cursor keys
            try:
                line = input(prompt) + "\n"
            except EOFError:
                line = ""
        else:
            stdout.write(prompt)
            stdout.flush()
            line = stdin.readline()
        if not line:
            stdout.write("\n")
            return 0
        for stmt in (s.strip() for s in line.split(";")):
            words = stmt.split()
            if not words:
                continue
            verb = words[0]
            if verb in ("exit", "quit"):
                return 0
            if verb == "..":
                if path:
                    path.pop()  # in place: the tab completer holds this same list
            elif verb in ("help", "?"):
                stdout.write(help_text(path) + "\n")
            elif verb in MUTATING:
                stderr.write(f"Error: this cmsh is read-only (workbench); '{stmt}' would change the cluster\n")
            elif (verb in ROOT_MODES and not path) or (path == ["monitoring"] and verb == "healthchecks"):
                path.append(verb)
            else:
                _code, out, err = run("; ".join([*path, stmt]))
                stdout.write(out)
                stderr.write(err.replace("unknown command: " + "; ".join([*path, stmt]), f"unknown command: {stmt}"))


def main(argv: list[str]) -> int:
    script, args = None, [a for a in argv if a != "--hide-events"]
    if args[:1] in (["-v"], ["--version"]):
        print("cmsh (workbench fake, read-only)")
        return 0
    if args[:1] in (["-c"], ["--command"]) and len(args) >= 2:
        script = args[1]
    if script is None:
        return console(sys.stdin, sys.stdout, sys.stderr)
    code, out, err = run(script)
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
