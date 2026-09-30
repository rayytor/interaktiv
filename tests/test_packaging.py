"""
The board bundle's scripts: installing into a home folder, choosing which
bundled libraries a system needs, and removing everything again.

The bundle itself is built in a container (`packaging/board/build.sh`); these
tests run the scripts that ship inside it against a small stand-in.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOARD = os.path.join(PROJECT_ROOT, "packaging", "board")

SCRIPTS = [
    "build.sh",
    "container-build.sh",
    "test-floor.sh",
    "test-bundle.sh",
    "bundle/interaktiv",
    "bundle/activate.sh",
    "bundle/install.sh",
    "bundle/kaldir.sh",
    "stick/autorun.sh",
]


class TestScriptsParse(unittest.TestCase):
    def test_every_script_is_valid_posix_shell(self):
        for name in SCRIPTS:
            with self.subTest(script=name):
                result = subprocess.run(
                    ["sh", "-n", os.path.join(BOARD, name)], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_removals_cannot_expand_to_a_system_directory(self):
        """Every `rm -rf` on a variable path is guarded against an empty variable."""
        for name in SCRIPTS:
            with open(os.path.join(BOARD, name), encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if "rm -rf" in line and "$" in line and "{} +" not in line:
                        with self.subTest(script=name, line=number):
                            self.assertIn(":?}", line)


class TestInstallIntoHome(unittest.TestCase):
    """`install.sh`, `activate.sh` and `kaldir.sh` on a stand-in bundle."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="interaktiv-packaging-")
        self.home = os.path.join(self.tmp, "home")
        self.bundle = os.path.join(self.tmp, "stick", "interaktiv")
        os.makedirs(self.home)
        for part in ("app/books", "lib", "typelibs", "python", "share"):
            os.makedirs(os.path.join(self.bundle, part))
        for script in ("interaktiv", "activate.sh", "install.sh", "kaldir.sh"):
            shutil.copy(os.path.join(BOARD, "bundle", script), self.bundle)
        self._write("icon.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")
        self._write("app/marker.txt", "v1")
        # One library no system has, and one every system has.
        self._write("lib/libinteraktiv-test-only.so.1", "")
        self._write("lib/libc.so.6", "")
        self._write("typelibs/InteraktivTestOnly-1.0.typelib", "")
        self._write("typelibs/GLib-2.0.typelib", "")
        self.dest = os.path.join(self.home, ".local", "share", "interaktiv")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, relative, text):
        with open(os.path.join(self.bundle, relative), "w", encoding="utf-8") as handle:
            handle.write(text)

    def _run(self, script):
        env = {"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        return subprocess.run(["sh", script], env=env, capture_output=True, text=True)

    def _install(self):
        result = self._run(os.path.join(self.bundle, "install.sh"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_install_needs_nothing_outside_the_home_folder(self):
        self._install()
        self.assertTrue(os.access(os.path.join(self.dest, "interaktiv"), os.X_OK))
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "app", "marker.txt")))

        desktop = os.path.join(
            self.home, ".local", "share", "applications", "org.interaktiv.School.desktop"
        )
        with open(desktop, encoding="utf-8") as handle:
            entry = handle.read()
        self.assertIn(f"Exec={self.dest}/interaktiv", entry)
        self.assertIn("Comment[tr]=", entry)
        self.assertIn("Icon=org.interaktiv.School", entry)
        self.assertTrue(os.path.isfile(os.path.join(
            self.home, ".local", "share", "icons", "hicolor", "scalable", "apps",
            "org.interaktiv.School.svg",
        )))

    def test_only_libraries_the_system_lacks_are_activated(self):
        self._install()
        active = os.listdir(os.path.join(self.dest, "lib-active"))
        self.assertIn("libinteraktiv-test-only.so.1", active)
        self.assertNotIn("libc.so.6", active)

    def test_only_typelibs_the_system_lacks_are_activated(self):
        self._install()
        active = os.listdir(os.path.join(self.dest, "typelibs-active"))
        self.assertIn("InteraktivTestOnly-1.0.typelib", active)
        if os.path.isfile("/usr/lib/x86_64-linux-gnu/girepository-1.0/GLib-2.0.typelib"):
            self.assertNotIn("GLib-2.0.typelib", active)

    def test_an_update_keeps_downloaded_books(self):
        self._install()
        book = os.path.join(self.dest, "app", "books", "downloaded-on-the-board.pdf")
        with open(book, "w", encoding="utf-8") as handle:
            handle.write("pdf")
        self._write("app/marker.txt", "v2")

        self._install()
        self.assertTrue(os.path.isfile(book))
        with open(os.path.join(self.dest, "app", "marker.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "v2")

    def test_uninstall_leaves_nothing_behind(self):
        self._install()
        os.makedirs(os.path.join(self.home, ".config", "interaktiv"))
        result = self._run(os.path.join(self.dest, "kaldir.sh"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(self.dest))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config", "interaktiv")))
        self.assertFalse(os.path.exists(os.path.join(
            self.home, ".local", "share", "applications", "org.interaktiv.School.desktop"
        )))


if __name__ == "__main__":
    unittest.main()
