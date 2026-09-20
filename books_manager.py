#!/usr/bin/env python3
"""
BooksManager: Manages the textbook catalog from kitap_pdf_linkleri.txt,
handles local installations, background downloads, thumbnail management,
and RFC 7233 byte-range streaming proxy for remote previews.
Zero external dependencies (Python standard library only).

Two editions are served from this one class:

  * "full"   -- the authoring/reader app. The catalogue comes from the
               publisher's link file, books are downloaded on demand, and a
               book may be previewed straight off the CDN.
  * "school" -- a packaged library. The catalogue comes from
               `library/manifest.json`, which was written once by
               `converter/build_library.py`; every book is already on disk, no
               downloader or network catalogue lookup is ever used, and the
               JIT cache for interactive activities lives outside the bundle
               so a read-only (USB / network share) library still works.
"""

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request

# Where a packaged library keeps its per-book bundles.
LIBRARY_MANIFEST = "manifest.json"


def default_activities_cache_dir():
    """
    A writable home for JIT-fetched interactive activities.

    Never inside the library: a school library is expected to be copied to a
    stick or a share that may be read-only, and the cache is disposable.
    """
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return os.path.join(base, "interaktiv", "activities")


class BooksManager:
    def __init__(self, base_dir=None, library_dir=None, activities_cache_dir=None, edition=None):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.books_file = os.path.join(self.base_dir, "kitap_pdf_linkleri.txt")
        self.books_dir = os.path.join(self.base_dir, "books")
        self.thumbs_dir = os.path.join(self.base_dir, "thumbnails")
        self.pdf_parts_dir = os.path.join(self.base_dir, "pdf_parts")
        self.meta_dir = os.path.join(self.base_dir, "activities_meta")

        # --- Edition / packaged-library detection -----------------------------
        candidate = library_dir
        if candidate and os.path.isdir(candidate):
            self.library_dir = os.path.abspath(candidate)
            self.manifest_path = os.path.join(self.library_dir, LIBRARY_MANIFEST)
        elif not library_dir and os.path.isdir(os.path.join(self.base_dir, "library")):
            self.library_dir = os.path.join(self.base_dir, "library")
            self.manifest_path = os.path.join(self.library_dir, LIBRARY_MANIFEST)
        else:
            self.library_dir = None
            self.manifest_path = None

        # Packaged library mode is active ONLY when an explicit library_dir is provided
        # or a non-empty manifest exists in library/, and edition != "full".
        manifest_data = self.read_library_manifest() if (self.manifest_path and os.path.isfile(self.manifest_path)) else {}
        has_packaged_books = bool(manifest_data.get("books"))
        self.library_mode = bool(
            self.manifest_path
            and os.path.isfile(self.manifest_path)
            and has_packaged_books
            and edition != "full"
        )
        self.edition = edition or ("school" if self.library_mode else "full")

        # Where JIT-fetched interactive activities are cached. In the full
        # edition this is the in-tree `activities/` (which also carries the
        # bakes); in the school edition or library mode it is a writable cache
        # outside the bundle.
        if self.edition == "school" or self.library_mode:
            self.activities_dir = os.path.abspath(
                activities_cache_dir or default_activities_cache_dir()
            )
            self.activities_source_dir = self.activities_dir
        else:
            self.activities_dir = os.path.join(self.base_dir, "activities")
            self.activities_source_dir = self.activities_dir

        if not self.library_mode:
            os.makedirs(self.books_dir, exist_ok=True)
            os.makedirs(self.thumbs_dir, exist_ok=True)

        self.books = []
        self.books_by_id = {}
        self.downloads = {}  # id -> { status, progress, downloaded_bytes, total_bytes, thread, cancel_event }
        self.downloads_lock = threading.Lock()
        # A baked regions.json is a few hundred KB and the dashboard asks for
        # every book's confidence on each catalogue call, so it is read once.
        self._confidence_cache = {}
        # guid -> directory inside a bundle, for the activities a packager chose
        # to ship with a book (`--with-activities`). Empty in a bundle that
        # ships none, which is the default.
        self._bundled_activities = {}

        # Known local mappings (friendly/legacy names in books)
        self.legacy_local_map = {
            "0e966773-5012-4f57-8be5-d892e8c75f22": os.path.join(self.books_dir, "full_pdf.pdf"),
            "51cdbbce-66f4-4baa-95d7-634bf11b7e44": os.path.join(self.books_dir, "matematik.pdf"),
        }

        self.load_catalog()

    def load_catalogue_index(self):
        """
        The publisher's own index of every book it lists, written by
        `extract_activities.sync_catalogue()`. It carries the real grade and
        subject for each book, which the link file does not.

        Only the full edition has one: a packaged library carries the same
        fields inside each bundle's own manifest entry.
        """
        if self.library_mode:
            return {}
        path = os.path.join(self.base_dir, "activities_meta", "_catalogue.json")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("books") or {}
        except (OSError, ValueError):
            return {}

    def load_catalog(self):
        """Parse kitap_pdf_linkleri.txt into structured books list grouped by grade."""
        self.books = []
        self.books_by_id = {}

        if self.library_mode:
            self._load_library_catalog()
            return

        if not os.path.exists(self.books_file):
            return

        catalogue = self.load_catalogue_index()

        with open(self.books_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        for idx, line in enumerate(lines, 1):
            if ": https://" not in line:
                continue
            title, url = line.split(": https://", 1)
            title = title.strip()
            url = "https://" + url.strip()

            m = re.search(r"Etkilesimlikitap/([a-f0-9\-]+)/pdf\.pdf", url)
            if not m:
                continue
            book_id = m.group(1)

            # Determine grade based on index ranges
            if 1 <= idx <= 10:
                grade = 9
                grade_label = "9. Sınıf"
            elif 11 <= idx <= 20:
                grade = 10
                grade_label = "10. Sınıf"
            elif 21 <= idx <= 31:
                grade = 11
                grade_label = "11. Sınıf"
            elif 32 <= idx <= 45:
                grade = 12
                grade_label = "12. Sınıf"
            else:
                grade = 0
                grade_label = "Seçmeli Dersler"

            # The line-index ranges above only reach line 45 while the file has
            # 56 entries, so they are a fallback: the publisher's own catalogue
            # says which grade and subject a book belongs to.
            entry = catalogue.get(book_id) or {}
            sinif = (entry.get("sinif_ad") or "").strip()
            if sinif:
                m_grade = re.match(r"^(\d+)\.\s*S\u0131n\u0131f", sinif)
                if not m_grade:
                    m_grade = re.search(r"(\d+)\s*$", sinif)  # "Hazırlık İngilizce 9"
                grade = int(m_grade.group(1)) if m_grade else 0
                if grade not in (9, 10, 11, 12):
                    grade = 0
                grade_label = sinif

            book = {
                "id": book_id,
                "title": title,
                "grade": grade,
                "gradeLabel": grade_label,
                "subject": (entry.get("ders_ad") or "").strip() or None,
                "pageCount": entry.get("sayfasayisi"),
                "interactiveCount": entry.get("interactive_count"),
                "url": url,
                "index": idx,
            }
            self.books.append(book)
            self.books_by_id[book_id] = book

        # Check existing local thumbnails for legacy books
        self.ensure_legacy_thumbnails()

    # --- Packaged library ----------------------------------------------------

    def read_library_manifest(self):
        """The packaged library's index, or an empty one when there is none."""
        if not os.path.isfile(self.manifest_path):
            return {"schema": 1, "edition": "school", "books": []}
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {"schema": 1, "edition": "school", "books": []}
        if not isinstance(data.get("books"), list):
            data["books"] = []
        return data

    def _load_library_catalog(self):
        """
        Build the catalogue from `library/manifest.json`.

        Every entry is by definition installed, so the fields the dashboard
        reads (`isInstalled`, `fileSize`, `hasThumbnail`) are answered from the
        bundle rather than probed per request. `url` stays for shape
        compatibility with the full edition but is never fetched.
        """
        manifest = self.read_library_manifest()
        for idx, entry in enumerate(manifest.get("books") or [], 1):
            book_id = entry.get("id")
            if not book_id:
                continue
            book = {
                "id": book_id,
                "title": entry.get("title") or book_id,
                "grade": entry.get("grade", 0),
                "gradeLabel": entry.get("gradeLabel") or "Kitaplık",
                "subject": entry.get("subject"),
                "pageCount": entry.get("pageCount"),
                "interactiveCount": entry.get("interactiveCount"),
                "url": None,
                "index": idx,
                # Packaged extras, not part of the full edition's shape.
                "bundle": entry.get("bundle") or book_id,
                "fingerprint": entry.get("fingerprint"),
                "builtAt": entry.get("builtAt"),
                "hasInteractive": bool(entry.get("hasInteractive")),
            }
            self.books.append(book)
            self.books_by_id[book_id] = book
            self._register_bundled_activities(book_id)

    def _register_bundled_activities(self, book_id):
        """
        Note the activities that were shipped inside a book's bundle, if any.

        The default bundle carries none -- interactive material is JIT-loaded --
        but a packager may have copied some in, and those must be found before
        the cache is consulted or they would be silently ignored.
        """
        meta_path = self.get_book_meta_path(book_id)
        if not os.path.isfile(meta_path):
            return
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        for guid in data.get("activities") or []:
            if not guid:
                continue
            path = os.path.join(os.path.dirname(meta_path), "activities", str(guid))
            if os.path.isfile(os.path.join(path, "index.html")):
                self._bundled_activities[str(guid)] = path

    def activity_dir(self, guid):
        """
        Where one interactive activity's files are.

        The JIT cache comes first -- a fetch of a newer revision should win --
        then a bundle that shipped the activity with the book. When neither has
        it, the cache path is returned so a caller can tell \"not here yet\" from
        \"not anywhere\" by looking at the directory.
        """
        if not guid:
            return None
        cached = os.path.join(self.activities_dir, guid)
        if os.path.isfile(os.path.join(cached, "index.html")):
            return cached
        bundled = self._bundled_activities.get(guid)
        if bundled:
            return bundled
        in_tree = os.path.join(self.base_dir, "activities", str(guid))
        if os.path.isfile(os.path.join(in_tree, "index.html")):
            return in_tree
        return cached

    def get_bundle_dir(self, book_id):
        """Directory holding one packaged book, or None in the full edition."""
        if not self.library_mode or not self.library_dir:
            return None
        book = self.books_by_id.get(book_id) or {}
        bundle = book.get("bundle") or book_id
        # A manifest is data, not a path: keep the bundle inside the library.
        safe = os.path.basename(bundle)
        candidate = os.path.join(self.library_dir, "books", safe)
        if os.path.isdir(candidate):
            return candidate
        direct = os.path.join(self.library_dir, "books", str(book_id))
        if os.path.isdir(direct):
            return direct
        return candidate

    def get_regions_path(self, book_id):
        """
        Where a book's baked activity regions live.

        The full edition bakes into `activities/books/<id>/regions.json`; a
        packaged library carries the same file inside the bundle, so the reader
        never has to detect anything at load time.
        """
        if self.library_mode:
            bundle = self.get_bundle_dir(book_id)
            if bundle:
                candidate = os.path.join(bundle, "regions.json")
                if os.path.isfile(candidate):
                    return candidate
        return os.path.join(self.base_dir, "activities", "books", book_id, "regions.json")

    def get_local_path(self, book_id):
        """Return absolute path to local PDF if installed, else None."""
        if self.library_mode:
            bundle = self.get_bundle_dir(book_id)
            if bundle:
                candidate = os.path.join(bundle, "book.pdf")
                if os.path.isfile(candidate):
                    return candidate
            return None
        local_path = os.path.join(self.books_dir, f"{book_id}.pdf")
        if os.path.isfile(local_path):
            return local_path
        # Check legacy map
        if book_id in self.legacy_local_map:
            legacy_path = self.legacy_local_map[book_id]
            if os.path.isfile(legacy_path):
                return legacy_path
        # Fallback to pdf_parts if present
        fallback_pdf_parts = {
            "0e966773-5012-4f57-8be5-d892e8c75f22": os.path.join(self.pdf_parts_dir, "full_pdf.pdf"),
            "51cdbbce-66f4-4baa-95d7-634bf11b7e44": os.path.join(self.pdf_parts_dir, "matematik.pdf"),
        }
        if book_id in fallback_pdf_parts and os.path.isfile(fallback_pdf_parts[book_id]):
            return fallback_pdf_parts[book_id]
        return None

    def is_installed(self, book_id):
        return self.get_local_path(book_id) is not None

    def book_id_for_path(self, path):
        """
        Which catalogue book a PDF on disk is, or None.

        Asked so the reader can hang a book's interactive activities on the file
        it was opened with -- by path, from the command line, through a symlink
        such as books/full_pdf.pdf -- rather than guessing from its name. The
        comparison is on the resolved path, so every alias of one book answers
        the same.
        """
        if not path:
            return None
        try:
            target = os.path.realpath(
                path if os.path.isabs(path) else os.path.join(self.base_dir, path)
            )
        except OSError:
            return None
        if not os.path.isfile(target):
            return None
        for book_id in list(self.books_by_id) + list(self.legacy_local_map):
            local = self.get_local_path(book_id)
            if local and os.path.realpath(local) == target:
                return book_id
        return None

    def get_file_size(self, book_id):
        path = self.get_local_path(book_id)
        if path and os.path.isfile(path):
            return os.path.getsize(path)
        return None

    def has_thumbnail(self, book_id):
        return self.get_thumbnail_path(book_id) is not None

    def get_thumbnail_path(self, book_id):
        if self.library_mode:
            bundle = self.get_bundle_dir(book_id)
            if bundle:
                for name in ("thumbnail.jpg", "thumb.jpg"):
                    candidate = os.path.join(bundle, name)
                    if os.path.isfile(candidate):
                        return candidate
            return None
        thumb_path = os.path.join(self.thumbs_dir, f"{book_id}.jpg")
        if os.path.isfile(thumb_path):
            return thumb_path
        return None

    def ensure_legacy_thumbnails(self):
        """Generate first-page thumbnails for existing local PDFs if missing."""
        for book_id in self.legacy_local_map:
            pdf_path = self.get_local_path(book_id)
            if pdf_path and os.path.isfile(pdf_path):
                thumb_path = os.path.join(self.thumbs_dir, f"{book_id}.jpg")
                if not os.path.isfile(thumb_path):
                    self._generate_thumbnail_from_local(book_id, pdf_path)

    def get_book_confidence(self, book_id):
        if book_id in self._confidence_cache:
            return self._confidence_cache[book_id]
        confidence = None
        regions_path = self.get_regions_path(book_id)
        if os.path.isfile(regions_path):
            try:
                with open(regions_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    confidence = (data.get("calibration") or {}).get("confidence")
            except Exception:
                confidence = None
        self._confidence_cache[book_id] = confidence
        return confidence

    def get_all_books(self):
        """Return full catalog with installation and download statuses."""
        with self.downloads_lock:
            active_downloads = {
                bid: {
                    "status": info["status"],
                    "progress": info["progress"],
                    "downloaded_bytes": info["downloaded_bytes"],
                    "total_bytes": info["total_bytes"],
                }
                for bid, info in self.downloads.items()
            }

        result = []
        for b in self.books:
            bid = b["id"]
            local_path = self.get_local_path(bid)
            installed = local_path is not None
            size = os.path.getsize(local_path) if installed else None
            download_info = active_downloads.get(bid)

            item = {
                "id": bid,
                "title": b["title"],
                "grade": b["grade"],
                "gradeLabel": b["gradeLabel"],
                "subject": b.get("subject"),
                "pageCount": b.get("pageCount"),
                "interactiveCount": b.get("interactiveCount"),
                "url": b["url"],
                "index": b["index"],
                "isInstalled": installed,
                "fileSize": size,
                "confidence": self.get_book_confidence(bid),
                "hasThumbnail": self.has_thumbnail(bid),
                "download": download_info,
            }
            if self.library_mode:
                item["hasInteractive"] = bool(b.get("hasInteractive"))
                item["fingerprint"] = b.get("fingerprint")
            result.append(item)
        return result

    def get_download_statuses(self):
        with self.downloads_lock:
            return {
                bid: {
                    "status": info["status"],
                    "progress": info["progress"],
                    "downloaded_bytes": info["downloaded_bytes"],
                    "total_bytes": info["total_bytes"],
                }
                for bid, info in self.downloads.items()
            }

    def start_download(self, book_id):
        """Start downloading a book in a background thread."""
        if self.library_mode:
            return False, "This edition reads a packaged library and does not download"
        if book_id not in self.books_by_id:
            return False, "Book not found"
        if self.is_installed(book_id):
            return True, "Already installed"

        with self.downloads_lock:
            if book_id in self.downloads and self.downloads[book_id]["status"] == "downloading":
                return True, "Download already in progress"

            cancel_event = threading.Event()
            self.downloads[book_id] = {
                "status": "downloading",
                "progress": 0,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "cancel_event": cancel_event,
            }

        thread = threading.Thread(target=self._download_worker, args=(book_id, cancel_event), daemon=True)
        thread.start()
        return True, "Download started"

    def _download_worker(self, book_id, cancel_event):
        book = self.books_by_id.get(book_id)
        if not book:
            return

        target_file = os.path.join(self.books_dir, f"{book_id}.pdf")
        part_file = os.path.join(self.books_dir, f"{book_id}.pdf.part")

        try:
            req = urllib.request.Request(
                book["url"],
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Interaktiv/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                total_bytes = int(resp.headers.get("Content-Length", 0))
                with self.downloads_lock:
                    if book_id in self.downloads:
                        self.downloads[book_id]["total_bytes"] = total_bytes

                downloaded = 0
                chunk_size = 128 * 1024

                with open(part_file, "wb") as f:
                    while not cancel_event.is_set():
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        progress = round((downloaded / total_bytes) * 100, 1) if total_bytes > 0 else 0

                        with self.downloads_lock:
                            if book_id in self.downloads:
                                self.downloads[book_id]["downloaded_bytes"] = downloaded
                                self.downloads[book_id]["progress"] = progress

            if cancel_event.is_set():
                if os.path.isfile(part_file):
                    os.remove(part_file)
                with self.downloads_lock:
                    self.downloads.pop(book_id, None)
                return

            os.replace(part_file, target_file)

            # Generate thumbnail if pdftoppm available
            self._generate_thumbnail_from_local(book_id, target_file)

            with self.downloads_lock:
                if book_id in self.downloads:
                    self.downloads[book_id]["status"] = "completed"
                    self.downloads[book_id]["progress"] = 100
                    self.downloads[book_id]["downloaded_bytes"] = downloaded

            # Clean up status after a short delay
            def _clean():
                time.sleep(3)
                with self.downloads_lock:
                    self.downloads.pop(book_id, None)

            threading.Thread(target=_clean, daemon=True).start()

        except Exception as e:
            if os.path.isfile(part_file):
                try:
                    os.remove(part_file)
                except Exception:
                    pass
            with self.downloads_lock:
                if book_id in self.downloads:
                    self.downloads[book_id]["status"] = "error"
                    self.downloads[book_id]["error"] = str(e)

    def cancel_download(self, book_id):
        with self.downloads_lock:
            info = self.downloads.get(book_id)
            if info and "cancel_event" in info:
                info["cancel_event"].set()
                return True, "Download canceled"
        return False, "No active download"

    def uninstall_book(self, book_id):
        """Remove locally installed PDF."""
        if self.library_mode:
            # A packaged book is the library, not a download: deleting it would
            # take the region bake with it and there is no catalogue to re-fetch
            # it from.
            return False, "Books in a packaged library cannot be uninstalled"
        target_file = os.path.join(self.books_dir, f"{book_id}.pdf")
        if os.path.isfile(target_file):
            try:
                os.remove(target_file)
            except Exception as e:
                return False, f"Failed to delete file: {e}"
        return True, "Book uninstalled"

    def save_thumbnail(self, book_id, image_data):
        """Save thumbnail image bytes for a book."""
        if self.library_mode:
            bundle = self.get_bundle_dir(book_id)
            if not bundle:
                return False
            target = os.path.join(bundle, "thumbnail.jpg")
        else:
            os.makedirs(self.thumbs_dir, exist_ok=True)
            target = os.path.join(self.thumbs_dir, f"{book_id}.jpg")
        try:
            with open(target, "wb") as f:
                f.write(image_data)
        except OSError:
            # A packaged library may be a read-only share or a USB stick. The
            # thumbnail is a nicety; refusing to write one is not an error.
            return False
        return True

    def _generate_thumbnail_from_local(self, book_id, pdf_path):
        thumb_path = os.path.join(self.thumbs_dir, f"{book_id}.jpg")
        if not os.path.isfile(thumb_path):
            try:
                out_prefix = os.path.join(self.thumbs_dir, f"tmp_{book_id}")
                subprocess.run(
                    ["pdftoppm", "-jpeg", "-f", "1", "-l", "1", "-scale-to-y", "360", pdf_path, out_prefix],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
                for candidate in [f"{out_prefix}-001.jpg", f"{out_prefix}-1.jpg"]:
                    if os.path.isfile(candidate):
                        try:
                            from PIL import Image
                            im = Image.open(candidate)
                            w, h = im.size
                            if w > h * 1.05:
                                fw = int(min(w * 0.485, h * 0.703))
                                im = im.crop((w - fw, 0, w, h))
                            im.save(thumb_path, "JPEG", quality=90)
                            os.remove(candidate)
                        except Exception:
                            os.replace(candidate, thumb_path)
                        break
            except Exception:
                pass

    def get_book_meta_path(self, book_id):
        if self.library_mode:
            bundle = self.get_bundle_dir(book_id)
            if bundle:
                candidate = os.path.join(bundle, "book.json")
                if os.path.isfile(candidate):
                    return candidate
            return os.path.join(self.library_dir, "books", book_id, "book.json")
        return os.path.join(self.base_dir, "activities_meta", f"{book_id}.json")

    def get_book_oges(self, book_id):
        """
        The publisher's activity entries for one book, or [] if it has none.
        Reads from local activities_meta/<book_id>.json or library bundle.
        """
        meta_path = self.get_book_meta_path(book_id)
        if not os.path.isfile(meta_path):
            if self.library_mode:
                return []
            catalogue = self.load_catalogue_index()
            if book_id not in catalogue and book_id not in self.books_by_id:
                raise LookupError(f"book {book_id} is not listed in the publisher's catalogue")
            return []
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("kitapogeList", [])
        except (OSError, ValueError):
            return []

    def is_activity_installed(self, guid):
        if not guid:
            return False
        return os.path.isfile(os.path.join(self.activity_dir(guid), "index.html"))



if __name__ == "__main__":
    bm = BooksManager()
    print(f"Loaded {len(bm.books)} books.")
    installed = [b for b in bm.get_all_books() if b["isInstalled"]]
    print(f"Installed books ({len(installed)}):")
    for b in installed:
        print(f" - {b['title']} ({b['gradeLabel']}): {b['fileSize']} bytes")
