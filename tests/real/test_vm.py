"""virsh managedsave against a throwaway tiny libvirt domain (never touches existing VMs)."""
import subprocess
import time
import uuid

import pytest

from governor.executors import Executor
from governor.memlock import MitigationPath
from governor.planner import Action, Group

URI = "qemu:///system"
XML = """<domain type='kvm'><name>{n}</name><memory unit='MiB'>64</memory><vcpu>1</vcpu>
<os><type arch='x86_64' machine='q35'>hvm</type></os>
<devices><emulator>/usr/bin/qemu-system-x86_64</emulator></devices></domain>"""


def virsh(*a):
    return subprocess.run(["virsh", "-c", URI, *a], capture_output=True, text=True, check=False)


@pytest.fixture
def vm(tmp_path):
    name = f"workbench-test-{uuid.uuid4().hex[:6]}"
    f = tmp_path / "vm.xml"
    f.write_text(XML.format(n=name))
    r = virsh("define", str(f))
    if r.returncode:
        pytest.skip(f"cannot define domain: {r.stderr.strip()}")
    assert virsh("start", name).returncode == 0
    yield name
    virsh("destroy", name)
    virsh("managedsave-remove", name)
    virsh("undefine", name, "--managed-save", "--nvram")


def state(name):
    return virsh("domstate", name).stdout.strip()


def test_managedsave_via_executor(vm):
    assert state(vm) == "running"
    ex = Executor([Group(vm, 6, "vm")], {}, MitigationPath())
    ex(Action("save", vm, 4))
    time.sleep(1)
    assert state(vm) == "shut off"
    assert "yes" in virsh("dominfo", vm).stdout  # "Managed save: yes"
    assert virsh("start", vm).returncode == 0  # restores from the saved image
    assert state(vm) == "running"
