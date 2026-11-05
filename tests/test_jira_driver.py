"""benchctl/jira_driver.py: the same ticket actions as the mock's /sim/tickets, through the standard REST API only (issue 262)."""
import importlib.util
import json
import random
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from benchctl import jira_driver as jd

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("jira", ROOT / "components/jira-mock/jira.py")
jira = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jira)


def start():
    j = jira.Jira(deliver=lambda *a: None, sleep=lambda s: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), jira.make_handler(j))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return j, f"http://127.0.0.1:{srv.server_port}", srv


@pytest.fixture
def pair():
    """Two mocks: one driven through /sim/tickets, one through the driver."""
    (ja, ua, sa), (jb, ub, sb) = start(), start()
    yield ja, ua, jb, jd.JiraDriver(ub, rng=random.Random(1))
    sa.shutdown()
    sb.shutdown()


def sim(base, **body):
    with urlopen(Request(base + "/sim/tickets", data=json.dumps(body).encode(), method="POST"), timeout=5) as r:
        return json.loads(r.read())


def view(j):
    """What a person sees: summary, labels (minus the driver's own), status, comments."""
    return sorted((i["fields"]["summary"], sorted(set(i["fields"]["labels"]) - {jd.LABEL}), i["fields"]["status"]["name"],
                   i["fields"]["priority"]["name"], [c["body"] for c in i["fields"]["comment"]["comments"]]) for i in j.issues.values())


SEQUENCE = [
    {"node": "cn003", "kind": "power", "detail": "powered off"},
    {"node": "cn003", "kind": "power", "detail": "powered off"},                     # repeat: one comment, not two tickets
    {"node": "cn003", "kind": "slurm", "detail": "Slurm state down", "merge": True},    # merged into the open ticket
    {"node": "cn004", "kind": "xid", "detail": "XID 79"},
    {"node": "cn003", "kind": "", "open": False, "detail": "back"},                     # recovery resolves cn003's tickets
    {"node": "cn003", "kind": "nic", "detail": "flapping"},                          # a new fault after recovery is a new ticket
]


def test_faults_give_the_same_tickets_as_sim_tickets(pair):
    ja, ua, jb, driver = pair
    for step in SEQUENCE:
        sim(ua, action="fault", **step)
        driver.fault(**step)
    assert view(jb) == view(ja) and len(jb.issues) == 3


def test_fault_validation_matches_the_mock(pair):
    _, _, _, driver = pair
    with pytest.raises(jd.JiraError):
        driver.fault("cn001", "nonsense")
    with pytest.raises(jd.JiraError):
        driver.fault("", "power")


def test_every_ticket_the_driver_files_carries_the_workbench_label(pair):
    _, _, jb, driver = pair
    driver.fault("cn001", "fan", "fan 2 stopped")
    driver.create("Something else")
    assert all(jd.LABEL in i["fields"]["labels"] for i in jb.issues.values()) and len(jb.issues) == 2


def test_move_walks_the_workflow_and_supplies_the_resolution(pair):
    _, _, jb, driver = pair
    key = driver.create("walk")
    driver.move(key, "Resolved")
    assert jb.issues[key]["fields"]["status"]["name"] == "Resolved"
    assert jb.issues[key]["fields"]["resolution"]["name"] == "Done"
    driver.move(key, "Closed")
    assert jb.issues[key]["fields"]["status"]["name"] == "Closed"
    driver.move(key, "Open")  # reopen
    assert jb.issues[key]["fields"]["status"]["name"] == "Open"


def test_tick_leaves_a_ticket_in_its_cooldown_alone(pair):
    _, _, jb, driver = pair
    key = driver.create("busy")
    before = json.dumps(jb.issues[key], sort_keys=True)
    for _ in range(15):
        driver.tick()  # the only thing it may do now is file a new ticket
    assert json.dumps(jb.issues[key], sort_keys=True) == before
    assert len(jb.issues) > 1 and all(jd.LABEL in i["fields"]["labels"] for i in jb.issues.values())


def test_tick_changes_tickets_once_they_are_quiet(pair):
    _, _, jb, driver = pair
    keys = [driver.create(f"t{n}") for n in range(6)]
    for i in jb.issues.values():
        i["fields"]["updated"] = "2020-01-01T00:00:00.000+0000"  # aged past the cooldown
    seen = {driver.tick() for _ in range(25)} - {None}
    kinds = {d.split()[0] for d in seen}
    assert {"advance", "comment"} <= kinds
    statuses = {jb.issues[k]["fields"]["status"]["name"] for k in keys}
    comments = sum(len(jb.issues[k]["fields"]["comment"]["comments"]) for k in keys)
    assert statuses != {"Open"} or comments > 0


def test_adf_round_trip_and_the_v2_string_is_kept_as_is():
    assert jd.text_of(jd.adf("Recovered. node back")) == "Recovered. node back"
    assert jd.text_of("plain") == "plain" and jd.text_of(None) == ""
    assert jd.JiraDriver("http://x").text("hi") == "hi" and jd.JiraDriver("http://x", adf=True).text("hi")["type"] == "doc"


def test_credentials_only_from_the_environment():
    with pytest.raises(jd.JiraError, match="environment"):
        jd.JiraDriver.from_env({})
    with pytest.raises(jd.JiraError):
        jd.JiraDriver.from_env({"WORKBENCH_JIRA_CLOUD_URL": "http://insecure", "JIRA_EMAIL": "you@example.com", "JIRA_API_TOKEN": "t"})
    d = jd.JiraDriver.from_env({"WORKBENCH_JIRA_CLOUD_URL": "https://site.example/", "JIRA_EMAIL": "you@example.com", "JIRA_API_TOKEN": "t"})
    assert d.base == "https://site.example" and d.api == 3 and d.auth.startswith("Basic ")


def test_the_driver_never_calls_the_mocks_private_endpoints():
    assert "/sim/" not in Path(jd.__file__).read_text().replace("/sim/*", "").replace("`/sim/tickets` and `/sim/activity`", "")


def test_fault_table_matches_the_mock():
    assert jd.FAULTS == jira.FAULTS
