"""Node selectors for scenarios (issue 470): a scenario says `first gpu node` or `any 2 cpu nodes`, never `gpu001`, so it runs on
any sim.yaml. The words:

    first | second | third | fourth | last | any | all   [N]   gpu | cpu | k8s gpu | k8s cpu   node | nodes

`first`, `second`... pick one node by place; `first 2`, `last 2` and `any 2` pick that many ("any" is deterministic: the first N, so a
run is repeatable); `all gpu nodes` picks every node of the kind. `gpu` and `cpu` are the Slurm nodes (sim.yaml `gpu`, `compute`), `k8s gpu` and
`k8s cpu` the Kubernetes nodes (sim.yaml `kubernetes.nodes`). A selector is a whole string value (in a list it expands into several
names), or sits in braces inside a longer text (`"xid on {first gpu node}"`, which needs exactly one node)."""
import re

from . import k8s
from .nodes import node_names

ORDINALS = {"first": 0, "second": 1, "third": 2, "fourth": 3}
_BODY = r"(?:(first|second|third|fourth|last|any|all)(?: (\d+))? (gpu|cpu|k8s gpu|k8s cpu) nodes?)"
WHOLE = re.compile(rf"\s*{_BODY}\s*", re.IGNORECASE)
INLINE = re.compile(r"\{\s*" + _BODY + r"\s*\}", re.IGNORECASE)
NODE_NAME = re.compile(r"\b(?:cn|gpu|kcn|kgpu)\d{3}\b")  # what a scenario must not write by hand (tests/test_scenario.py)


class SelectorError(ValueError):
    pass


def pool(sim: dict, kind: str, pools: dict[str, list[str]] | None = None) -> list[str]:
    """The nodes of one kind: sim.yaml's, or `pools` when given (a config lab run's own nodes, issue 554: `cpu` and `gpu` only)."""
    if pools is not None:
        return list(pools.get(kind, []))
    if kind in ("gpu", "cpu"):
        return [n for n, k in node_names(sim) if k == ("gpu" if kind == "gpu" else "compute")]
    want_gpu = kind == "k8s gpu"
    return [n for n, g in k8s.node_list(sim) if bool(g.get("gpus")) == want_gpu]


def pick(word: str, count: str | None, kind: str, sim: dict, pools: dict[str, list[str]] | None = None) -> list[str]:
    word, kind = word.lower(), kind.lower()
    names = pool(sim, kind, pools)
    where = "the lab" if pools is not None else "sim.yaml"
    n = int(count) if count else None
    if word in ORDINALS:
        if n is not None or ORDINALS[word] >= len(names):
            raise SelectorError(f"{word} {kind} node: {where} has {len(names)} {kind} node(s)")
        return [names[ORDINALS[word]]]
    if word == "all":
        out = names
    elif word == "last":
        out = names[-(n or 1):]
    else:  # any
        out = names[:n or 1]
    if (n or 1) > len(names) and word != "all":
        raise SelectorError(f"{word} {n or 1} {kind} node(s): {where} has only {len(names)}")
    if not out:
        raise SelectorError(f"no {kind} nodes in {where}")
    return out


def _text(value: str, sim: dict, pools: dict[str, list[str]] | None = None):
    """A list of names for a whole-string selector, the text with names filled in for braces, else the value unchanged."""
    if m := WHOLE.fullmatch(value):
        return pick(m.group(1), m.group(2), m.group(3), sim, pools)

    def fill(m: re.Match) -> str:
        names = pick(m.group(1), m.group(2), m.group(3), sim, pools)
        if len(names) != 1:
            raise SelectorError(f"{{{m.group(0)[1:-1].strip()}}} in a text must pick one node, it picks {len(names)}")
        return names[0]
    return INLINE.sub(fill, value)


def resolve(value, sim: dict, pools: dict[str, list[str]] | None = None):
    """The same structure with every selector replaced by node names. A whole-string selector in a list becomes its names; in a
    mapping value it must pick one node."""
    if isinstance(value, dict):
        return {k: _one(resolve(v, sim, pools), v) for k, v in value.items()}
    if isinstance(value, list):
        out = []
        for v in value:
            r = resolve(v, sim, pools) if not isinstance(v, str) else _text(v, sim, pools)
            out += r if isinstance(v, str) and isinstance(r, list) else [r]
        return out
    if isinstance(value, str):
        return _text(value, sim, pools)
    return value


def _one(resolved, original):
    if isinstance(original, str) and isinstance(resolved, list):
        if len(resolved) != 1:
            raise SelectorError(f"{original!r} picks {len(resolved)} nodes where one is needed")
        return resolved[0]
    return resolved


def resolve_scenario(sc: dict, sim: dict, pools: dict[str, list[str]] | None = None) -> dict:
    """The scenario with selectors in its steps and expectations resolved against sim.yaml, or against `pools` (a config lab run's
    nodes, issue 554) when given (name, ref, description are kept)."""
    try:
        return {**sc, "steps": resolve(sc.get("steps") or [], sim, pools), **({"expect": resolve(sc["expect"], sim, pools)} if sc.get("expect") else {})}
    except SelectorError as e:
        from .scenario import ScenarioError
        raise ScenarioError(f"{sc.get('name', 'scenario')}: {e}") from e
