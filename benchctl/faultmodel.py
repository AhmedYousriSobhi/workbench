"""Fault model (issue 470): one definition per fault, every physical effect applied together.

A scenario step `{target: fault, do: {inject: gpu-fell-off-bus, nodes: [first gpu node]}}` (or `clear:`) stands for all the
effects below, so a scenario injects the ROOT CAUSE and asserts how the system reacts (Slurm drains through NHC, a ticket is
filed through node_watch and the fault side effects), instead of scripting the reaction itself. The effects are ordinary
steps; `scenario.plan` expands them, so the timeline, the preview and the run see exactly what happens.

Effects are (delay seconds, target, do). `{param}` in a value is filled from the step's own settings, falling back to the
fault's `defaults`. The BCM mock models only a device state (UP/DOWN), not health checks, so no fault writes there."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Fault:
    describe: str
    inject: tuple
    clear: tuple
    defaults: dict = field(default_factory=dict)


FAULTS: dict[str, Fault] = {
    "gpu-fell-off-bus": Fault(
        "A GPU drops off the PCIe bus: DCGM and nvidia-smi report XID 79, the BMC logs a critical event.",
        inject=((0, "gpu-exporter", {"xid": 79}),
                (0, "bmc-farm", {"event": "GPU has fallen off the bus (XID 79)", "severity": "Critical"})),
        clear=((0, "gpu-exporter", {"clear": True}),
               (0, "bmc-farm", {"event": "GPU is back on the bus", "severity": "OK"}))),
    "fan-failure": Fault(
        "Fans fail and the CPU temperature climbs: Redfish shows the failed fans, then the rising temperature.",
        inject=((0, "bmc-farm", {"fan_fail": "{fans}"}),
                (10, "bmc-farm", {"ramp": {"to": "{to}", "over": "{over}"}})),
        clear=((0, "bmc-farm", {"clear": True}),),
        defaults={"fans": 3, "to": 88, "over": "60s"}),
    "ib-port-down": Fault(
        "InfiniBand ports go down: the exporter's port state and the node's link counters change.",
        inject=((0, "gpu-exporter", {"ib_down": "{ports}"}),),
        clear=((0, "gpu-exporter", {"ib_down": 0}),),
        defaults={"ports": 1}),
    "nic-flap": Fault(
        "The Ethernet link keeps changing state.",
        inject=((0, "gpu-exporter", {"nic_flap": True}),),
        clear=((0, "gpu-exporter", {"nic_flap": False}),)),
}
SETTINGS = {"nodes", "inject", "clear"}


def problems(do: dict) -> list[str]:
    inject, clear = do.get("inject"), do.get("clear")
    if bool(inject) == bool(clear):
        return ["fault needs exactly one of inject: NAME or clear: NAME"]
    name = inject or clear
    f = FAULTS.get(name)
    if f is None:
        return [f"unknown fault {name!r} (known: {', '.join(FAULTS)})"]
    out = [f"fault {name}: unknown setting {k!r}" for k in sorted(set(do) - SETTINGS - set(f.defaults))]
    if not do.get("nodes"):
        out.append(f"fault {name}: needs nodes: [...]")
    return out


def _fill(value, params: dict):
    if isinstance(value, dict):
        return {k: _fill(v, params) for k, v in value.items()}
    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        return params[value[1:-1]]
    return value


def effects(do: dict) -> list[tuple[float, str, dict]]:
    """(delay, target, do) for one fault step, every effect carrying the step's nodes."""
    name = do.get("inject") or do.get("clear")
    f = FAULTS[name]
    params = {**f.defaults, **{k: v for k, v in do.items() if k not in SETTINGS}}
    return [(delay, target, {"nodes": list(do["nodes"]), **_fill(body, params)})
            for delay, target, body in (f.inject if do.get("inject") else f.clear)]
