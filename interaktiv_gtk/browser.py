"""Hand a URL to the board's own browser."""

import shutil
import subprocess
import webbrowser

from gi.repository import Gio

# ETAP boards ship Chrome; the others are what a teacher's laptop may have.
CHROME_COMMANDS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def open_as_app(url: str) -> bool:
    """
    Open `url` in a Chrome window without tabs or an address bar, so an
    activity looks like part of the lesson and not like a web page.
    """
    for command in CHROME_COMMANDS:
        path = shutil.which(command)
        if not path:
            continue
        try:
            subprocess.Popen(
                [path, f"--app={url}", "--start-maximized"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            return True
        except OSError:
            continue
    return False


def open_uri(url: str) -> bool:
    """Open `url` with whatever the desktop uses for it."""
    try:
        if Gio.AppInfo.launch_default_for_uri(url, None):
            return True
    except Exception:
        pass
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False
