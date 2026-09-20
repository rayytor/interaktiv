#!/usr/bin/env python3
"""
Package one textbook into a self-contained library bundle.

A bundle is what a school install actually is: the PDF, the activity regions
baked once here so the reader never scans a page, the publisher's activity
pointers (so a hotspot can still just-in-time load its interactive version),
and a thumbnail. Everything the reader needs about a book is inside the book's
own directory, which is why a library can be copied to a stick and opened.

    library/
      manifest.json
      books/<book-id>/
        book.pdf        the textbook, streamed by byte range as before
        regions.json    the bake: every activity, question and rectangle
        book.json       catalogue metadata + activity pointers
        thumbnail.jpg

The bake is *not* reimplemented here. `converter/bake_activities.mjs` runs the
reader's own `js/activities.js` headlessly over the whole book; this script
arranges the files it produces into a bundle and records what was built.

Usage:
  python3 converter/package_book.py <book-id | path/to/<book-id>.pdf> [options]

Options:
  --out DIR           library directory to write into (default: ./library)
  --force             re-bake even if a current bake exists
  --with-activities   also copy the book's interactive activities into the
                      bundle (off by default: they are fetched on demand)
  --no-thumbnail      do not generate or copy a cover
  --quiet             only print warnings and errors
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The shape of a baked regions file this packager understands. Kept in step by
# hand with BAKE_VERSION in `tools/hotspot_extraction/bake_activities.mjs` and
# `js/viewer.js` -- a bundle carrying a different number is refused rather than
# shipped and silently ignored by the reader.
BAKE_VERSION = 1


def log(msg, quiet=False):
    if not quiet:
        print(msg)


def fingerprint(file):
    """
    What identifies the bytes a bundle was made from.

    Size plus a hash of the head, middle and tail: identical in intent to the
    baker's, because the two are compared, and a full hash of a 140 MB PDF
    costs about a second per book to prove nothing extra.
    """
    size = os.path.getsize(file)
    span = 256 * 1024
    h = hashlib.sha256()
    with open(file, "rb") as f:
        for at in (0, max(0, (size >> 1) - (span >> 1)), max(0, size - span)):
            f.seek(at)
            h.update(f.read(min(span, size)))
    return f"{size}-{h.hexdigest()[:32]}"


def book_id_for_path(path):
    base = os.path.basename(os.path.realpath(path))
    stem = os.path.splitext(base)[0]
    parts = stem.split("-")
    if len(parts) == 5 and all(parts) and all(len(p) in (8, 4, 4, 4, 12) for p in parts):
        return stem
    return None


def get_catalog():
    """Load catalogue index and books list from project files without backend dependencies."""
    catalogue_path = os.path.join(BASE_DIR, "activities_meta", "_catalogue.json")
    catalogue = {}
    if os.path.isfile(catalogue_path):
        try:
            with open(catalogue_path, "r", encoding="utf-8") as f:
                catalogue = json.load(f).get("books") or {}
        except Exception:
            pass

    books_file = os.path.join(BASE_DIR, "kitap_pdf_linkleri.txt")
    books_by_id = {}
    if os.path.isfile(books_file):
        with open(books_file, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f, 1):
                line = line.strip()
                if ": https://" not in line:
                    continue
                title, url = line.split(": https://", 1)
                m = re.search(r"Etkilesimlikitap/([a-f0-9\-]+)/pdf\.pdf", url)
                if not m:
                    continue
                book_id = m.group(1)
                entry = catalogue.get(book_id) or {}
                sinif = (entry.get("sinif_ad") or "").strip()
                grade = 0
                grade_label = "Seçmeli Dersler"
                if 1 <= idx <= 10:
                    grade, grade_label = 9, "9. Sınıf"
                elif 11 <= idx <= 20:
                    grade, grade_label = 10, "10. Sınıf"
                elif 21 <= idx <= 31:
                    grade, grade_label = 11, "11. Sınıf"
                elif 32 <= idx <= 45:
                    grade, grade_label = 12, "12. Sınıf"
                if sinif:
                    m_grade = re.match(r"^(\d+)\.\s*S\u0131n\u0131f", sinif) or re.search(r"(\d+)\s*$", sinif)
                    grade = int(m_grade.group(1)) if m_grade else 0
                    if grade not in (9, 10, 11, 12):
                        grade = 0
                    grade_label = sinif
                books_by_id[book_id] = {
                    "id": book_id,
                    "title": title.strip(),
                    "grade": grade,
                    "gradeLabel": grade_label,
                    "subject": (entry.get("ders_ad") or "").strip() or None,
                    "pageCount": entry.get("sayfasayisi"),
                    "interactiveCount": entry.get("interactive_count"),
                }
    return books_by_id


def generate_thumbnail_from_local(book_id, pdf_path, thumbs_dir):
    os.makedirs(thumbs_dir, exist_ok=True)
    thumb_path = os.path.join(thumbs_dir, f"{book_id}.jpg")
    if not os.path.isfile(thumb_path):
        try:
            out_prefix = os.path.join(thumbs_dir, f"tmp_{book_id}")
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
    return thumb_path if os.path.isfile(thumb_path) else None


def find_source_pdf(book_id, explicit=None):
    """
    The PDF to package, as a real file whose *name* is the catalogue id.
    """
    if explicit:
        return os.path.abspath(explicit)
    candidates = [
        os.path.join(BASE_DIR, "books", f"{book_id}.pdf"),
        os.path.join(BASE_DIR, "books", "full_pdf.pdf") if book_id == "0e966773-5012-4f57-8be5-d892e8c75f22" else None,
        os.path.join(BASE_DIR, "books", "matematik.pdf") if book_id == "51cdbbce-66f4-4baa-95d7-634bf11b7e44" else None,
        os.path.join(BASE_DIR, "pdf_parts", "full_pdf.pdf") if book_id == "0e966773-5012-4f57-8be5-d892e8c75f22" else None,
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError(
        f"no local PDF for {book_id}: download it (or pass a path) first"
    )


def stage_pdf(source, dest_dir, book_id, quiet=False):
    """
    Put the PDF next to the bundle under its catalogue name, cheaply.

    A hard link is used when the two live on one filesystem, so a 140 MB book
    is not copied twice; a symlink would not do, because the baker resolves the
    link before reading the name off it.
    """
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, f"{book_id}.pdf")
    if os.path.exists(target):
        return target
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def run_bake(pdf, force=False, quiet=False):
    """Run the reader's own detector over the whole book, once."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bake_activities.mjs")
    args = ["node", script, pdf]
    if force:
        args.append("--force")
    log(f"  baking regions: {' '.join(args[1:])}", quiet)
    run = subprocess.run(args, cwd=BASE_DIR, capture_output=True, text=True)
    if run.returncode != 0:
        detail = (run.stderr or run.stdout or "").strip().splitlines()
        raise RuntimeError(f"bake failed for {os.path.basename(pdf)}: {detail[-1] if detail else 'unknown error'}")
    for line in (run.stdout or "").strip().splitlines():
        log(f"  {line}", quiet)
    return True


def load_regions(book_id):
    path = os.path.join(BASE_DIR, "activities", "books", book_id, "regions.json")
    if not os.path.isfile(path):
        return None, path
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f), path


def slim_oges(meta_path, quiet=False):
    """
    The publisher's activity entries, reduced to what the reader reads.

    One entry per activity the publisher hung on a page: the id, the guid of its
    interactive version, the printed page, the title it typed and the position
    of its icon. Everything else in the manifest -- unit ids, application
    records, media lists -- is authoring data the reader never looks at, and
    keeping it would multiply the bundle's metadata by an order of magnitude
    for nothing.

    Entries without a page (`sayfano` 0) belong to no sheet and are dropped:
    they carry position (0, 0) and would otherwise draw a pin in the corner of
    whichever page happened to be first.
    """
    if not os.path.isfile(meta_path):
        log(f"  warning: no publisher manifest at {meta_path}; the book will be "
            f"packaged without activity pointers (regions are unaffected)", quiet)
        return []
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    keep = ("id", "data", "baslik", "sayfano", "posx", "posy", "ogeturu", "sayfaustuoge")
    out = []
    for oge in meta.get("kitapogeList") or []:
        if oge.get("ogeturu") != 1 or not oge.get("data"):
            continue
        if not oge.get("sayfano"):
            continue
        out.append({k: oge.get(k) for k in keep})
    return out


def copy_activities(guids, dest_dir, quiet=False):
    """Copy the interactive versions a book's manifest points at, if present."""
    copied = []
    for guid in guids:
        src = os.path.join(BASE_DIR, "activities", guid)
        if not os.path.isfile(os.path.join(src, "index.html")):
            continue
        dst = os.path.join(dest_dir, guid)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        copied.append(guid)
    if guids and not copied:
        log("  note: --with-activities found no extracted activities on disk; "
            "run `python3 tools/content_extraction/extract_activities.py --book <id>` first", quiet)
    return copied


def ensure_thumbnail(book_id, pdf, bundle_dir, quiet=False):
    """Put a cover in the bundle, from the existing cache or by rendering one."""
    thumbs_dir = os.path.join(BASE_DIR, "thumbnails")
    existing = os.path.join(thumbs_dir, f"{book_id}.jpg")
    if not os.path.isfile(existing):
        generate_thumbnail_from_local(book_id, pdf, thumbs_dir)
    if not os.path.isfile(existing):
        log("  note: no thumbnail (pdftoppm or PIL unavailable); the library can "
            "render one from the first page instead", quiet)
        return False
    shutil.copy2(existing, os.path.join(bundle_dir, "thumbnail.jpg"))
    return True


def package_book(book_id, out_dir=None, force=False, with_activities=False,
                 thumbnail=True, quiet=False, source_pdf=None):
    """
    Build one bundle and return its manifest entry.

    Raises rather than shipping a half-built book: a bundle whose PDF and
    regions disagree about how many pages it has is worse than no bundle at
    all, because the reader would draw another edition's rectangles onto it.
    """
    books_by_id = get_catalog()
    book = books_by_id.get(book_id) or {}
    out_dir = os.path.abspath(out_dir or os.path.join(BASE_DIR, "library"))
    bundle_dir = os.path.join(out_dir, "books", book_id)

    source = find_source_pdf(book_id, explicit=source_pdf)
    log(f"{book_id}: packaging from {os.path.relpath(source, BASE_DIR)}", quiet)

    # 1. The bake. Done first, in a staging directory the baker can name
    #    correctly, and only then moved into place.
    staging = os.path.join(bundle_dir, ".staging")
    staged_pdf = stage_pdf(source, staging, book_id, quiet=quiet)
    os.makedirs(bundle_dir, exist_ok=True)
    final_pdf = os.path.join(bundle_dir, "book.pdf")
    if os.path.exists(final_pdf):
        os.remove(final_pdf)
    os.replace(staged_pdf, final_pdf)
    try:
        os.rmdir(staging)
    except OSError:
        pass

    regions, regions_path = load_regions(book_id)
    if regions is None or force:
        # The baker needs the file to *be* named after the book id, and the
        # bundle's copy is `book.pdf`, so it is staged again for the run and
        # then thrown away.
        bake_tmp = os.path.join(bundle_dir, ".bake")
        bake_pdf = stage_pdf(final_pdf, bake_tmp, book_id, quiet=quiet)
        try:
            run_bake(bake_pdf, force=force, quiet=quiet)
        finally:
            shutil.rmtree(bake_tmp, ignore_errors=True)
        regions, regions_path = load_regions(book_id)

    if regions is None:
        raise RuntimeError(f"{book_id}: no bake produced; cannot package")

    version = regions.get("version")
    if version != BAKE_VERSION:
        raise RuntimeError(
            f"{book_id}: bake version {version} is not the {BAKE_VERSION} this "
            f"packager writes; re-bake with --force"
        )

    # 2. The publisher's activity pointers, and the activities themselves when
    #    asked for. Default is pointers only: the content is JIT-loaded.
    meta_path = os.path.join(BASE_DIR, "activities_meta", f"{book_id}.json")
    oges = slim_oges(meta_path, quiet=quiet)
    guids = []
    seen = set()
    for oge in oges:
        guid = oge.get("data")
        if guid and guid not in seen:
            seen.add(guid)
            guids.append(guid)
    bundled_activities = []
    if with_activities:
        bundled_activities = copy_activities(guids, os.path.join(bundle_dir, "activities"), quiet=quiet)

    # 3. Metadata: what the library screen shows, plus what the reader needs to
    #    join this book's regions to the publisher's entries.
    print_hash = fingerprint(final_pdf)
    book_meta = {
        "schema": 1,
        "id": book_id,
        "title": book.get("title") or book_id,
        "grade": book.get("grade", 0),
        "gradeLabel": book.get("gradeLabel") or "Kitaplık",
        "subject": book.get("subject"),
        "pageCount": regions.get("pageCount"),
        "fingerprint": print_hash,
        "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bakeVersion": version,
        "bakeFingerprint": regions.get("fingerprint"),
        "calibration": regions.get("calibration") or {},
        "activities": bundled_activities,
        "kitapogeList": oges,
    }
    with open(os.path.join(bundle_dir, "book.json"), "w", encoding="utf-8") as f:
        json.dump(book_meta, f, ensure_ascii=False)

    shutil.copy2(regions_path, os.path.join(bundle_dir, "regions.json"))

    has_thumb = False
    if thumbnail:
        has_thumb = ensure_thumbnail(book_id, final_pdf, bundle_dir, quiet=quiet)

    entry = {
        "id": book_id,
        "title": book_meta["title"],
        "grade": book_meta["grade"],
        "gradeLabel": book_meta["gradeLabel"],
        "subject": book_meta["subject"],
        "pageCount": book_meta["pageCount"],
        "interactiveCount": len(guids),
        "hasInteractive": bool(guids),
        "hasThumbnail": has_thumb,
        "bundle": book_id,
        "fingerprint": print_hash,
        "bakeFingerprint": regions.get("fingerprint"),
        "bakeVersion": version,
        "builtAt": book_meta["builtAt"],
        "pdfBytes": os.path.getsize(final_pdf),
        "regionsBytes": os.path.getsize(os.path.join(bundle_dir, "regions.json")),
        "activitiesBundled": len(bundled_activities),
    }
    pages = len(regions.get("pages") or {})
    regions_n = sum(len(p.get("activities") or []) for p in (regions.get("pages") or {}).values())
    questions = sum(
        len(a.get("items") or [])
        for p in (regions.get("pages") or {}).values()
        for a in (p.get("activities") or [])
    )
    log(
        f"  {book_meta['pageCount']} pages, {regions_n} regions, {questions} questions, "
        f"{len(oges)} pointers, {len(bundled_activities)} bundled activities"
        f"{'' if has_thumb else ', no thumbnail'} ({pages} pages baked)",
        quiet,
    )
    return entry


def write_manifest(out_dir, entry, quiet=False):
    """Upsert one book's entry into the library's index, creating the index."""
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "manifest.json")
    manifest = {"schema": 1, "edition": "school", "generatedAt": None, "books": []}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                manifest.update(json.load(f))
        except (OSError, ValueError):
            pass
    books = [b for b in manifest.get("books") or [] if b.get("id") != entry["id"]]
    books.append(entry)
    books.sort(key=lambda b: (b.get("grade", 0), (b.get("title") or "").lower()))
    manifest["books"] = books
    manifest["generatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["bookCount"] = len(books)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    log(f"  library manifest: {os.path.relpath(path, BASE_DIR)} ({len(books)} books)", quiet)
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Package one textbook into a library bundle.")
    parser.add_argument("book", help="Catalogue id, or the path to a <id>.pdf")
    parser.add_argument("--out", default=os.path.join(BASE_DIR, "library"),
                        help="Library directory to write into (default: ./library)")
    parser.add_argument("--force", action="store_true", help="Re-bake even if a current bake exists")
    parser.add_argument("--with-activities", action="store_true",
                        help="Copy interactive activities into the bundle (default: fetch on demand)")
    parser.add_argument("--no-thumbnail", action="store_true", help="Do not copy or render a cover")
    parser.add_argument("--quiet", action="store_true", help="Only print warnings and errors")
    args = parser.parse_args()

    book_id = args.book
    source = None
    if os.path.isfile(book_id):
        source = os.path.abspath(book_id)
        book_id = book_id_for_path(source)
        if not book_id:
            print(f"error: {os.path.basename(source)} is not named after a catalogue id "
                  f"(expected <guid>.pdf)", file=sys.stderr)
            return 2

    try:
        entry = package_book(
            book_id,
            out_dir=args.out,
            force=args.force,
            with_activities=args.with_activities,
            thumbnail=not args.no_thumbnail,
            quiet=args.quiet,
            source_pdf=source,
        )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    write_manifest(args.out, entry, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
