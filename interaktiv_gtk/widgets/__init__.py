"""
Widgets the reader needs that libadwaita 1.2 does not have.

The oldest board runs Debian 12, which ships GTK 4.8 and libadwaita 1.2, so
the newer `Adw.NavigationView`, `Adw.ToggleGroup`, `Adw.OverlaySplitView`,
`Adw.Banner` and `Adw.AlertDialog` are replaced by the small widgets here.
`tools/check_api_floor.py` reports anything that slips above that floor.
"""

from .banner import Banner
from .dialogs import ask
from .lists import after_layout, scroll_to_item
from .navigation import PageStack
from .numpad import NumberPad
from .segmented import SegmentedControl
from .sidepanel import SidePanel
from .theme_picker import ThemePicker

__all__ = [
    "Banner",
    "NumberPad",
    "PageStack",
    "SegmentedControl",
    "SidePanel",
    "ThemePicker",
    "after_layout",
    "ask",
    "scroll_to_item",
]
