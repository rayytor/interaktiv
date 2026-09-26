#!/usr/bin/env python3
"""
Fetch "Kitap Sunumları" (teacher-made textbook slide decks) published as
iSpring HTML5 exports, and store them in a form the training pipeline can use.

Why these decks matter
----------------------
Teachers build these presentations by cropping each activity out of the
official MEB coursebook page and pasting the crop onto a slide, one slide per
page or per exercise. That makes every picture in a deck a human-drawn
question region -- exactly the label the hotspot detector cannot otherwise
obtain without hand labelling. Matching a crop back onto the PDF page yields
a bounding box for free.

What an iSpring HTML5 export looks like
---------------------------------------
    <deck>/index.html      -- holds `var presInfo = "<base64(zlib(JSON))>"`
    <deck>/data/slideN.js  -- one JS file per slide; the slide's HTML is the
                              second argument of a loadHandler(...) call
    <deck>/data/imgN.png   -- pictures referenced by <img src="data/imgN.png">
    <deck>/data/*.mp3|mp4|woff|css -- audio, video, fonts: not fetched

The manifest (`presInfo`) lists the slides in order and carries each slide's
visible text (key "x"), which usually includes the printed page number the
slide covers.  We keep it, the slide scripts and every picture a slide shows,
and write `manifest.json` with each picture's placement on its slide.

Politeness
----------
One deck at a time, a handful of parallel connections per deck, identifying
user agent, resumable (existing files are skipped), and a short pause between
decks.  These are small teacher-run sites.

Usage
-----
    python3 tools/kitap_sunumlari/fetch_ispring.py                  # all sources
    python3 tools/kitap_sunumlari/fetch_ispring.py --only 9-1-WAYMARK
    python3 tools/kitap_sunumlari/fetch_ispring.py --list
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SOURCES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.json")
DEST_ROOT = os.path.join(PROJECT_ROOT, "kitap_sunumlari")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36 interaktiv-kitap-sunumlari/1.0")
TIMEOUT = 60
RETRIES = 3
WORKERS = 6
PAUSE_BETWEEN_DECKS = 1.5

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")


# ---------------------------------------------------------------- HTTP


def http_get(url, binary=True):
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = resp.read()
                return data if binary else data.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 403, 410):
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = exc
        time.sleep(1.5 * (attempt + 1))
    raise last


def fetch_to(url, path):
    """Download url to path unless it already exists; return (bytes, skipped)."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return os.path.getsize(path), True
    data = http_get(url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return len(data), False


# ---------------------------------------------------------------- iSpring parsing


def decode_presinfo(index_html):
    m = re.search(r'var presInfo = "([^"]+)"', index_html)
    if not m:
        raise ValueError("no presInfo in index.html (not an iSpring HTML5 export?)")
    raw = zlib.decompress(base64.b64decode(m.group(1)))
    return json.loads(raw.decode("utf-8"))


def slide_html_from_js(js_text):
    """The slide HTML is the second argument of loadHandler(n, '<html>', '{json}')."""
    m = re.search(r"loadHandler\(\s*\d+\s*,\s*'", js_text)
    if not m:
        return ""
    i = m.end()
    out = []
    while i < len(js_text):
        ch = js_text[i]
        if ch == "\\" and i + 1 < len(js_text):
            nxt = js_text[i + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t", "'": "'", '"': '"', "\\": "\\", "/": "/"}.get(nxt, nxt))
            i += 2
            continue
        if ch == "'":
            break
        out.append(ch)
        i += 1
    return "".join(out)


_PX = re.compile(r"(-?\d+(?:\.\d+)?)")


def _px(value):
    if value is None:
        return None
    m = _PX.search(str(value))
    return float(m.group(1)) if m else None


def _style_dict(style):
    d = {}
    for part in (style or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            d[k.strip().lower()] = v.strip()
    return d


class _SlideImages(HTMLParser):
    """Walk the slide's nested absolutely-positioned divs and place every <img>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []  # (dx, dy) contributed by each open element
        self.images = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        st = _style_dict(a.get("style"))
        dx = _px(st.get("left")) or 0.0
        dy = _px(st.get("top")) or 0.0
        if tag == "img":
            ox = sum(x for x, _ in self.stack)
            oy = sum(y for _, y in self.stack)
            w = _px(a.get("width")) or _px(st.get("width"))
            h = _px(a.get("height")) or _px(st.get("height"))
            self.images.append({
                "src": a.get("src", ""),
                "x": round(ox + dx, 2),
                "y": round(oy + dy, 2),
                "w": w,
                "h": h,
                "id": a.get("id", ""),
            })
            return  # <img> is void; do not push
        if tag in ("br", "path", "rect", "circle", "line", "polygon", "stop", "use", "image", "source"):
            return
        self.stack.append((dx, dy))

    def handle_endtag(self, tag):
        if tag in ("img", "br", "path", "rect", "circle", "line", "polygon", "stop", "use", "image", "source"):
            return
        if self.stack:
            self.stack.pop()


def parse_slide_images(slide_html):
    p = _SlideImages()
    try:
        p.feed(slide_html)
    except Exception:
        pass
    return p.images


def png_or_jpeg_size(path):
    """Read pixel dimensions from a PNG/JPEG/GIF header without any imaging library."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                import struct
                w, h = struct.unpack(">II", head[16:24])
                return w, h
            if head[:6] in (b"GIF87a", b"GIF89a"):
                import struct
                w, h = struct.unpack("<HH", head[6:10])
                return w, h
            if head[:2] == b"\xff\xd8":
                fh.seek(2)
                import struct
                while True:
                    marker = fh.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    if marker[1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        fh.read(3)
                        h, w = struct.unpack(">HH", fh.read(4))
                        return w, h
                    seg = struct.unpack(">H", fh.read(2))[0]
                    fh.seek(seg - 2, 1)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------- per-deck


def fetch_deck(src, dest_root, log):
    base = src["url"].rstrip("/") + "/"
    slug = src["slug"]
    dest = os.path.join(dest_root, src["site"], slug)
    os.makedirs(dest, exist_ok=True)

    index_path = os.path.join(dest, "index.html")
    n, skipped = fetch_to(base, index_path)
    index_html = open(index_path, encoding="utf-8", errors="replace").read()
    pres = decode_presinfo(index_html)
    with open(os.path.join(dest, "presinfo.json"), "w", encoding="utf-8") as fh:
        json.dump(pres, fh, ensure_ascii=False, indent=1)

    slides = pres.get("s") or []
    slide_w, slide_h = pres.get("w"), pres.get("h")
    log(f"  {slug}: {len(slides)} slides, {slide_w}x{slide_h}, title={pres.get('t')!r}")

    # 1. slide scripts (parallel)
    slide_files = []
    for i, sl in enumerate(slides):
        rel = sl.get("s") or f"data/slide{i + 1}.js"
        slide_files.append((i, sl, rel))

    def _get_slide(item):
        i, sl, rel = item
        path = os.path.join(dest, rel.replace("/", os.sep))
        try:
            fetch_to(urllib.parse.urljoin(base, rel), path)
            return i, rel, None
        except Exception as exc:
            return i, rel, str(exc)

    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(_get_slide, slide_files))
    failures = [(i, rel, e) for i, rel, e in results if e]
    for i, rel, e in failures:
        log(f"    slide {i + 1} ({rel}) failed: {e}")

    # 2. parse slides, collect image refs
    manifest_slides = []
    image_refs = {}
    for i, sl, rel in slide_files:
        path = os.path.join(dest, rel.replace("/", os.sep))
        images = []
        if os.path.exists(path):
            html_text = slide_html_from_js(open(path, encoding="utf-8", errors="replace").read())
            for im in parse_slide_images(html_text):
                srcrel = im["src"]
                if not srcrel or srcrel.startswith(("data:", "http")):
                    continue
                is_bg = (im["x"] == 0 and im["y"] == 0 and im["w"] and im["h"]
                         and slide_w and slide_h and abs(im["w"] - slide_w) < 2 and abs(im["h"] - slide_h) < 2)
                im["background"] = bool(is_bg)
                images.append(im)
                image_refs.setdefault(srcrel, 0)
                image_refs[srcrel] += 1
        manifest_slides.append({
            "n": i + 1,
            "script": rel,
            "text": sl.get("x") or "",
            "title": sl.get("t") or "",
            "hidden": bool(sl.get("d")),
            "images": images,
        })

    # 3. images (parallel, deduplicated)
    def _get_img(rel):
        path = os.path.join(dest, rel.replace("/", os.sep))
        try:
            n, skipped = fetch_to(urllib.parse.urljoin(base, rel), path)
            return rel, n, None
        except Exception as exc:
            return rel, 0, str(exc)

    img_meta = {}
    total_bytes = 0
    with ThreadPoolExecutor(WORKERS) as ex:
        for rel, n, err in ex.map(_get_img, sorted(image_refs)):
            if err:
                log(f"    image {rel} failed: {err}")
                img_meta[rel] = {"error": err, "uses": image_refs[rel]}
                continue
            path = os.path.join(dest, rel.replace("/", os.sep))
            total_bytes += n
            dims = png_or_jpeg_size(path)
            with open(path, "rb") as fh:
                digest = hashlib.sha1(fh.read()).hexdigest()
            img_meta[rel] = {
                "bytes": os.path.getsize(path),
                "px": list(dims) if dims else None,
                "sha1": digest,
                "uses": image_refs[rel],
            }

    manifest = {
        "site": src["site"],
        "slug": slug,
        "source_url": base,
        "source_page": src.get("page"),
        "title": pres.get("t"),
        "grade": src.get("grade"),
        "book": src.get("book"),
        "book_kind": src.get("book_kind"),
        "unit": src.get("unit"),
        "school_year": src.get("school_year"),
        "notes": src.get("notes"),
        "slide_size": [slide_w, slide_h],
        "slide_count": len(slides),
        "generator": pres.get("ui"),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "slides": manifest_slides,
        "images": img_meta,
    }
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    n_img = sum(1 for m in img_meta.values() if "bytes" in m)
    n_fail = sum(1 for m in img_meta.values() if "error" in m)
    size_mb = sum(m.get("bytes", 0) for m in img_meta.values()) / 1e6
    log(f"    {n_img} images ({size_mb:.1f} MB on disk), {n_fail} failed, {len(failures)} slide scripts failed")
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default=SOURCES_PATH)
    ap.add_argument("--dest", default=DEST_ROOT)
    ap.add_argument("--only", action="append", default=[], help="substring of slug to fetch (repeatable)")
    ap.add_argument("--site", help="only this site key")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    with open(args.sources, encoding="utf-8") as fh:
        sources = json.load(fh)["decks"]
    if args.site:
        sources = [s for s in sources if s["site"] == args.site]
    if args.only:
        sources = [s for s in sources if any(o.lower() in s["slug"].lower() for o in args.only)]
    if args.list:
        for s in sources:
            print(f"{s['site']:16} {s['slug']:60} grade {s.get('grade')} {s.get('book')} {s.get('unit')}")
        return

    def log(msg):
        print(msg, flush=True)

    ok, bad = 0, []
    index = []
    for k, src in enumerate(sources, 1):
        log(f"[{k}/{len(sources)}] {src['site']}/{src['slug']}")
        try:
            m = fetch_deck(src, args.dest, log)
            ok += 1
            index.append({k2: m[k2] for k2 in ("site", "slug", "title", "grade", "book", "book_kind", "unit",
                                                "school_year", "slide_count", "source_url", "source_page")}
                         | {"images": sum(1 for v in m["images"].values() if "bytes" in v),
                            "path": os.path.relpath(os.path.join(args.dest, src["site"], src["slug"]), PROJECT_ROOT)})
        except Exception as exc:
            log(f"    FAILED: {exc}")
            bad.append((src["slug"], str(exc)))
        time.sleep(PAUSE_BETWEEN_DECKS)

    # merge into the root index (keep entries for decks not touched in this run)
    index_path = os.path.join(args.dest, "index.json")
    existing = {}
    if os.path.exists(index_path):
        try:
            for e in json.load(open(index_path, encoding="utf-8"))["decks"]:
                existing[(e["site"], e["slug"])] = e
        except Exception:
            pass
    for e in index:
        existing[(e["site"], e["slug"])] = e
    os.makedirs(args.dest, exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump({"written": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "decks": sorted(existing.values(), key=lambda e: (e["site"], e["slug"]))},
                  fh, ensure_ascii=False, indent=1)
    log(f"done: {ok} decks ok, {len(bad)} failed")
    for slug, err in bad:
        log(f"  {slug}: {err}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
