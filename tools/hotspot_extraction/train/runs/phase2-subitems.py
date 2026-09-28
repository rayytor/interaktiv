"""Phase 2 (cloud run): replay one book under a profile, print region / sub-item / label-kind counts as JSON.

Usage: python3 tools/hotspot_extraction/train/runs/phase2-subitems.py <book-id> <profile.json | defaults>
"""
import collections, json, re, sys, os
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
sys.path.insert(0, ROOT)
import pymupdf; pymupdf.TOOLS.set_low_memory(True)
from tools.hotspot_extraction.scanner.profile import load_profile, apply_profile, DEFAULTS
from tools.hotspot_extraction.train import cache as cache_mod
from tools.hotspot_extraction.train.objective import _replay_pages

book, prof = sys.argv[1], sys.argv[2]
apply_profile(DEFAULTS if prof == "defaults" else load_profile(prof))
shard = cache_mod.load_shard(cache_mod.shard_path(book))
regions, items, labels, per_page = 0, 0, collections.Counter(), {}
for page, w, h, acts, diag, anchors in _replay_pages(shard.pages):
    per_page[page] = [(a.get("label"), len(a.get("items", []))) for a in acts]
    for a in acts:
        regions += 1
        items += len(a.get("items", []))
        lab = a.get("label")
        kind = "none" if lab is None else ("digit" if re.fullmatch(r"\d+", str(lab).strip(" .)")) else "letter" if re.fullmatch(r"[A-Za-z]", str(lab).strip(" .)")) else "other")
        labels[kind] += 1
print(json.dumps({"book": book, "profile": prof, "regions": regions, "items": items,
                  "labelKinds": dict(labels), "perPage": per_page}))
