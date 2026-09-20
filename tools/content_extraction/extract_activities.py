#!/usr/bin/env python3
"""
extract_activities.py - Extract & scrape interactive activities from OGM Materyal / EBA.
Supports granular downloading (e.g. only HTML/JS, skip audio, skip video),
automatic asset discovery, CDN fallback rewriting for skipped media,
and offline local serving. Zero external dependencies (Python standard library only).
"""

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

API_BASE_URL = "https://ogmmateryal.eba.gov.tr/ogm-test-v2/api"
API_KEY = "18395axcje-62860-ogm-mat-web-aschew193257"
UYGULAMA_CDN_BASE = "https://ogm-large-cdn.eba.gov.tr/materyal/Uygulama"
DOSYA_CDN_BASE = "https://ogm-large-cdn.eba.gov.tr/materyal/Dosya"

AUDIO_EXTS = {".mp3", ".ogg", ".wav", ".m4a", ".aac"}
VIDEO_EXTS = {".mp4", ".webm", ".ogv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".ico"}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ACTIVITIES_DIR = os.path.join(BASE_DIR, "activities")
META_DIR = os.path.join(BASE_DIR, "activities_meta")
BOOKS_CATALOG_PATH = os.path.join(BASE_DIR, "kitap_pdf_linkleri.txt")
# The index of every book the publisher lists, written by sync_catalogue().
CATALOGUE_PATH = os.path.join(META_DIR, "_catalogue.json")
CATALOGUE_MAX_AGE = 7 * 24 * 3600  # seconds before a sync is considered stale


def set_paths(activities_dir=None, meta_dir=None):
    """
    Point the fetcher at writable directories.

    The reader calls this so a JIT-fetched activity lands in its own cache —
    the school app is served off a library that may be read-only, and the tree
    it is running from may be read-only too.
    """
    global ACTIVITIES_DIR, META_DIR, CATALOGUE_PATH
    if activities_dir:
        ACTIVITIES_DIR = os.path.abspath(activities_dir)
    if meta_dir:
        META_DIR = os.path.abspath(meta_dir)
        CATALOGUE_PATH = os.path.join(META_DIR, "_catalogue.json")
    return ACTIVITIES_DIR, META_DIR


# Creating these is a convenience for the CLI, not a requirement of importing
# the module: a read-only install must still be able to import it to serve
# whatever is already cached.
for _dir in (ACTIVITIES_DIR, META_DIR):
    try:
        os.makedirs(_dir, exist_ok=True)
    except OSError:
        pass


def fetch_api(path):
    """Fetch JSON from OGM API with public API key."""
    url = f"{API_BASE_URL}/{path.lstrip('/')}"
    req = urllib.request.Request(
        url,
        headers={
            "X-Api-Key": API_KEY,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Interaktiv/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download_file(url, target_path, timeout=30, retries=3):
    """Download a file from url to target_path with retries and exponential backoff."""
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Interaktiv/1.0"},
    )
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                with open(target_path, "wb") as f:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
            return True
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise last_err


class AssetFinder(HTMLParser):
    """HTML parser to extract relative asset references."""
    def __init__(self):
        super().__init__()
        self.assets = set()

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        for key in ("src", "href", "data-src", "poster"):
            val = attr_dict.get(key)
            if val and not val.startswith("data:") and not val.startswith("javascript:") and not val.startswith("#"):
                self.assets.add(val.strip())


def extract_assets_from_html(html_text):
    """Extract all relative asset links from HTML markup and CSS url() rules."""
    parser = AssetFinder()
    try:
        parser.feed(html_text)
    except Exception:
        pass
    assets = parser.assets

    # Extract CSS url(...) references
    css_urls = re.findall(r'url\(\s*[\'"]?([^\'")]+)[\'"]?\s*\)', html_text, re.IGNORECASE)
    for u in css_urls:
        u = u.strip()
        if not u.startswith("data:") and not u.startswith("javascript:"):
            assets.add(u)

    # Filter out absolute external URLs like https://fonts.googleapis.com
    relative_assets = set()
    for a in assets:
        parsed = urllib.parse.urlparse(a)
        if not parsed.scheme and not a.startswith("//"):
            # Clean query string / anchor
            clean_path = parsed.path.lstrip("/")
            if clean_path:
                relative_assets.add(clean_path)

    return relative_assets


def extract_activity(
    guid,
    dest_dir=None,
    force=False,
    no_audio=False,
    no_video=False,
    no_images=False,
    only_html=False,
    verbose=True,
):
    """
    Extracts a single activity GUID from the OGM CDN into dest_dir.
    Applies granular filtering (no_audio, no_video, etc.).
    When media is skipped, rewrites relative references in index.html
    to point directly to the CDN so the activity still functions online!
    """
    if dest_dir is None:
        dest_dir = os.path.join(ACTIVITIES_DIR, guid)
    os.makedirs(dest_dir, exist_ok=True)

    if not force and os.path.isfile(os.path.join(dest_dir, "index.html")):
        if verbose:
            print(f"  Activity {guid} already installed, skipping (force=False)")
        return True

    activity_base_url = f"{UYGULAMA_CDN_BASE}/{guid}"
    index_url = f"{activity_base_url}/index.html"

    if verbose:
        print(f"Fetching activity: {guid}")
        print(f"  URL: {index_url}")

    req = urllib.request.Request(
        index_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Interaktiv/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html_content = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        if verbose:
            print(f"  Failed to fetch index.html: HTTP {e.code}")
        return False
    except Exception as e:
        if verbose:
            print(f"  Error fetching index.html: {e}")
        return False

    assets = extract_assets_from_html(html_content)
    if verbose:
        print(f"  Discovered {len(assets)} referenced asset(s): {list(assets)}")

    downloaded = []
    failed_assets = []
    skipped_rewrites = {}

    for asset in assets:
        ext = os.path.splitext(asset)[1].lower()
        is_audio = ext in AUDIO_EXTS
        is_video = ext in VIDEO_EXTS
        is_image = ext in IMAGE_EXTS

        skip = False
        if only_html and (is_audio or is_video or is_image):
            skip = True
        elif no_audio and is_audio:
            skip = True
        elif no_video and is_video:
            skip = True
        elif no_images and is_image:
            skip = True

        cdn_asset_url = f"{activity_base_url}/{asset}"

        if skip:
            # We rewrite the reference in index.html to the full CDN URL
            skipped_rewrites[asset] = cdn_asset_url
            if verbose:
                print(f"  [SKIPPED] {asset} -> CDN fallback {cdn_asset_url}")
        else:
            target_path = os.path.join(dest_dir, asset)
            if verbose:
                print(f"  [DOWNLOADING] {asset} -> {target_path}...")
            try:
                download_file(cdn_asset_url, target_path)
                downloaded.append(asset)
            except Exception as e:
                print(f"  [FAILED] {asset}: {e}")
                failed_assets.append(asset)
                # Fallback rewrite to CDN
                skipped_rewrites[asset] = cdn_asset_url

    # Perform rewrites for skipped assets in html_content
    for local_rel, cdn_url in skipped_rewrites.items():
        # Match exact src="local_rel" or src='local_rel' or url(local_rel).
        # `[src|href|poster]` is a character set, not an alternation: it matched
        # any one of those letters, so `a="asset.png"` was rewritten and
        # `poster="asset.png"` was matched only by its final `r`.
        pattern = re.compile(
            r'((?:src|href|poster)=[\'"])' + re.escape(local_rel) + r'([\'"])',
            re.IGNORECASE,
        )
        html_content = pattern.sub(r"\1" + cdn_url + r"\2", html_content)

        css_pattern = re.compile(
            r'(url\(\s*[\'"]?)' + re.escape(local_rel) + r'([\'"]?\s*\))',
            re.IGNORECASE,
        )
        html_content = css_pattern.sub(r"\1" + cdn_url + r"\2", html_content)

    # Save index.html
    index_path = os.path.join(dest_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Save extraction manifest
    manifest = {
        "guid": guid,
        "index_url": index_url,
        "downloaded_assets": downloaded,
        "failed_assets": failed_assets,
        "skipped_cdn_rewrites": skipped_rewrites,
        "options": {
            "no_audio": no_audio,
            "no_video": no_video,
            "no_images": no_images,
            "only_html": only_html,
        },
    }
    with open(os.path.join(dest_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    if verbose:
        print(f"  Saved {index_path} (downloaded: {len(downloaded)}, failed: {len(failed_assets)}, skipped/CDN: {len(skipped_rewrites)})")

    return True


def local_catalogue_ids():
    """The book guids listed in kitap_pdf_linkleri.txt, in file order."""
    out = []
    if not os.path.isfile(BOOKS_CATALOG_PATH):
        return out
    with open(BOOKS_CATALOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if ": https://" not in line:
                continue
            title, url = line.split(": https://", 1)
            m = re.search(r"Etkilesimlikitap/([a-f0-9\-]+)/pdf\.pdf", "https://" + url.strip())
            if m:
                out.append((title.strip(), m.group(1)))
    return out


def meta_path_for(book_guid):
    return os.path.join(META_DIR, f"{book_guid}.json")


def catalogue_age():
    """Seconds since the catalogue was last synced, or None if never."""
    if not os.path.isfile(CATALOGUE_PATH):
        return None
    return time.time() - os.path.getmtime(CATALOGUE_PATH)


def sync_catalogue(force=False, wanted=None, progress=None):
    """
    Walk the publisher's catalogue once and cache what it returns.

    There is no lookup-by-id endpoint, so finding one book means walking every
    grade and every subject -- about 155 requests. The walk's own responses
    already carry each book's complete `kitapogeList`, though, so the whole
    catalogue is in hand by the end of it. Doing that walk per book, which is
    what get_book_metadata() used to do, paid 155 requests for one manifest and
    threw the other 380 away.

    Manifests are written for `wanted` (by default the books listed in
    kitap_pdf_linkleri.txt); the index of everything the publisher lists is
    written to _catalogue.json either way, so a later lookup costs no network at
    all.

    Returns the index dict, or None when the catalogue could not be reached.
    """
    if not force:
        age = catalogue_age()
        if age is not None and age < CATALOGUE_MAX_AGE:
            try:
                with open(CATALOGUE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass  # unreadable cache: fall through and rebuild it

    if wanted is None:
        wanted = {guid for _, guid in local_catalogue_ids()}
    wanted = set(wanted or ())

    def say(msg):
        if progress:
            progress(msg)

    try:
        siniflar = fetch_api("SinifPublic").get("data", [])
    except Exception as e:
        say(f"catalogue unreachable: {e}")
        return None

    books = {}
    written = []
    for sinif in siniflar:
        s_id = sinif.get("id")
        s_ad = sinif.get("ad")
        try:
            dersler = fetch_api(f"DersPublic/BySinif/{s_id}").get("data", [])
        except Exception:
            continue
        say(f"{s_ad}: {len(dersler)} subject(s)")
        for ders in dersler:
            d_id = ders.get("id")
            try:
                found = fetch_api(f"KitapPublic/BySinifAndDers/{s_id}/{d_id}").get("data", [])
            except Exception:
                continue
            for b in found:
                klasor = (b.get("klasoradi") or "").strip()
                if not klasor:
                    continue
                oges = b.get("kitapogeList") or []
                books[klasor] = {
                    "id": (b.get("id") or "").strip(),
                    "klasoradi": klasor,
                    "baslik": b.get("baslik"),
                    "dersbaslik": b.get("dersbaslik"),
                    "sinif_ad": s_ad,
                    "sinif_kod": sinif.get("kod"),
                    "ders_ad": ders.get("ad"),
                    "sayfasayisi": b.get("sayfasayisi"),
                    "oge_count": len(oges),
                    "interactive_count": sum(
                        1 for o in oges if o.get("ogeturu") == 1 and o.get("data")
                    ),
                }
                if klasor in wanted or (b.get("id") or "").strip() in wanted:
                    with open(meta_path_for(klasor), "w", encoding="utf-8") as f:
                        json.dump(b, f, indent=2, ensure_ascii=False)
                    written.append(klasor)

    index = {
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "books": books,
        "written": sorted(set(written)),
    }
    with open(CATALOGUE_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    say(f"catalogue: {len(books)} book(s) listed, {len(set(written))} manifest(s) written")
    return index


def get_book_metadata(book_guid, force_refresh=False):
    """
    The publisher's manifest for one book, from cache or from a catalogue sync.
    Returns the parsed dict with kitapogeList, or None if the book is not listed.
    """
    meta_path = meta_path_for(book_guid)
    if not force_refresh and os.path.isfile(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # The sync writes manifests for every book we know of, so asking for one
    # book that is missing fills in all the others at the same time.
    wanted = {guid for _, guid in local_catalogue_ids()}
    wanted.add(book_guid)
    index = sync_catalogue(force=force_refresh, wanted=wanted)

    if os.path.isfile(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # Listed under a guid we did not ask for (the API's own id rather than the
    # folder name the PDF is stored under), or not listed at all.
    if index:
        for klasor, entry in (index.get("books") or {}).items():
            if entry.get("id") == book_guid:
                return get_book_metadata(klasor, force_refresh=force_refresh)
    return None


def extract_book_activities(
    book_guid,
    no_audio=False,
    no_video=False,
    no_images=False,
    only_html=False,
    max_workers=4,
    dry_run=False,
):
    """
    Extracts all interactive activities (ogeturu == 1) for a book.
    """
    meta = get_book_metadata(book_guid)
    if not meta:
        print(f"Error: Could not find book metadata for {book_guid}")
        return False

    oges = meta.get("kitapogeList", [])
    uygulamalar = [o for o in oges if o.get("ogeturu") == 1 and o.get("data")]
    print(f"Book '{meta.get('baslik')}': {len(uygulamalar)} interactive activities found out of {len(oges)} oges.")

    if dry_run:
        for idx, u in enumerate(uygulamalar, 1):
            print(f"  [{idx}/{len(uygulamalar)}] Page {u.get('sayfano')} - {u.get('baslik')}: GUID={u.get('data')}")
        return True

    success_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                extract_activity,
                u["data"],
                None,
                no_audio,
                no_video,
                no_images,
                only_html,
                False,  # quiet in worker
            ): u
            for u in uygulamalar
        }
        for future in concurrent.futures.as_completed(futures):
            u = futures[future]
            try:
                ok = future.result()
                if ok:
                    success_count += 1
                    print(f"✓ [Page {u.get('sayfano')} · {u.get('baslik')}] Extracted {u.get('data')}")
                else:
                    print(f"✗ [Page {u.get('sayfano')} · {u.get('baslik')}] Failed {u.get('data')}")
            except Exception as e:
                print(f"✗ [Page {u.get('sayfano')} · {u.get('baslik')}] Error {u.get('data')}: {e}")

    print(f"\nCompleted: {success_count}/{len(uygulamalar)} activities extracted successfully.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Extract interactive activities from OGM Materyal / EBA.")
    parser.add_argument("--guid", type=str, help="Extract a single activity by GUID (e.g. 5303510f-4ff7-4c40-bfba-ad0cae290efd)")
    parser.add_argument("--book", type=str, help="Extract all activities for a book GUID (e.g. 0e966773-5012-4f57-8be5-d892e8c75f22)")
    parser.add_argument("--meta-only", action="store_true", help="Only fetch and cache book metadata without downloading activities")
    parser.add_argument("--no-audio", action="store_true", help="Skip downloading audio files (.mp3, .wav, etc.) and fallback to CDN")
    parser.add_argument("--no-video", action="store_true", help="Skip downloading video files (.mp4, etc.) and fallback to CDN")
    parser.add_argument("--no-images", action="store_true", help="Skip downloading image files and fallback to CDN")
    parser.add_argument("--only-html", action="store_true", help="Download only HTML, CSS, JS; all media falls back to CDN")
    parser.add_argument("--dry-run", action="store_true", help="List activities without downloading")
    parser.add_argument("--sync-catalogue", action="store_true", help="Walk the publisher's catalogue once and cache a manifest for every local book")
    parser.add_argument("--force", action="store_true", help="With --sync-catalogue, re-walk even if the cached catalogue is fresh")
    parser.add_argument("--workers", type=int, default=4, help="Number of concurrent download threads (default: 4)")

    args = parser.parse_args()

    if args.sync_catalogue:
        index = sync_catalogue(force=args.force, progress=lambda msg: print(f"  {msg}"))
        if not index:
            print("catalogue sync failed")
            sys.exit(1)
        local = local_catalogue_ids()
        written = set(index.get("written") or ())
        missing = [(t, g) for t, g in local if g not in written]
        interactive = sum(
            (index["books"].get(g) or {}).get("interactive_count", 0)
            for _, g in local
            if g in written
        )
        print(
            f"catalogue: {len(index.get('books') or {})} books listed, "
            f"{len(local) - len(missing)}/{len(local)} local books cached, "
            f"{interactive} interactive activities"
        )
        for title, guid in missing:
            print(f"  not listed by the publisher: {title} ({guid})")
        return

    if args.guid:
        extract_activity(
            args.guid,
            no_audio=args.no_audio,
            no_video=args.no_video,
            no_images=args.no_images,
            only_html=args.only_html,
            verbose=True,
        )
    elif args.book:
        if args.meta_only:
            meta = get_book_metadata(args.book, force_refresh=True)
            if meta:
                print(f"Successfully cached metadata for book: {meta.get('baslik')} ({len(meta.get('kitapogeList', []))} oges)")
            else:
                print(f"Failed to find metadata for book: {args.book}")
        else:
            extract_book_activities(
                args.book,
                no_audio=args.no_audio,
                no_video=args.no_video,
                no_images=args.no_images,
                only_html=args.only_html,
                max_workers=args.workers,
                dry_run=args.dry_run,
            )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
