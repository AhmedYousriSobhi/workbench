"""The six plane line types stay apart when drawn thin (issue 642): rasterised at a 1.5 px stroke."""
import itertools

from palette_lib import PAL, mp

STROKE = 1.5
RES_PX = 0.25          # sample step along the line, in px


def runs(dash: str, round_caps: bool, width: float = STROKE) -> list[tuple[bool, float]]:
    """On/off runs of one dash period as drawn: round caps lengthen each dash by the stroke width and shorten the gaps."""
    if not dash:
        return [(True, 1.0)]
    nums = [float(x) for x in dash.split()]
    if len(nums) % 2:
        nums *= 2
    out = []
    for i, n in enumerate(nums):
        on = i % 2 == 0
        n = n + width if (round_caps and on) else n - width if round_caps else n
        out.append((on, max(n, 0.0)))
    return out


def signature(name: str) -> tuple[int, float, float, float]:
    p = PAL["planes"][name]
    r = runs(p["dash"], p.get("cap") == "round")
    on = [n for o, n in r if o]
    off = [n for o, n in r if not o]
    return (len(on), sum(n for _, n in r), min(on), min(off) if off else 9)


def test_every_plane_has_its_own_line_type():
    assert len({p["dash"] for p in PAL["planes"].values()}) == 6


def test_dashes_and_gaps_survive_at_1_5px():
    """No dash or gap shrinks below one device pixel at a 1.5 px stroke, so the pattern still reads."""
    for name in mp.PLANES:
        if not PAL["planes"][name]["dash"]:
            continue
        _, _, shortest_on, shortest_off = signature(name)
        assert shortest_on >= 1.0, (name, shortest_on)
        assert shortest_off >= 1.5, (name, shortest_off)


def test_line_types_differ_by_period_or_by_number_of_marks():
    for a, b in itertools.combinations(mp.PLANES, 2):
        na, pa, *_ = signature(a)
        nb, pb, *_ = signature(b)
        if na == nb:
            assert abs(pa - pb) / max(pa, pb) >= 0.25, (a, b, pa, pb)    # same count of marks: the period must differ clearly
        else:
            assert na != nb


def test_dash_dot_and_dash_dot_dot_differ_in_mark_count():
    assert signature("k8s")[0] == 2 and signature("management")[0] == 3


def test_the_two_outline_dashes_are_distinct_from_the_planes():
    for key in ("sim_outline", "attention_node"):
        assert PAL["dashes"][key] not in {p["dash"] for p in PAL["planes"].values()}
