"""Tab completion for benchctl (#105): value completers, the shell scripts, and a reversible install.

Completers read files only (sim.yaml, profiles/, scenarios/); they never call Docker, so a Tab press stays fast
and works with the stack down. Any error returns no candidates: completion must never print a traceback.
"""
import difflib
import functools
import os
import re
from pathlib import Path

import typer

SHELLS = ("bash", "zsh", "fish")
OUTPUTS = ["table", "json", "name"]
COLUMNS = {"nodes": ["name", "kind", "state"], "services": ["name", "state"],
           "profiles": ["name", "compute_nodes", "groups"], "scenarios": ["name", "steps", "description"],
           "checks": ["position", "name", "state", "params", "description"]}
SELECTOR_VALUES = {"kind": ["compute", "gpu"], "state": ["running", "paused", "exited", "absent"]}
MARK_OPEN, MARK_CLOSE = "# >>> workbench completion >>>", "# <<< workbench completion <<<"
NAMES = ("benchctl", "./scripts/benchctl")


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a completer that raises would print a traceback into the user's prompt
            return []
    return wrapper


def _stems(folder: str) -> list[str]:
    return sorted(p.stem for p in Path(folder).glob("*.yaml"))


def _sim() -> dict:
    """sim.yaml with the workbench last built applied, as `benchctl get nodes` sees it."""
    import yaml

    from . import workbench

    base = yaml.safe_load(Path("sim.yaml").read_text())
    try:
        spec = workbench.current(base)
    except workbench.SpecError:
        return base
    return workbench.effective(base, spec)[0] if spec else base


def _starts(options: list[str], incomplete: str) -> list[str]:
    return [o for o in options if o.startswith(incomplete)]


@_safe
def profiles(incomplete: str) -> list[str]:
    return _starts(_stems("profiles"), incomplete)


@_safe
def scenarios(incomplete: str) -> list[str]:
    return _starts(_stems("scenarios"), incomplete)


@_safe
def checks(incomplete: str) -> list[str]:
    from . import nhc

    return _starts([c.name for c in nhc.load()], incomplete)


@_safe
def check_templates(incomplete: str) -> list[str]:
    from . import nhc

    return _starts([t["name"] for t in nhc.templates()], incomplete)


@_safe
def users(incomplete: str) -> list[str]:
    return _starts([u["name"] for u in _sim().get("users", [])], incomplete)


@_safe
def parts(incomplete: str) -> list[str]:
    from . import workbench

    return _starts(list(workbench.PARTS), incomplete)


@_safe
def outputs(incomplete: str) -> list[str]:
    return _starts(OUTPUTS, incomplete)


@_safe
def nodes(incomplete: str) -> list[str]:
    from . import nodes as node_mod

    return _starts([n for n, _ in node_mod.node_names(_sim())], incomplete)


@_safe
def targets(incomplete: str) -> list[str]:
    """Nodes plus the service containers, for `logs` and `exec`."""
    from . import compose

    services = [s for group in compose.CATALOG.values() for s in group]
    known = nodes("")
    return _starts(sorted(set(known) | set(services)), incomplete) if known else []


@_safe
def columns(ctx: typer.Context, incomplete: str) -> list[str]:
    return _starts(COLUMNS.get(ctx.command.name or "", []), incomplete)


@_safe
def selectors(ctx: typer.Context, incomplete: str) -> list[str]:
    """`kind=`, then `kind=gpu`; a comma starts the next selector."""
    head, _, last = incomplete.rpartition(",")
    keys = [c for c in COLUMNS.get(ctx.command.name or "", []) if c != "description"]
    key, eq, _value = last.partition("=")
    if eq:
        values = SELECTOR_VALUES.get(key.rstrip("!"), [])
        return [f"{head + ',' if head else ''}{key}={v}" for v in values if v.startswith(_value)]
    return [f"{head + ',' if head else ''}{k}=" for k in keys if k.startswith(last)]


def explain(value: str, valid: list[str], what: str) -> str:
    """One message for a value that is not valid: a close match when there is one, then the full list."""
    close = difflib.get_close_matches(value, valid, n=1)
    hint = f" Did you mean '{close[0]}'?" if close else ""
    return f"unknown {what} '{value}'.{hint} Valid: {', '.join(valid) if valid else 'none found'}."


def check_value(value: str | None, valid: list[str], what: str) -> str | None:
    """Option callback: pass `value` through, or exit 2 with `explain`. Silent when the list cannot be read."""
    if value is None or not valid or value in valid:
        return value
    raise typer.BadParameter(explain(value, valid, what))


# ---- scripts and install ------------------------------------------------------------------------

def script(shell: str, prog: str = "benchctl") -> str:
    """The completion script for `shell`, also registered for `./scripts/benchctl` (bash, zsh)."""
    from typer.completion import completion_init, get_completion_script

    if shell not in SHELLS:
        raise typer.BadParameter(f"unknown shell '{shell}'. Valid: {', '.join(SHELLS)}.")
    completion_init()
    text = get_completion_script(prog_name=prog, complete_var=f"_{prog.replace('-', '_').upper()}_COMPLETE", shell=shell)
    if shell == "bash":
        func = re.search(r"complete [^\n]*-F (\S+) " + re.escape(prog), text).group(1)
        text += "".join(f"complete -o default -F {func} {n}\n" for n in NAMES[1:])
    elif shell == "zsh":
        func = re.search(r"compdef (\S+) " + re.escape(prog), text).group(1)
        text += "".join(f"compdef {func} {n}\n" for n in NAMES[1:])
    return text


def detect_shell() -> str | None:
    name = Path(os.environ.get("SHELL", "")).name
    return name if name in SHELLS else None


def _home() -> Path:
    return Path.home()


def _paths(shell: str) -> tuple[Path, Path | None]:
    """(completion script file, rc file to source it from; None for fish, which autoloads)."""
    h = _home()
    if shell == "fish":
        return h / ".config/fish/completions/benchctl.fish", None
    return h / ".local/share/workbench" / f"benchctl.{shell}", h / (".bashrc" if shell == "bash" else ".zshrc")


def installed(shell: str) -> bool:
    f, rc = _paths(shell)
    return f.is_file() and (rc is None or (rc.is_file() and MARK_OPEN in rc.read_text()))


def install(shell: str, repo: Path) -> list[str]:
    """Write the script, link `benchctl` into ~/.local/bin and source the script from the rc file. Never sudo.
    Returns what was done, one line each."""
    done = []
    f, rc = _paths(shell)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(script(shell))
    done.append(f"wrote {f}")
    link = _home() / ".local/bin/benchctl"
    if not link.exists() and not link.is_symlink():
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(repo / "scripts/benchctl")
        done.append(f"linked {link} to scripts/benchctl (so plain `benchctl` works; add ~/.local/bin to PATH if it is not)")
    if rc is not None:
        text = rc.read_text() if rc.is_file() else ""
        if MARK_OPEN not in text:
            block = f'{MARK_OPEN}\n[ -f "{f}" ] && . "{f}"\n{MARK_CLOSE}\n'
            rc.write_text(text + ("" if text.endswith("\n") or not text else "\n") + block)
            done.append(f"added one line to {rc}")
    done.append("open a new terminal to use it")
    return done


def remove(shell: str, repo: Path) -> list[str]:
    done = []
    f, rc = _paths(shell)
    if f.is_file():
        f.unlink()
        done.append(f"deleted {f}")
    if rc is not None and rc.is_file() and MARK_OPEN in rc.read_text():
        text = re.sub(rf"\n?{re.escape(MARK_OPEN)}.*?{re.escape(MARK_CLOSE)}\n", "\n", rc.read_text(), flags=re.DOTALL)
        rc.write_text(text)
        done.append(f"removed the block from {rc}")
    link = _home() / ".local/bin/benchctl"
    if link.is_symlink() and link.resolve() == (repo / "scripts/benchctl").resolve():
        link.unlink()
        done.append(f"deleted {link}")
    return done or ["nothing to remove"]


def asked_flag() -> Path:
    return _home() / ".config/workbench/completion-asked"
