"""
Saying that the app could not start, where a teacher will see it.

A board has no terminal: a traceback on stderr goes nowhere. The message goes
to a window (`zenity` is on every ETAP board) and the detail to a log file
whose path the window names, so that a photo of the screen is a bug report.
This module must not import GTK, which may be the thing that failed.
"""

import logging
import os
import shutil
import subprocess
import sys

from interaktiv_core import appdirs

LOG_NAME = "interaktiv.log"
MAX_LOG_BYTES = 512 * 1024

TITLE = "Rayyan Ekitap"
HEADLINE = "Rayyan Ekitap başlatılamadı."
ADVICE = "Bu pencerenin fotoğrafını çekip geliştiriciye gönderin."


def log_path() -> str:
    return os.path.join(appdirs.cache_dir(), LOG_NAME)


def start_log() -> str:
    """Send the app's log, and any uncaught error, to the log file."""
    path = log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > MAX_LOG_BYTES:
            os.remove(path)
        logging.basicConfig(
            filename=path, level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    except OSError:
        logging.basicConfig(level=logging.INFO)

    previous = sys.excepthook

    def hook(kind, value, trace):
        logging.getLogger("interaktiv").error(
            "uncaught error", exc_info=(kind, value, trace)
        )
        previous(kind, value, trace)

    sys.excepthook = hook
    return path


def message(reason: str) -> str:
    return f"{HEADLINE}\n\n{reason}\n\n{ADVICE}\n\nAyrıntılar: {log_path()}"


def report(reason: str, detail: str = "") -> None:
    """Log `detail` and show `reason` in a window, falling back to stderr."""
    logging.getLogger("interaktiv").error("%s\n%s", reason, detail)
    text = message(reason)
    print(text, file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)

    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    for command in (
        ["zenity", "--error", "--no-wrap", f"--title={TITLE}", f"--text={text}"],
        ["notify-send", TITLE, text],
    ):
        if shutil.which(command[0]):
            try:
                subprocess.run(command, check=False, timeout=600)
                return
            except (OSError, subprocess.SubprocessError):
                continue
