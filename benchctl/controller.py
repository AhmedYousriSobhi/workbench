"""The controller node and the jump host (SPEC C-20, C-21, issues 415, 416).

Controller: the machine slurmctld runs on. With `controller.enabled` the `slurmctld` container also runs sshd (admins ssh in
to `controller`, 10.141.255.1, or 127.0.0.1:2221), is the BCM head node (the fake `cmsh` lives here, not on the login nodes)
and lets only whitelisted users in: the users named in `controller.allow.users` plus every user whose `groups` meet
`controller.allow.groups`. Without it the login node acts as the head node, as before.

Jump host: the `jump` part, a bare sshd (nothing else installed) on 10.141.255.5 and 127.0.0.1:2220, used with `ssh -J` to
reach a key-login node or the controller. Teleport nodes keep Teleport as their way in."""
CONTROLLER = "slurmctld"      # the container that hosts the controller
ALIAS = "controller"          # the name users type
SSH_PORT = 2221               # fallback published port (login nodes use 2222 and up)
JUMP_PORT = 2220
JUMP = "jump"
EXTRA_MB = 24                 # sshd on the controller


class ControllerError(ValueError):
    pass


def enabled(sim: dict) -> bool:
    return bool((sim.get("controller") or {}).get("enabled"))


def allowed_users(sim: dict) -> list[str]:
    """The simulated users who may ssh to the controller, in sim.yaml order."""
    allow = (sim.get("controller") or {}).get("allow") or {}
    names, groups = set(allow.get("users") or []), set(allow.get("groups") or [])
    return [u["name"] for u in sim.get("users", []) if u["name"] in names or groups & set(u.get("groups") or [])]


def problems(sim: dict) -> list[str]:
    """Why the controller cannot work: the whitelist lets nobody in, or names a user that does not exist."""
    if not enabled(sim):
        return []
    out = []
    known = {u["name"] for u in sim.get("users", [])}
    allow = (sim.get("controller") or {}).get("allow") or {}
    out += [f"controller.allow.users names {n!r}, who is not in users:" for n in allow.get("users") or [] if n not in known]
    if not allowed_users(sim):
        out.append("controller.enabled is on but controller.allow lets no user in: list users or groups (and give those users `groups:`)")
    return out
