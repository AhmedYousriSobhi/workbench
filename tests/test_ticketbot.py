"""benchctl/ticketbot.py: the team and the node watcher on the host, for either Jira (issue 284)."""
import random

from test_jira_driver import jd, start

from benchctl import ticketbot


def driver(dc=False):
    j, url, srv = start()
    return j, jd.JiraDriver(url, dc=dc, rng=random.Random(3)), srv


def test_the_team_makes_the_changes_asked_for_and_stops():
    j, d, srv = driver()
    try:
        for n in range(5):
            d.create(f"t{n}")
        for i in j.issues.values():
            i["fields"]["updated"] = "2020-01-01T00:00:00.000+0000"
        said = []
        assert ticketbot.team(d, every=0, count=10, say=said.append, sleep=lambda s: None) == 10
        assert said and all(" " in line for line in said)
    finally:
        srv.shutdown()


POLLS = ["cn001|idle|none\n", "cn001|down*|Not responding\ncn002|idle|none\n", "cn001|down*|Not responding\n", "cn001|idle|none\n"]


def test_the_watcher_opens_one_ticket_per_bad_node_and_resolves_it_on_return():
    for dc in (False, True):
        j, d, srv = driver(dc)
        try:
            said, polls = [], iter(POLLS)
            ticketbot.watch_nodes(d, "workbench", every=0, rounds=4, say=said.append, read=lambda p, it=polls: next(it),
                                  sleep=lambda s: None)
            tickets = list(j.issues.values())
            assert len(tickets) == 1 and tickets[0]["fields"]["status"]["name"] == "Resolved"
            assert "fault-slurm" in tickets[0]["fields"]["labels"] and len(said) == 2
        finally:
            srv.shutdown()


def test_an_unreadable_sinfo_changes_nothing_and_a_jira_that_fails_is_retried():
    j, d, srv = driver()
    try:
        said = []
        ticketbot.watch_nodes(d, "workbench", every=0, rounds=2, say=said.append, read=lambda p: None, sleep=lambda s: None)
        assert not j.issues and not said
    finally:
        srv.shutdown()
    d2 = jd.JiraDriver("http://127.0.0.1:9")  # nothing listens here
    out = []
    ticketbot.watch_nodes(d2, "workbench", every=0, rounds=2, say=out.append, read=lambda p: "cn001|down*|x\n", sleep=lambda s: None)
    assert out and "trying again" in out[0]
