import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("node_watch", Path(__file__).resolve().parent.parent / "images/slurm-ctl/node_watch.py")
node_watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(node_watch)

SINFO = "cn001|idle|none\ncn002|down*|Not responding\ngpu001|drained|NHC: GPU XID 79\ngpu002|draining|\ncn003|mixed|none\n"


def test_nodes_out_of_service_are_found_with_their_reason():
    assert node_watch.bad_nodes(SINFO) == {"cn002": "Slurm state down: Not responding", "gpu001": "Slurm state drained: NHC: GPU XID 79",
                                           "gpu002": "Slurm state draining"}
    assert node_watch.bad_nodes("") == {}


def test_a_ticket_is_filed_when_a_node_leaves_service_and_resolved_when_it_returns():
    one = {"cn002": "Slurm state down"}
    posts = node_watch.changes({}, one)
    assert posts == [{"action": "fault", "node": "cn002", "kind": "slurm", "detail": "Slurm state down", "merge": True}]
    assert node_watch.changes(one, one) == []  # nothing new: no second ticket, no comment storm
    back = node_watch.changes(one, {})
    assert len(back) == 1 and back[0]["open"] is False and back[0]["node"] == "cn002"
    assert [p["node"] for p in node_watch.changes(one, {"cn002": "x", "gpu001": "y"})] == ["gpu001"]
