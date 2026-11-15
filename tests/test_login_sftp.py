"""The login node's sftp subsystem (issue 716): the generated config, not a live sshd."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGIN = ROOT / "images/login"


def directives(text: str) -> list[list[str]]:
    return [line.split() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_sshd_config_has_the_sftp_subsystem_and_no_chroot():
    d = directives((LOGIN / "sshd_config").read_text())
    assert ["Subsystem", "sftp", "/usr/lib/openssh/sftp-server"] in d
    assert not any(x[0].lower() in ("chrootdirectory", "forcecommand") for x in d)  # no new jail: the user sees the real home


def test_the_server_comes_from_node_base_single_apt_layer():
    base = (ROOT / "images/node-base/Dockerfile").read_text()
    layer = base.rsplit("FROM ubuntu:24.04\n", 1)[1]  # the final stage: one apt layer
    assert "openssh-sftp-server" in layer and layer.count("apt-get install") == 1
    assert not re.search(r"^RUN .*apt-get", (LOGIN / "Dockerfile").read_text(), re.MULTILINE)


def test_login_sh_keeps_the_subsystem_line_it_appends_to():
    # login.sh appends AllowUsers to the file; it must not rewrite the file and drop the Subsystem line
    sh = (LOGIN / "login.sh").read_text()
    assert ">> /etc/ssh/sshd_config" in sh and not re.search(r"[^>]> /etc/ssh/sshd_config", sh)
