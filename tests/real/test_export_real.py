"""The exported profile points at things that really answer on the live stack (#91)."""
import json
import os
import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from benchctl import stack

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    assert wait_for(lambda: subprocess.run(["docker", "exec", "workbench-login-1", "sinfo", "-h"], capture_output=True, check=False).returncode == 0, timeout=90)
    out = tmp_path_factory.mktemp("p") / "p.json"
    r = subprocess.run([str(ROOT / "scripts/benchctl"), "export", "profile", "--profile", "min", "--bmc", "--format", "demo:1", "-o", str(out)],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    yield json.loads(out.read_text())
    stack.down(say=lambda m: None)


def test_ssh_endpoint_answers(exported):
    c = exported["clusters"][0]["connection"]
    with socket.create_connection((c["host"], c["port"]), timeout=5) as s:
        assert s.recv(4) == b"SSH-"


def test_grafana_dashboard_and_jira_project_exist(exported):
    c = exported["clusters"][0]
    token = (ROOT / "compose/generated/secrets/grafana_viewer_token").read_text().strip()
    req = urllib.request.Request(f"{c['grafana']['baseUrl']}/api/dashboards/uid/{c['grafana']['dashboardUids'][0]}",
                                 headers={"Authorization": f"Bearer {token}"})
    assert json.load(urllib.request.urlopen(req, timeout=10))["dashboard"]["uid"] == "hpc-overview"
    projects = json.load(urllib.request.urlopen(f"{c['jira']['baseUrl']}/rest/api/2/project", timeout=10))
    assert c["jira"]["projectKey"] in [p["key"] for p in projects]


def test_every_listed_bmc_node_answers_redfish(exported):
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    creds = (ROOT / "compose/generated/secrets/bmc_credentials").read_text().strip()
    import base64
    auth = base64.b64encode(creds.encode()).decode()
    for t in exported["bmc"]:
        req = urllib.request.Request(f"https://{t['address']}:{t['port']}/redfish/v1/",
                                     headers={"Host": f"bmc-{t['node']}", "Authorization": f"Basic {auth}"})
        assert t["node"] in urllib.request.urlopen(req, timeout=10, context=ctx).read().decode()
