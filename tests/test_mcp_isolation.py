"""The MCP package stays out of every process but `benchctl serve mcp` (SPEC AG-4, W-1; ADR 0064). Runs without the [mcp] extra."""
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchctl import cli

ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


@pytest.fixture(autouse=True)
def in_repo(monkeypatch):
    monkeypatch.chdir(ROOT)


def test_only_serve_mcp_imports_the_mcp_package():
    """The web page, the governor and plain benchctl startup (and tab completion) never load it: it brings pydantic (SPEC W-1, ADR 0008)."""
    for code in ("import webui.app", "import governor.__main__", "import benchctl.cli", "import benchctl.completion"):
        probe = f"import sys; {code}; sys.exit(1 if 'mcp' in sys.modules or 'pydantic' in sys.modules else 0)"
        r = subprocess.run([sys.executable, "-I", "-c", probe], cwd=ROOT, check=False, capture_output=True, text=True)
        assert r.returncode == 0, f"{code} loaded mcp or pydantic: {r.stderr[-300:]}"


def test_serve_mcp_without_the_extra_says_how_to_install_it(monkeypatch):
    import benchctl

    class Block:  # the extra is not installed: importing the server module fails
        @staticmethod
        def find_spec(name, path=None, target=None):
            if name == "benchctl.mcp_server":
                raise ImportError("No module named 'mcp'")

    monkeypatch.delitem(sys.modules, "benchctl.mcp_server", raising=False)
    monkeypatch.delattr(benchctl, "mcp_server", raising=False)
    monkeypatch.setattr(sys, "meta_path", [Block, *sys.meta_path])
    monkeypatch.setenv("BENCHCTL_AGENT", "1")
    r = runner.invoke(cli.app, ["serve", "mcp"])
    assert r.exit_code == 1 and r.output.strip() == "WB-EXTRA-MISSING: the MCP package is not installed | fix: pip install -e '.[mcp]'"
