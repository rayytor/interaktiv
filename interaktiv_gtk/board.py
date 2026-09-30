"""
What the app does differently on a Pardus ETAP smart board.

ETAP runs a daemon that turns a finger held still for 700 ms into a right
click. The reader uses the same hold for two things of its own -- a button's
name, and paging while an edge is held -- so it asks the daemon to stand down
while its window is the active one. The daemon's switch is a directory: an
entry named after a running process id disables it for the whole desktop.
"""

import os
from typing import Optional

from interaktiv_core import appdirs

ETAP_RUN_DIR = "/run/etap"
RIGHT_CLICK_DISABLE_DIR = os.path.join(ETAP_RUN_DIR, "right-click", "disable")


def is_board() -> bool:
    return os.path.isdir(ETAP_RUN_DIR)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RightClickGuard:
    """Holds ETAP's long-press right click off while `acquire`d."""

    def __init__(self, directory: str = RIGHT_CLICK_DISABLE_DIR,
                 record_path: Optional[str] = None):
        self.directory = directory
        self.path = os.path.join(directory, str(os.getpid()))
        # Where the entry is remembered, so that a run that was killed can be
        # cleaned up after by the next one.
        self.record_path = record_path or os.path.join(
            appdirs.cache_dir(), "right-click-guard"
        )
        self.held = False

    @property
    def available(self) -> bool:
        return os.path.isdir(self.directory) and os.access(self.directory, os.W_OK)

    def clean_stale(self) -> None:
        """Remove the entry a previous run left behind, if it did not exit cleanly."""
        try:
            with open(self.record_path, "r", encoding="utf-8") as handle:
                stale = handle.read().strip()
        except OSError:
            return
        if stale.isdigit() and not _pid_alive(int(stale)):
            self._remove(os.path.join(self.directory, stale))
        self._remove(self.record_path)

    def acquire(self) -> bool:
        if self.held or not self.available:
            return self.held
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                handle.write("interaktiv\n")
            os.makedirs(os.path.dirname(self.record_path), exist_ok=True)
            with open(self.record_path, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
        except OSError:
            self._remove(self.path)
            return False
        self.held = True
        return True

    def release(self) -> None:
        if not self.held:
            return
        self.held = False
        self._remove(self.path)
        self._remove(self.record_path)

    @staticmethod
    def _remove(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass
