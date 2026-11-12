"""The phone menu rule must come after the desktop `.menu` rule, or it never applies (#182, #310)."""
import re

from webui_src import page_source

HTML = page_source()


def test_the_phone_menu_rule_comes_after_the_desktop_one():
    desktop = re.search(r"\n  \.menu \{ position:absolute;[^\n]*", HTML).group(0)
    phone = re.search(r"@media \(max-width:860px\) \{ \.menu \{[^\n]*", HTML).group(0)
    assert "position:fixed" in phone and "overflow-y:auto" in phone and "max-height" in phone
    assert HTML.index(phone) > HTML.index(desktop), "an equal-weight rule that comes first loses to the desktop rule"
