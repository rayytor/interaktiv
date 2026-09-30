"""ETAP board integration and the start-up failure message."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from interaktiv_gtk import board, failure  # noqa: E402


class TestRightClickGuard(unittest.TestCase):
    """The entry in `/run/etap/right-click/disable` that holds the daemon off."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.disable_dir = os.path.join(self.tmp.name, "disable")
        os.makedirs(self.disable_dir)
        self.record = os.path.join(self.tmp.name, "cache", "guard")

    def tearDown(self):
        self.tmp.cleanup()

    def guard(self):
        return board.RightClickGuard(directory=self.disable_dir, record_path=self.record)

    def test_acquire_creates_an_entry_named_after_this_process(self):
        guard = self.guard()
        self.assertTrue(guard.acquire())
        self.assertEqual(os.listdir(self.disable_dir), [str(os.getpid())])

    def test_release_removes_the_entry_and_its_record(self):
        guard = self.guard()
        guard.acquire()
        guard.release()
        self.assertEqual(os.listdir(self.disable_dir), [])
        self.assertFalse(os.path.exists(self.record))

    def test_not_a_board_means_nothing_is_written(self):
        guard = board.RightClickGuard(
            directory=os.path.join(self.tmp.name, "missing"), record_path=self.record
        )
        self.assertFalse(guard.acquire())
        self.assertFalse(os.path.exists(self.record))
        guard.release()

    def test_the_entry_of_a_killed_run_is_cleaned_up_by_the_next(self):
        dead_pid = "999999"
        with open(os.path.join(self.disable_dir, dead_pid), "w") as handle:
            handle.write("interaktiv\n")
        os.makedirs(os.path.dirname(self.record))
        with open(self.record, "w") as handle:
            handle.write(dead_pid)

        with patch("interaktiv_gtk.board._pid_alive", return_value=False):
            self.guard().clean_stale()
        self.assertEqual(os.listdir(self.disable_dir), [])
        self.assertFalse(os.path.exists(self.record))

    def test_the_entry_of_a_running_process_is_left_alone(self):
        other = "4242"
        with open(os.path.join(self.disable_dir, other), "w") as handle:
            handle.write("another app\n")
        os.makedirs(os.path.dirname(self.record))
        with open(self.record, "w") as handle:
            handle.write(other)

        with patch("interaktiv_gtk.board._pid_alive", return_value=True):
            self.guard().clean_stale()
        self.assertEqual(os.listdir(self.disable_dir), [other])


class TestFailureMessage(unittest.TestCase):
    def test_the_message_is_turkish_and_names_the_log(self):
        text = failure.message("GTK 4 ve libadwaita bulunamadı.")
        self.assertIn("Interaktiv başlatılamadı.", text)
        self.assertIn("GTK 4 ve libadwaita bulunamadı.", text)
        self.assertIn("fotoğrafını", text)
        self.assertIn(failure.log_path(), text)

    def test_without_a_display_nothing_is_launched(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("interaktiv_gtk.failure.subprocess.run") as run:
            failure.report("neden", "ayrıntı")
            run.assert_not_called()

    def test_zenity_is_used_when_there_is_a_display(self):
        with patch.dict(os.environ, {"DISPLAY": ":0"}), \
             patch("interaktiv_gtk.failure.shutil.which",
                   side_effect=lambda name: "/usr/bin/zenity" if name == "zenity" else None), \
             patch("interaktiv_gtk.failure.subprocess.run") as run:
            failure.report("neden")
            command = run.call_args[0][0]
            self.assertEqual(command[0], "zenity")
            self.assertIn("--error", command)


if __name__ == "__main__":
    unittest.main()
