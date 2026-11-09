"""slurmctld's request counts from sdiag (#397)."""

import subprocess

import pytest

from benchctl import slurmstats

SDIAG = """\
Server thread count:  3
Agent queue size:     0

Remote Procedure Call statistics by message type
	REQUEST_PARTITION_INFO                  ( 2009) count:120    ave_time:210    total_time:25200
	MESSAGE_NODE_REGISTRATION_STATUS        ( 1002) count:900    ave_time:1500   total_time:1350000
	REQUEST_PING                            ( 1008) count:30     ave_time:50     total_time:1500
"""


def run(out, rc=0):
    return lambda c: subprocess.CompletedProcess(c, rc, out, "")


def test_sdiag_is_parsed_busiest_first_with_totals():
    got = slurmstats.collect(run(SDIAG))
    assert got["rpcs"] == 1050 and got["threads"] == 3
    assert got["top"][0] == {
        "type": "MESSAGE_NODE_REGISTRATION_STATUS",
        "count": 900,
        "ave_us": 1500,
    }


def test_no_answer_or_other_output_is_an_error_not_zeros():
    with pytest.raises(RuntimeError):
        slurmstats.collect(run("", 1))
    with pytest.raises(ValueError):
        slurmstats.parse("hello")


def test_only_the_busiest_few_are_listed():
    many = "".join(
        f"\tREQUEST_X{c}  ( {c}) count:{c}  ave_time:1  total_time:1\n" for c in range(1, 9)
    )
    assert len(slurmstats.parse(many)["top"]) == slurmstats.TOP


def test_slurmdbd_answers_does_not_answer_or_is_not_configured():
    assert slurmstats.dbd(run("workbench\n")) is True
    assert slurmstats.dbd(run("", 1)) is False
    assert slurmstats.dbd(lambda c: subprocess.CompletedProcess(c, 1, "", "Slurm accounting storage is disabled")) is None
    both = lambda c: subprocess.CompletedProcess(c, 0, "workbench\n" if "sacctmgr" in c else SDIAG, "")
    assert slurmstats.collect(both)["dbd"] is True


def test_node_states_come_from_sinfo_and_the_worse_state_wins():
    out = "cn001 idle\ncn002 drained\ncn002 idle\ngpu001 allocated*\ngpu002 draining\ngarbage here now\n"
    assert slurmstats.node_states(run(out)) == {"cn001": "idle", "cn002": "drained", "gpu001": "allocated", "gpu002": "draining"}
    assert slurmstats.node_states(run("", 1)) is None
    both = lambda c: subprocess.CompletedProcess(c, 0, "cn001 drained\n" if "sinfo" in c else SDIAG, "")
    assert slurmstats.collect(both)["node_states"] == {"cn001": "drained"}
