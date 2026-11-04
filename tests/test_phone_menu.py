"""The phone menu rule must win over the desktop `.menu` rule that comes later in the stylesheet (#182)."""
import re
from pathlib import Path

HTML = Path("webui/static/index.html").read_text()


def test_the_phone_menu_rule_is_more_specific_than_the_desktop_one():
    phone = re.search(r"@media \(max-width:860px\) \{.*?\n  \}", HTML, re.DOTALL).group(0)
    assert ".menuwrap .menu {" in phone and "position:fixed" in phone and "overflow-y:auto" in phone
    desktop = re.search(r"\n  \.menu \{ position:absolute;[^\n]*", HTML).group(0)
    assert HTML.index(desktop) > HTML.index(phone), "the plain .menu rule is later: only specificity keeps the phone rule"
