"""
Work the reader starts and does not wait for.

These four used to live inside `server.py`, where they were reachable only as
HTTP endpoints. None of them is about HTTP: baking a book that arrived unbaked,
resolving an activity bundle to a file, fetching one that is not on disk yet, and
keeping MuPDF's cache from growing without bound are all things a reader does
whatever its front end is. They are lifted here so the native reader can call
them directly and the web server can keep calling them through its routes.

Everything here is safe to call from a worker thread and returns plain data; none
of it touches a widget.
"""

import ctypes
import gc
import os
import re
import subprocess
import sys
import threading
from typing import Callable, Optional

# The publisher's CDN, where an activity that is not on disk is played from.
CDN_ACTIVITY_URL = "https://ogm-large-cdn.eba.gov.tr/materyal/Uygulama/{guid}/index.html"

_GUID_RE = re.compile(r"[A-Fa-f0-9\-]{36}")

_active_bakes: set = set()
_active_bakes_lock = threading.Lock()


# -- memory ---------------------------------------------------------------

def trim_memory() -> None:
    """
    Hand back what MuPDF and the allocator are holding but not using.

    MuPDF keeps a store of decoded page resources. In PyMuPDF 1.28
    `TOOLS.store_maxsize()` reports None and cannot be set, so the store cannot
    be capped -- shrinking it on idle is the only lever there is. Call this
    every so often on the render thread, and whenever its queue empties.
    """
    try:
        import pymupdf

        pymupdf.TOOLS.store_shrink(100)
    except Exception:
        pass
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        # Not glibc, or no malloc_trim: the gc pass above is still worth having.
        pass


# -- baking ---------------------------------------------------------------

def trigger_bake(
    manager,
    book_id: str,
    on_done: Optional[Callable[[str, bool], None]] = None,
) -> bool:
    """
    Bake a book that has no regions yet, in a short-lived child process.

    The child is `scan.py`, one book at a time, at the default heap. That is not
    an implementation detail to be optimised away: the detector holds a lot of
    page structure, and a sweep over many books inside one process will take the
    machine down with it.

    Returns True if a bake was started. `on_done(book_id, ok)` is called on the
    worker thread when the child exits.
    """
    with _active_bakes_lock:
        if book_id in _active_bakes:
            return False
        local_pdf = manager.get_local_path(book_id)
        if not local_pdf or not os.path.isfile(local_pdf):
            return False
        scan_script = os.path.join(
            manager.base_dir, "tools", "hotspot_extraction", "scan.py"
        )
        if not os.path.isfile(scan_script):
            return False
        _active_bakes.add(book_id)

    def _run() -> None:
        ok = False
        try:
            result = subprocess.run(
                [sys.executable, scan_script, "--only", book_id,
                 "--pdf", local_pdf, "--quiet"],
                cwd=manager.base_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            ok = result.returncode == 0
        except Exception:
            ok = False
        finally:
            with _active_bakes_lock:
                _active_bakes.discard(book_id)
            if on_done is not None:
                on_done(book_id, ok)

    threading.Thread(target=_run, daemon=True).start()
    return True


def bake_in_progress(book_id: str) -> bool:
    with _active_bakes_lock:
        return book_id in _active_bakes


def needs_bake(manager, book_id: str, regions_book) -> bool:
    """
    Whether this book's bake is missing or describes a different file.

    The fingerprint is the same freshness rule the baker uses, so a book that
    was re-downloaded at a new edition is re-baked rather than drawn with rects
    that no longer fit it.
    """
    if regions_book is None:
        return True
    local_pdf = manager.get_local_path(book_id)
    if not local_pdf or not os.path.isfile(local_pdf):
        return False
    if not regions_book.fingerprint:
        return False
    try:
        from tools.hotspot_extraction.scanner.serializer import compute_fingerprint

        return compute_fingerprint(local_pdf) != regions_book.fingerprint
    except Exception:
        # Without the scanner installed we cannot tell; trust what we have.
        return False


# -- activities -----------------------------------------------------------

def activity_asset_path(manager, guid: str, rest: str = "index.html") -> Optional[str]:
    """
    Map an activity GUID and a path inside its bundle to a real file.

    The directory is the manager's answer -- JIT cache first, then a bundle that
    shipped the activity -- and `rest` is confined to it, so a `..` cannot climb
    out of an activity and read the rest of the disk.
    """
    if not _GUID_RE.fullmatch(guid or ""):
        return None
    root = manager.activity_dir(guid) if manager else None
    if not root:
        return None
    root = os.path.abspath(root)
    filepath = os.path.abspath(os.path.join(root, rest or "index.html"))
    if not filepath.startswith(root + os.sep):
        return None
    if not os.path.isfile(filepath):
        return None
    return filepath


def activity_index_url(manager, guid: str) -> Optional[str]:
    """
    Where to point a web view at this activity: the local copy if there is one,
    the publisher's CDN otherwise. None if the GUID is not a GUID.
    """
    if not _GUID_RE.fullmatch(guid or ""):
        return None
    local = activity_asset_path(manager, guid, "index.html")
    if local:
        return "file://" + local
    return CDN_ACTIVITY_URL.format(guid=guid)


def fetch_activity(
    manager,
    guid: str,
    only_html: bool = True,
    on_done: Optional[Callable[[str, bool], None]] = None,
) -> bool:
    """
    Cache an activity bundle locally, so opening it a second time works offline.

    The web reader has always asked for this and never got it -- it calls an
    endpoint `server.py` does not define -- so this is the first implementation
    rather than a port. `only_html` keeps the fetch small: the markup is stored
    and its media references are rewritten to the CDN, which is what makes a
    first open cheap and a second one instant.

    Returns True if a fetch was started.
    """
    if not _GUID_RE.fullmatch(guid or ""):
        return False
    if activity_asset_path(manager, guid, "index.html"):
        return False  # already cached

    def _run() -> None:
        ok = False
        try:
            from tools.content_extraction import extract_activities

            extract_activities.set_paths(
                activities_dir=manager.activities_dir, meta_dir=manager.meta_dir
            )
            dest = os.path.join(manager.activities_dir, guid)
            extract_activities.extract_activity(
                guid, dest_dir=dest, only_html=only_html, verbose=False
            )
            ok = os.path.isfile(os.path.join(dest, "index.html"))
        except Exception:
            ok = False
        finally:
            if on_done is not None:
                on_done(guid, ok)

    threading.Thread(target=_run, daemon=True).start()
    return True


# -- thumbnails -----------------------------------------------------------

def ensure_thumbnail(manager, book_id: str) -> Optional[str]:
    """
    This book's cover, generating it from page 1 if it is missing.

    Generation shells out to `pdftoppm`, which is deliberate: it keeps cover
    rendering out of the process that owns the reader's MuPDF context.
    """
    path = manager.get_thumbnail_path(book_id)
    if path and os.path.isfile(path):
        return path
    local_pdf = manager.get_local_path(book_id)
    if not local_pdf or not os.path.isfile(local_pdf):
        return None
    try:
        manager._generate_thumbnail_from_local(book_id, local_pdf)
    except Exception:
        return None
    path = manager.get_thumbnail_path(book_id)
    return path if path and os.path.isfile(path) else None


# -- preview cache --------------------------------------------------------

# A preview is a whole book, so the cache holds few and forgets quickly.
PREVIEW_MAX_FILES = 2
PREVIEW_MAX_BYTES = 1024 ** 3


def preview_path(book_id: str) -> str:
    """Where a book downloaded for preview is kept."""
    from . import appdirs

    return os.path.join(appdirs.preview_cache_dir(), f"{book_id}.pdf")


def cached_preview(book_id: str) -> Optional[str]:
    """The cached preview of a book, if one is on disk and complete."""
    path = preview_path(book_id)
    return path if os.path.isfile(path) else None


def prune_previews(keep: Optional[str] = None) -> None:
    """
    Keep the preview cache to a couple of books.

    Called after a preview finishes downloading. `keep` is the file just
    written, which is never evicted however large it is -- the reader has it
    open.
    """
    from . import appdirs

    directory = appdirs.preview_cache_dir()
    if not os.path.isdir(directory):
        return
    entries = []
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if not name.endswith(".pdf") or not os.path.isfile(path):
            continue
        if keep and os.path.abspath(path) == os.path.abspath(keep):
            continue
        try:
            entries.append((os.path.getatime(path), os.path.getsize(path), path))
        except OSError:
            continue
    entries.sort(reverse=True)  # most recently touched first

    budget_files = PREVIEW_MAX_FILES - (1 if keep else 0)
    budget_bytes = PREVIEW_MAX_BYTES
    if keep and os.path.isfile(keep):
        try:
            budget_bytes -= os.path.getsize(keep)
        except OSError:
            pass

    for _atime, size, path in entries:
        if budget_files > 0 and size <= budget_bytes:
            budget_files -= 1
            budget_bytes -= size
            continue
        try:
            os.remove(path)
        except OSError:
            pass


def probe_download_size(url: str, timeout: float = 10.0) -> Optional[int]:
    """
    How many bytes a book is, without fetching any of them.

    Asked before a preview, so the teacher is told what a preview costs before
    agreeing to it rather than watching a progress bar discover it. Returns
    None when the server will not say; the caller should carry on regardless,
    since the transfer reports the real total as soon as it starts.
    """
    import urllib.request

    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Interaktiv/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            size = int(response.headers.get("Content-Length", 0))
    except Exception:
        return None
    return size or None
