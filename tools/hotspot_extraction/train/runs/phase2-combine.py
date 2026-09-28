"""Phase 2 (cloud run): combine fits A and B (same-sign knobs, value from A), score defaults vs combined on TRAIN only.

Usage: python3 tools/hotspot_extraction/train/runs/phase2-combine.py <workers>
"""
import dataclasses, json, os, sys
from dataclasses import asdict
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
sys.path.insert(0, ROOT)
from tools.hotspot_extraction.scanner.profile import DEFAULTS, BY_KEY, from_dict, save_profile, profile_hash, changed_from_default, load_profile
from tools.hotspot_extraction.train import splits as splits_mod
from tools.hotspot_extraction.train.objective import ChunkPool
from tools.hotspot_extraction.train.fit import _report, _compare, _report_gates

def main():
    R = os.path.join(ROOT, "tools/hotspot_extraction/train/runs/")
    workers = int(sys.argv[1])
    a = json.load(open(R + "phase2-fit-a.json"))["bestChanged"]
    b = json.load(open(R + "phase2-fit-b.json"))["bestChanged"]
    keep, dropped = {}, {}
    for k in sorted(set(a) | set(b)):
        d = getattr(DEFAULTS, k)
        sa = (a[k] > d) - (a[k] < d) if k in a else 0
        sb = (b[k] > d) - (b[k] < d) if k in b else 0
        if sa and sa == sb:
            keep[k] = a[k]
        else:
            dropped[k] = {"default": d, "a": a.get(k), "b": b.get(k)}
    print("default / A / B for every moved knob:")
    for k in sorted(set(a) | set(b)):
        print(f"  {'KEEP' if k in keep else 'drop'}  {k:<24} {getattr(DEFAULTS, k)!r:>10}  A={a.get(k)!r:>10}  B={b.get(k)!r:>10}")
    combined = from_dict(keep)
    h = save_profile(combined, R + "phase2-profile-combined.json",
                     note="phase 2 combined: knobs moved the same way in fits A and B (value from A)")
    print(f"\nwrote phase2-profile-combined.json profile {h}, {len(changed_from_default(combined))} knobs moved")
    assert load_profile(R + "phase2-profile-combined.json") == combined

    train = splits_mod.train_ids()
    splits_mod.assert_trainable(train)
    with ChunkPool(workers=workers) as pool:
        d = pool.evaluate(DEFAULTS, train)
        c = pool.evaluate(combined, train)
    print("  " + _report("train d", d.totals()))
    print("  " + _report("combined", c.totals()))
    fails = c.gates(d)
    print(_compare(d.totals(), c.totals(), d.books, c.books))
    print(_report_gates(fails))
    rows = lambda ev: [dict(asdict(x), loss=round(x.loss(), 6)) for x in ev.books]
    json.dump({"kept": keep, "dropped": dropped, "profileHash": h,
               "trainDefaults": {"totals": d.totals(), "books": rows(d)},
               "trainCombined": {"totals": c.totals(), "books": rows(c)},
               "gates": fails}, open(R + "phase2-combine.json", "w"), indent=2)
    print("GATES_FAILED" if fails else "GATES_OK")

if __name__ == "__main__":
    main()
