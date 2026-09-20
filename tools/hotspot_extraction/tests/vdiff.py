import json, sys
base = json.load(open('converter/tests/scorecard.json'))
now  = json.load(open(sys.argv[1]))
b = {x['book']: x for x in base['books']}
keys = ['panelsCut','solutionsCut','tall','slivers','overlaps']
tot = {k: [0,0,0] for k in keys}
print(f"{'book':14} {'rule':14} {'was':>5} {'now':>5} {'d':>5} {'anch':>5}")
for r in now['books']:
    if 'violations' not in r: continue
    was = b.get(r['book'], {}).get('violations', {})
    fa = r.get('violationsFromAnchors', {})
    for k in keys:
        w, n, a = was.get(k,0), r['violations'][k], fa.get(k,0)
        tot[k][0]+=w; tot[k][1]+=n; tot[k][2]+=a
        if n != w:
            print(f"{r['book'][:12]:14} {k:14} {w:5} {n:5} {n-w:+5} {a:5}")
print()
print(f"{'TOTAL':14} {'rule':14} {'was':>5} {'now':>5} {'d':>5} {'anch':>5}")
for k in keys:
    w,n,a = tot[k]
    print(f"{'':14} {k:14} {w:5} {n:5} {n-w:+5} {a:5}")
print()
print(f"{'book':14} {'regions':>8} {'anchored?':>10} {'matched':>8} {'was':>5}")
for r in now['books']:
    if 'join' not in r: continue
    was = b.get(r['book'], {}).get('join', {})
    print(f"{r['book'][:12]:14} {r['yield']['regions']:8} {r['join'].get('anchored',0):10} {r['join']['matched']:8} {was.get('matched',0):5}")
