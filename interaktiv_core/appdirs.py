"""XDG locations the native reader writes to."""

import os

APP_NAME = "interaktiv"


def _xdg(var: str, default: str) -> str:
    base = os.environ.get(var) or os.path.join(os.path.expanduser("~"), default)
    return os.path.join(base, APP_NAME)


def config_dir() -> str:
    return _xdg("XDG_CONFIG_HOME", ".config")


def cache_dir() -> str:
    return _xdg("XDG_CACHE_HOME", ".cache")


def state_path() -> str:
    """Where the window remembers its themes, view mode and last pages."""
    return os.path.join(config_dir(), "state.json")


def preview_cache_dir() -> str:
    """
    Where a book being previewed before install is downloaded to.

    A preview is a whole file: MuPDF cannot parse a partially-ranged PDF, and
    `pymupdf` cannot read a URL, so the web reader's byte-range proxy has no
    native equivalent. The download is cached here so that installing a book
    that was previewed is a rename rather than a second download.
    """
    return os.path.join(cache_dir(), "previews")


def ensure(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path
