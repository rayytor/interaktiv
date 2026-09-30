"""The app's own icon set: every icon the UI names exists and can be recoloured."""

import os
import re
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from interaktiv_gtk import icons  # noqa: E402

ACTIONS = os.path.join(icons.ICON_DIR, "hicolor", "scalable", "actions")


def named_icons():
    return sorted(
        value for name, value in vars(icons).items()
        if name.isupper() and isinstance(value, str) and value.endswith("-symbolic")
    )


class TestIconSet(unittest.TestCase):
    def test_every_named_icon_has_a_file_in_the_theme_folder(self):
        # In a theme folder, not loose on the search path: GTK 4.8 only treats
        # an icon as symbolic, and so recolours it, when it came from a theme.
        for name in named_icons():
            with self.subTest(icon=name):
                self.assertTrue(os.path.isfile(os.path.join(ACTIONS, f"{name}.svg")))

    def test_icons_are_fills_not_strokes(self):
        # GTK recolours a symbolic icon by forcing `fill` on its paths, which
        # turns a stroked drawing into a blob.
        for name in named_icons():
            with open(os.path.join(ACTIONS, f"{name}.svg"), encoding="utf-8") as handle:
                svg = handle.read()
            with self.subTest(icon=name):
                self.assertNotIn("stroke", svg)
                self.assertRegex(svg, r'<path d="M[^"]+"/>')

    def test_the_generator_and_the_files_agree(self):
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))
        try:
            import make_icons
        finally:
            sys.path.pop(0)
        for name, draw in make_icons.ICONS.items():
            path = os.path.join(ACTIONS, f"{make_icons.PREFIX}{name}-symbolic.svg")
            with self.subTest(icon=name):
                with open(path, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), make_icons.svg(draw()))

    def test_the_app_icon_exists(self):
        self.assertTrue(os.path.isfile(icons.app_icon_path()))
        with open(icons.app_icon_path(), encoding="utf-8") as handle:
            self.assertTrue(re.search(r"<svg[^>]+viewBox", handle.read()))


if __name__ == "__main__":
    unittest.main()
