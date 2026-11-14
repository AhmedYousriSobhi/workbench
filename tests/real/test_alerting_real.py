"""Alertmanager and the default rules against the live min stack (issue 460, ADR 0072). Run: `./scripts/verify.sh --unit alerting`.

The chain under test: a fault in the GPU exporter -> a Prometheus rule fires -> Alertmanager groups it -> its webhook posts to the
Jira mock's `/sim/alerts` -> one ticket opens (a repeat comments, a resolve comments). The `promtool test rules` cases of every rule
run in the Prometheus image and need no stack. No monitoring app is connected."""
import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, exporter_up, release, wait_for

ROOT = Path(__file__).resolve().parents[2]
PROM, AM, JIRA, EXPORTER = "http://127.0.0.1:9090", "http://127.0.0.1:9093", "http://127.0.0.1:8080", "http://127.0.0.1:9400"
LABEL = "alertname-GpuXidCritical"


def get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read() or b"null")
    except (OSError, ValueError):
        return None


def ready(url: str) -> bool:
    """True when the endpoint answers 200 (Alertmanager's /-/ready is plain text, not JSON)."""
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status == 200
    except OSError:
        return False


def post(url: str, body: dict):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


BEFORE: set[str] = set()  # tickets an earlier module left (agent_recipes files one for gpu002): not this module's to count


def all_tickets() -> list[dict]:
    q = urllib.parse.quote(f"labels = {LABEL}")
    return (get(f"{JIRA}/rest/api/2/search?jql={q}&fields=summary,status,labels,comment") or {}).get("issues", [])


def tickets() -> list[dict]:
    return [t for t in all_tickets() if t["key"] not in BEFORE]


def comments(key: str) -> list[str]:
    return [c["body"] for c in (get(f"{JIRA}/rest/api/2/issue/{key}/comment") or {}).get("comments", [])]


def test_promtool_passes_every_rule_case_and_the_config_files_are_valid():
    """No stack needed: the Prometheus and Alertmanager images' own tools read the files the stack mounts."""
    def run(*a: str) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", "run", "--rm", *a], capture_output=True, text=True, timeout=180, check=False)

    mount = ["-v", f"{ROOT}:/w:ro", "-w", "/w"]
    prom_image = yaml.safe_load((ROOT / "compose/observability.yml").read_text())["services"]["prometheus"]["image"]
    am_image = yaml.safe_load((ROOT / "compose/observability.yml").read_text())["services"]["alertmanager"]["image"]
    r = run(*mount, "--entrypoint", "promtool", prom_image, "check", "rules", "observability/rules.yml")
    assert r.returncode == 0, r.stdout + r.stderr
    r = run(*mount, "--entrypoint", "promtool", prom_image, "test", "rules", "tests/rules/rules_test.yml")
    assert r.returncode == 0, r.stdout + r.stderr
    r = run(*mount, "--entrypoint", "amtool", am_image, "check-config", "observability/alertmanager.yml")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.fixture(scope="module")
def live():
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    BEFORE.update(t["key"] for t in all_tickets())
    assert wait_for(exporter_up, timeout=60)
    assert wait_for(lambda: ready(f"{AM}/-/ready"), timeout=60)
    yield sim
    post(f"{EXPORTER}/sim/inject", {"node": "gpu001", "clear": True})  # a shared module leaves the cluster as it found it
    post(f"{JIRA}/sim/tickets", {"action": "transition", "jql": f"labels = {LABEL}", "to": "Resolved"})
    release()


def test_alertmanager_runs_and_prometheus_sends_to_it(live):
    assert wait_for(lambda: bool((get(f"{PROM}/api/v1/alertmanagers") or {}).get("data", {}).get("activeAlertmanagers")), timeout=60)
    found = get(f"{PROM}/api/v1/alertmanagers")["data"]["activeAlertmanagers"]
    assert any("alertmanager:9093" in a["url"] for a in found), found
    assert wait_for(lambda: get(f"{AM}/api/v2/status") is not None, timeout=30)  # JSON


def test_the_jira_mock_is_scraped_and_its_scrapes_are_not_client_traffic(live):
    def up() -> bool:
        q = urllib.parse.quote('up{job="jira-mock"}')
        res = (get(f"{PROM}/api/v1/query?query={q}") or {}).get("data", {}).get("result", [])
        return bool(res) and res[0]["value"][1] == "1"

    assert wait_for(up, timeout=60)
    before = get(f"{JIRA}/sim/stats")["requests"]
    time.sleep(35)  # more than two scrapes
    assert get(f"{JIRA}/sim/stats")["requests"] == before


def test_an_xid_becomes_one_ticket_a_repeat_comments_and_the_clear_resolves_it(live):
    assert tickets() == [], "start from no alert tickets"
    post(f"{EXPORTER}/sim/inject", {"node": "gpu001", "xid": 79})
    assert wait_for(lambda: len(tickets()) == 1, timeout=120), "no ticket within 2 minutes"
    t = tickets()[0]
    assert t["fields"]["summary"] == "[ALERT] GpuXidCritical on gpu001"
    assert {"alert", LABEL} <= set(t["fields"]["labels"])
    assert get(f"{AM}/api/v2/alerts") is not None
    post(f"{EXPORTER}/sim/inject", {"node": "gpu001", "clear": True})
    assert wait_for(lambda: any("resolved" in c for c in comments(t["key"])), timeout=180), comments(t["key"])
    assert len(tickets()) == 1  # still one ticket: the resolve commented, it opened nothing
    assert t["fields"]["status"]["name"] == "Open" or get(f"{JIRA}/rest/api/2/issue/{t['key']}")["fields"]["status"]["name"] == "Open"
