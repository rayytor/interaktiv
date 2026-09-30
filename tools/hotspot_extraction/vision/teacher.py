#!/usr/bin/env python3
"""
Phase 2 of the vision plan: the teacher labels the selected pages, resumably.

The teacher is the Gemini API, called directly with the page JPEG attached,
through `google-genai`, using free-tier keys read one per line from `api.md`
(gitignored; the user's decision of 2026-09-28). Rate limits are per project,
so the keys are used round-robin with a per-key requests-per-minute limiter
(`--rpm`); a 429 puts that key on cooldown (or retires it for the run when the
error names the daily quota) and the page is retried on another key. The reply
is schema-constrained JSON: a list of
`{"box_2d": [ymin, xmin, ymax, xmax], "label": ...}` in 0–1000 image units,
written, with the boxes converted to PDF points, to

    data/vision/labels/api/<prompt-version>/<book-id>/<page:04d>.json

A page whose label file exists is skipped, so the same command resumes after
a crash or a quota stop. When every key is spent the run stops cleanly and
prints what remains; any other error is retried twice and then leaves a
`<page>.failed` marker beside the label path. Every request is appended to
`data/vision/labels/requests.jsonl`, from which "requests used today" is read.

The Antigravity CLI / IDE routes and the local Ollama (Qwen3-VL) route that
this file once had live outside the repository, in
`~/Projects/interaktiv-local-teacher/` (2026-09-30).

Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/teacher.py --limit 3                   # smoke
    .venv-vision/bin/python tools/hotspot_extraction/vision/teacher.py --workers 12 --rpm 10
    .venv-vision/bin/python tools/hotspot_extraction/vision/teacher.py --sample 0.1 --out-version v1-agree
    .venv-vision/bin/python tools/hotspot_extraction/vision/teacher.py --selection data/vision/select/reask.json --hint --out-version v1-reask
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hotspot_extraction.vision.select import read_index  # noqa: E402

VISION_DIR = Path(__file__).resolve().parent
PAGES_DIR = PROJECT_ROOT / "data" / "vision" / "pages"
LABELS_DIR = PROJECT_ROOT / "data" / "vision" / "labels"
DEFAULT_SELECTION = PROJECT_ROOT / "data" / "vision" / "select" / "round1.json"
DEFAULT_PROMPT = VISION_DIR / "teacher_prompt.md"
REQUEST_LOG = LABELS_DIR / "requests.jsonl"
DEFAULT_MODEL = "gemini-3.6-flash"           # thinking level LOW
THINKING_LEVEL = "LOW"
DEFAULT_KEYS_FILE = PROJECT_ROOT / "api.md"
DEFAULT_WORKERS = 12
DEFAULT_RPM = 10                              # per key; the free tier's usual Flash figure
API_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "box_2d": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
            "label": {"type": ["string", "null"]},
        },
        "required": ["box_2d", "label"],
    },
}
DEFAULT_TIMEOUT = 120
DEFAULT_RETRIES = 2

# "No capacity available for model ..." (503) comes and goes within a minute;
# it is not the quota and not the page's fault, so the key just cools down.
TRANSIENT_RE = re.compile(r"\b503\b|UNAVAILABLE|no capacity|overloaded|\b502\b|\b504\b|deadline exceeded", re.I)


class ParseError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# Prompt                                                                      #
# --------------------------------------------------------------------------- #

def load_prompt(path: Path = DEFAULT_PROMPT) -> Tuple[str, str, str]:
    """(version, body, sha) from teacher_prompt.md; the first line is `version: vN`."""
    text = path.read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    m = re.match(r"\s*version:\s*(\S+)", first)
    if not m:
        raise ValueError(f"{path}: first line must be 'version: vN', got {first!r}")
    body = rest.strip()
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return m.group(1), body, sha


def hint_line(count: Optional[int]) -> Optional[str]:
    if not count:
        return None
    return (f"The publisher lists {count} interactive activit{'y' if count == 1 else 'ies'} "
            f"on this page; make sure each has a box.")


def build_prompt(body: str, hint: Optional[str] = None) -> str:
    parts = ["The attached image is one textbook page.", body]
    if hint:
        parts.append(hint)
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Parsing and conversion                                                      #
# --------------------------------------------------------------------------- #

def _balanced_list(text: str, start: int) -> Optional[str]:
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_boxes(response: str) -> List[Dict[str, Any]]:
    """The JSON list out of a reply that may carry prose or a ```json fence."""
    if response is None:
        raise ParseError("empty response")
    text = response.strip()
    candidates: List[str] = []
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S):
        candidates.append(m.group(1).strip())
    candidates.append(text)
    for i, ch in enumerate(text):
        if ch == "[":
            lst = _balanced_list(text, i)
            if lst:
                candidates.append(lst)
            break
    last_err: Optional[Exception] = None
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError as exc:
            last_err = exc
            continue
        if isinstance(data, dict) and isinstance(data.get("activities"), list):
            data = data["activities"]
        if not isinstance(data, list):
            last_err = ParseError("JSON is not a list")
            continue
        return [_check_box(b) for b in data]
    raise ParseError(f"no JSON list in response ({last_err})")


def _check_box(b: Any) -> Dict[str, Any]:
    if not isinstance(b, dict) or "box_2d" not in b:
        raise ParseError(f"item is not a box object: {b!r}")
    box = b["box_2d"]
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        raise ParseError(f"box_2d is not four numbers: {box!r}")
    try:
        y0, x0, y1, x1 = [float(v) for v in box]
    except (TypeError, ValueError):
        raise ParseError(f"box_2d is not numeric: {box!r}")
    if not all(0 <= v <= 1000 for v in (y0, x0, y1, x1)):
        raise ParseError(f"box_2d outside 0-1000: {box!r}")
    if y1 <= y0 or x1 <= x0:
        raise ParseError(f"box_2d is empty or inverted: {box!r}")
    label = b.get("label")
    if label is not None:
        label = str(label).strip() or None
    return {"box_2d": [y0, x0, y1, x1], "label": label}


def box_to_pixels(box_2d: Sequence[float], width_px: int, height_px: int) -> List[float]:
    y0, x0, y1, x1 = box_2d
    return [round(x0 * width_px / 1000, 1), round(y0 * height_px / 1000, 1),
            round(x1 * width_px / 1000, 1), round(y1 * height_px / 1000, 1)]


def pixels_to_points(px: Sequence[float], index: Dict[str, Any]) -> List[float]:
    """Pixel rect (y down) -> PDF points [x0, y0, x1, y1] with y up, the regions.json convention."""
    sx = index["width_px"] / index["width_pt"]
    sy = index["height_px"] / index["height_pt"]
    x0, y0, x1, y1 = px
    h = index["height_pt"]
    return [round(x0 / sx, 3), round(h - y1 / sy, 3), round(x1 / sx, 3), round(h - y0 / sy, 3)]


def convert_boxes(boxes: List[Dict[str, Any]], index: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for b in boxes:
        px = box_to_pixels(b["box_2d"], index["width_px"], index["height_px"])
        out.append({"label": b["label"], "box_2d": b["box_2d"], "px": px, "rect": pixels_to_points(px, index)})
    return out


# --------------------------------------------------------------------------- #
# The Gemini API with free-tier keys                                          #
# --------------------------------------------------------------------------- #

def load_api_keys(path: Path = DEFAULT_KEYS_FILE) -> List[str]:
    """One key per line; blank lines and `#` comments ignored. The values are never printed."""
    if not path.is_file():
        raise FileNotFoundError(f"no key file at {path}")
    keys = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            keys.append(line)
    if not keys:
        raise ValueError(f"{path} holds no keys")
    return keys


class KeyPool:
    """
    Round-robin over projects, each with its own requests-per-minute pacing.

    `acquire()` blocks until some key may send, preferring the one idle
    longest. A 429 that names the daily quota retires the key for the run;
    any other 429 puts it on cooldown for the delay the error suggests.
    """

    def __init__(self, keys: Sequence[str], rpm: float, stop: Optional[threading.Event] = None):
        self.keys = list(keys)
        self.interval = 60.0 / max(rpm, 0.01)
        self.next_ok = [0.0] * len(self.keys)
        self.spent = [False] * len(self.keys)
        self.sent = [0] * len(self.keys)
        self.cv = threading.Condition()
        self.stop = stop
        self._clients: Dict[int, Any] = {}

    def client(self, i: int):
        if i not in self._clients:
            from google import genai
            self._clients[i] = genai.Client(api_key=self.keys[i])
        return self._clients[i]

    def acquire(self, timeout: float = 600.0) -> Optional[int]:
        deadline = time.time() + timeout
        with self.cv:
            while True:
                live = [i for i in range(len(self.keys)) if not self.spent[i]]
                if not live:
                    return None
                now = time.time()
                i = min(live, key=lambda k: self.next_ok[k])
                if self.next_ok[i] <= now:
                    self.next_ok[i] = now + self.interval
                    self.sent[i] += 1
                    return i
                if now >= deadline or (self.stop is not None and self.stop.is_set()):
                    return None
                self.cv.wait(timeout=min(self.next_ok[i] - now, deadline - now, 5.0))

    def cooldown(self, i: int, seconds: float) -> None:
        with self.cv:
            self.next_ok[i] = max(self.next_ok[i], time.time() + seconds)
            self.cv.notify_all()

    def retire(self, i: int) -> None:
        with self.cv:
            self.spent[i] = True
            self.cv.notify_all()

    @property
    def live(self) -> int:
        return sum(1 for s in self.spent if not s)


DAILY_RE = re.compile(r"per.?day|daily|PerDay|RequestsPerDay|GenerateRequestsPerDay", re.I)
RETRY_RE = re.compile(r"retry(?:Delay|.?in|.?after)\D{0,10}(\d+(?:\.\d+)?)\s*s", re.I)


def classify_api_error(exc: Exception) -> Tuple[str, float]:
    """('daily' | 'rate' | 'transient' | 'bad-request' | 'error', suggested wait in seconds)."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    msg = str(getattr(exc, "message", None) or exc)
    # The per-day quota id ("GenerateRequestsPerDayPerProjectPerModel-FreeTier") is
    # only in the structured details, never in the message text, so look there too.
    blob = msg + " " + json.dumps(getattr(exc, "details", None) or {}, default=str)
    m = RETRY_RE.search(msg)
    wait = float(m.group(1)) if m else 30.0
    if code == 429 or "RESOURCE_EXHAUSTED" in msg:
        return ("daily", 0.0) if DAILY_RE.search(blob) else ("rate", wait)
    if code in (500, 502, 503, 504) or TRANSIENT_RE.search(msg):
        return "transient", wait
    if code in (400, 404):
        return "bad-request", 0.0
    return "error", wait


def call_api(pool: KeyPool, key_index: int, model: str, prompt: str, jpeg: Path, timeout: int,
             schema: bool = True) -> Tuple[str, Dict[str, Any], float]:
    """One generate_content request. Returns (text, usage, seconds). Raises the SDK's APIError."""
    from google.genai import types
    client = pool.client(key_index)
    cfg: Dict[str, Any] = {
        "response_mime_type": "application/json",
        "thinking_config": types.ThinkingConfig(thinking_level=THINKING_LEVEL),
        "temperature": 0.0,
        "http_options": types.HttpOptions(timeout=int(timeout * 1000)),
    }
    if schema:
        cfg["response_json_schema"] = API_SCHEMA
    t0 = time.time()
    resp = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=jpeg.read_bytes(), mime_type="image/jpeg"), prompt],
        config=types.GenerateContentConfig(**cfg),
    )
    dt = time.time() - t0
    um = getattr(resp, "usage_metadata", None)
    usage = {
        "input_tokens": getattr(um, "prompt_token_count", None),
        "output_tokens": getattr(um, "candidates_token_count", None),
        "thinking_tokens": getattr(um, "thoughts_token_count", None),
        "cache_read_tokens": getattr(um, "cached_content_token_count", None),
        "total_tokens": getattr(um, "total_token_count", None),
    }
    return resp.text or "", usage, dt


def label_page(task: Dict[str, Any]) -> Dict[str, Any]:
    """Ask the teacher about one page and write its label file. Never raises for one page's sake."""
    book_id, page = task["book_id"], int(task["page"])
    out_dir = Path(task["out_dir"])
    dest = label_path(out_dir, book_id, page)
    fail = failed_path(out_dir, book_id, page)
    res: Dict[str, Any] = {"book_id": book_id, "page": page, "status": "failed", "requests": 0}
    if dest.is_file():
        res["status"] = "skipped"
        return res
    index = task["index"]
    jpeg = PAGES_DIR / book_id / f"{page:04d}.jpg"
    if not jpeg.is_file():
        res["error"] = f"missing {jpeg}"
        return res
    pool: KeyPool = task["pool"]
    stop: threading.Event = task["stop"]
    hint = hint_line(task.get("hint_count")) if task.get("hint") else None
    prompt = build_prompt(task["prompt_body"], hint)
    retries = int(task.get("retries", DEFAULT_RETRIES))
    schema = True
    last_error = ""
    last_raw: Optional[str] = None
    attempt = 0        # attempts that reached the model and came back unusable
    hiccups = 0        # rate/transient bounces that are nobody's fault
    while attempt <= retries and hiccups < 12:
        if stop.is_set():
            res.update(status="stopped", error="stopped")
            return res
        k = pool.acquire()
        if k is None:
            res.update(status="quota", error="every key is spent for today (daily quota)")
            return res
        entry = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "book": book_id, "page": page,
                 "route": "api", "key": k, "model": task["model"], "version": task["version"],
                 "out": out_dir.name, "attempt": attempt + 1}
        try:
            text, usage, dt = call_api(pool, k, task["model"], prompt, jpeg, task["timeout"], schema)
        except Exception as exc:  # noqa: BLE001  (google.genai.errors.APIError and friends)
            res["requests"] += 1
            kind, wait = classify_api_error(exc)
            msg = str(getattr(exc, "message", None) or exc)[:300]
            log_request({**entry, "status": kind, "error": msg[:200]})
            if kind == "daily":
                pool.retire(k)
                hiccups += 1
                continue
            if kind in ("rate", "transient"):
                pool.cooldown(k, wait)
                hiccups += 1
                continue
            if kind == "bad-request" and schema:
                schema = False          # the schema was rejected: ask again without it, once
                last_error = f"schema rejected: {msg}"
                continue
            last_error = msg
            attempt += 1
            continue
        res["requests"] += 1
        last_raw = text
        try:
            boxes = parse_boxes(text)
        except ParseError as exc:
            last_error = str(exc)
            log_request({**entry, "status": "parse-error", "seconds": round(dt, 1), "usage": usage,
                         "error": last_error[:200], "raw": str(text)[:600]})
            attempt += 1
            continue
        log_request({**entry, "status": "ok", "seconds": round(dt, 1), "usage": usage, "boxes": len(boxes)})
        write_json_atomic(dest, {
            "book": book_id, "page": page, "route": "api", "model": task["model"], "thinking_level": THINKING_LEVEL,
            "prompt_version": task["version"], "prompt_sha": task["prompt_sha"], "hint": hint,
            "created": entry["ts"], "duration_s": round(dt, 1), "attempts": attempt + 1, "key": k,
            "usage": usage, "conversation_id": None, "index": index, "boxes": convert_boxes(boxes, index),
            "raw_response": text, "schema": schema,
        })
        if fail.exists():
            fail.unlink()
        res.update(status="ok", boxes=len(boxes), seconds=round(dt, 1), usage=usage)
        return res
    fail.parent.mkdir(parents=True, exist_ok=True)
    fail.write_text(last_error + "\n" + (("--- last reply ---\n" + str(last_raw)[:4000] + "\n") if last_raw else ""),
                    encoding="utf-8")
    res["error"] = last_error or "gave up after repeated rate limits"
    return res


# --------------------------------------------------------------------------- #
# Paths and bookkeeping                                                       #
# --------------------------------------------------------------------------- #

def label_path(out_dir: Path, book_id: str, page: int) -> Path:
    return out_dir / book_id / f"{page:04d}.json"


def failed_path(out_dir: Path, book_id: str, page: int) -> Path:
    return out_dir / book_id / f"{page:04d}.failed"


def write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


_log_lock = threading.Lock()


def log_request(entry: Dict[str, Any], log_path: Path = REQUEST_LOG) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with _log_lock:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def requests_today(log_path: Path = REQUEST_LOG, day: Optional[str] = None) -> int:
    """API requests logged on `day` (local calendar date, default today)."""
    return sum(requests_today_by_key(log_path, day).values())


def requests_today_by_key(log_path: Path = REQUEST_LOG, day: Optional[str] = None) -> Dict[Any, int]:
    """Same count split by key index. Lines from other routes (older logs) are not counted."""
    day = day or datetime.now().strftime("%Y-%m-%d")
    out: Dict[Any, int] = {}
    if not log_path.is_file():
        return out
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            if not (line.startswith('{"ts": "' + day) or f'"ts": "{day}' in line[:40]):
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("route") != "api":
                continue
            k = e.get("key")
            out[k] = out.get(k, 0) + 1
    return out


# --------------------------------------------------------------------------- #
# Selection helpers                                                           #
# --------------------------------------------------------------------------- #

def load_selection(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def selected_pages(selection: Dict[str, Any], only: Optional[Sequence[str]] = None,
                   pages: Optional[Sequence[int]] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for book_id in sorted(selection["books"]):
        if only and not any(w and w.lower() in book_id.lower() for w in only):
            continue
        for rec in selection["books"][book_id]["pages"]:
            if pages and int(rec["page"]) not in pages:
                continue
            out.append({"book_id": book_id, **rec})
    return out


def sample_pages(pages: List[Dict[str, Any]], fraction: float, seed: int) -> List[Dict[str, Any]]:
    """A seeded random fraction of the selection, at least one page, for the agreement check."""
    rng = random.Random(f"{seed}:agree")
    order = sorted(pages, key=lambda r: (r["book_id"], int(r["page"])))
    k = max(1, int(round(len(order) * fraction)))
    picked = rng.sample(order, k)
    return sorted(picked, key=lambda r: (r["book_id"], int(r["page"])))


def index_for(book_id: str, page: int, cache: Dict[str, Dict[int, Dict[str, Any]]]) -> Dict[str, Any]:
    if book_id not in cache:
        cache[book_id] = {int(r["page"]): r for r in read_index(book_id, PAGES_DIR)}
    return cache[book_id][page]


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selection", default=str(DEFAULT_SELECTION))
    ap.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--keys", default=str(DEFAULT_KEYS_FILE), help="key file, one key per line")
    ap.add_argument("--rpm", type=float, default=DEFAULT_RPM, help="requests per minute per key")
    ap.add_argument("--out-version", default=None, help="label folder name (default: the prompt version)")
    ap.add_argument("--only", default=None, help="comma-separated book id prefixes")
    ap.add_argument("--pages", default=None, help="comma-separated page numbers (with --only)")
    ap.add_argument("--limit", type=int, default=None, help="label at most this many pages this run")
    ap.add_argument("--sample", type=float, default=None, help="label a seeded random fraction of the selection")
    ap.add_argument("--seed", type=int, default=20260928)
    ap.add_argument("--hint", action="store_true", help="add the manifest-count hint line where the selection carries hint_count")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    ap.add_argument("--dry-run", action="store_true", help="list what would be requested and exit")
    args = ap.parse_args(argv)

    version, body, sha = load_prompt(Path(args.prompt))
    out_dir = LABELS_DIR / "api" / (args.out_version or version)
    selection = load_selection(Path(args.selection))
    pages = selected_pages(selection, args.only.split(",") if args.only else None,
                           [int(p) for p in args.pages.split(",")] if args.pages else None)
    if args.sample:
        pages = sample_pages(pages, args.sample, args.seed)
    index_cache: Dict[str, Any] = {}

    todo = [p for p in pages if not label_path(out_dir, p["book_id"], int(p["page"])).is_file()]
    done_before = len(pages) - len(todo)
    if args.limit is not None:
        todo = todo[:args.limit]
    stop = threading.Event()
    keys = load_api_keys(Path(args.keys))
    pool = KeyPool(keys, args.rpm, stop)
    by_key = requests_today_by_key()
    print(f"prompt {version} ({sha}), model {args.model} (thinking {THINKING_LEVEL}), "
          f"{len(keys)} key(s) at {args.rpm:g}/min each -> {out_dir}")
    print(f"{len(pages)} selected, {done_before} labelled already, {len(todo)} to request now "
          f"| {sum(by_key.values())} request(s) used today, per key {[by_key.get(i, 0) for i in range(len(keys))]} "
          f"| {args.workers} worker(s), timeout {args.timeout} s")
    if args.dry_run or not todo:
        for p in todo[:20]:
            print(f"  {p['book_id'][:8]} p{int(p['page']):4d} {p.get('stratum', '')}")
        return 0

    tasks = []
    for p in todo:
        tasks.append({
            "book_id": p["book_id"], "page": int(p["page"]), "out_dir": str(out_dir),
            "index": index_for(p["book_id"], int(p["page"]), index_cache),
            "prompt_body": body, "version": version, "prompt_sha": sha, "model": args.model,
            "timeout": args.timeout, "retries": args.retries, "hint": args.hint,
            "hint_count": p.get("hint_count"), "stop": stop, "pool": pool,
        })

    t0 = time.time()
    counts = {"ok": 0, "failed": 0, "skipped": 0, "quota": 0, "stopped": 0}
    requests = 0
    boxes = 0
    quota_msg = None
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(label_page, t): t for t in tasks}
        try:
            for fut in as_completed(futures):
                res = fut.result()
                counts[res["status"]] = counts.get(res["status"], 0) + 1
                requests += res.get("requests", 0)
                boxes += res.get("boxes", 0)
                mark = {"ok": "✓", "skipped": "-", "failed": "✗", "quota": "■", "stopped": "·"}.get(res["status"], "?")
                extra = (f"{res.get('boxes', 0):2d} boxes, {res.get('seconds', 0):5.1f}s" if res["status"] == "ok"
                         else str(res.get("error", ""))[:120])
                print(f"  {mark} {res['book_id'][:8]} p{res['page']:4d} | {extra}", flush=True)
                if res["status"] == "quota" and not stop.is_set():
                    stop.set()
                    quota_msg = res.get("error")
        except KeyboardInterrupt:
            stop.set()
            print("interrupted; letting running requests finish")
            raise
    wall = time.time() - t0
    remaining = sum(1 for p in pages if not label_path(out_dir, p["book_id"], int(p["page"])).is_file())
    by_key = requests_today_by_key()
    print("=" * 72)
    print(f"{counts['ok']} labelled ({boxes} boxes), {counts['failed']} failed, {counts['skipped']} skipped, "
          f"{requests} request(s) in {wall:.0f}s ({wall / max(counts['ok'], 1):.0f} s/page); "
          f"{sum(by_key.values())} used today; {remaining} of {len(pages)} still unlabelled")
    print(f"per key today: {[by_key.get(i, 0) for i in range(len(pool.keys))]}; keys spent: "
          f"{[i for i, sp in enumerate(pool.spent) if sp]}")
    if stop.is_set() and quota_msg is not None:
        print(f"QUOTA STOP: {quota_msg}\nRe-run the same command tomorrow to continue.")
        return 3
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
