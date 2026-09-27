"""Final-pack evaluation: challenger arm vs ch0 (primary evidence).

usage: eval_pack.py <set>[,<set>...] <tag> [--base ch0] [--json out] [--quiet]
  reads dumps/<set>-<base>.jsonl (A) and dumps/<set>-<tag>.jsonl (B), gold/<set>.json.
Metrics follow validate_ch5.py: gold-line coverage over delivered snippet spans,
relevant tokens = chars of delivered gold lines / 4, efficiency = rel / used.
Paired bootstrap over tasks, 10,000 resamples, seed 0, 95 % percentile intervals;
relative efficiency = (Σ eff_B − Σ eff_A) / Σ eff_A.
"""
import json, os, random, sys
from collections import defaultdict
from pathlib import Path

J = os.path.expanduser('~/.cache/oxide-julia-eval')
sets, tag = sys.argv[1].split(','), sys.argv[2]
base = sys.argv[sys.argv.index('--base') + 1] if '--base' in sys.argv else 'ch0'
OUT = sys.argv[sys.argv.index('--json') + 1] if '--json' in sys.argv else None
B, SEED, CPT = 10_000, 0, 4.0
fkey = lambda k: k.split('#', 1)[0]
span = lambda sp: set(range(sp[0], sp[1] + 1)) if sp and sp[0] > 0 else set()
_files = {}


def flines(root, f):
    if (root, f) not in _files:
        try:
            _files[(root, f)] = (Path(root) / f).read_text(errors='replace').splitlines()
        except OSError:
            _files[(root, f)] = []
    return _files[(root, f)]


def metrics(rec, path, gl):
    deliv = defaultdict(set)
    for p in rec['trace']['packed']:
        deliv[fkey(p['key'])] |= span(p['span'])
    hit = {f: deliv.get(f, set()) & v for f, v in gl.items()}
    tot = sum(len(v) for v in gl.values())
    fl = lambda f, ls: sum(len(flines(path, f)[i - 1]) + 1 for i in ls if 0 < i <= len(flines(path, f)))
    rel = sum(fl(f, s) for f, s in hit.items()) / CPT
    return dict(cov=sum(len(s) for s in hit.values()) / tot, rel=rel, used=rec['used'],
                eff=rel / rec['used'] if rec['used'] else 0.0, items=len(rec['trace']['packed']),
                files=len({fkey(p['key']) for p in rec['trace']['packed']}))


def pct(xs, q):
    return xs[min(len(xs) - 1, int(q * len(xs)))]


M, changes, repo = {}, [], {}
for s in sets:
    gold = json.load(open(f'{J}/gold/{s}.json'))
    tasks = {t['id']: t for t in map(json.loads, open(f'{J}/tasks/{s}.jsonl'))}
    A = {r['id']: r for r in map(json.loads, open(f'{J}/dumps/{s}-{base}.jsonl'))}
    Bd = {r['id']: r for r in map(json.loads, open(f'{J}/dumps/{s}-{tag}.jsonl'))}
    for i in sorted(A):
        gl = {f: set(v) for f, v in gold.get(i, {}).get('lines', {}).items()}
        if not gl or i not in Bd:
            continue
        k = f'{s}:{i}'; repo[k] = tasks[i].get('repo', s)
        M[k] = (metrics(A[i], tasks[i]['path'], gl), metrics(Bd[i], tasks[i]['path'], gl))
        pa = {p['key']: p for p in A[i]['trace']['packed']}; pb = {p['key']: p for p in Bd[i]['trace']['packed']}
        g = lambda p: bool(span(p['span']) & gl.get(fkey(p['key']), set()))
        # gold-bearing packed items: rescued (in B only) / suppressed (in A only)
        for key in pb.keys() - pa.keys():
            changes.append(dict(task=k, kind='added', key=key, gold=g(pb[key]), tok=pb[key]['est'], role=pb[key]['role']))
        for key in pa.keys() - pb.keys():
            changes.append(dict(task=k, kind='removed', key=key, gold=g(pa[key]), tok=pa[key]['est'], role=pa[key]['role'],
                                why=dict(map(tuple, Bd[i]['trace']['omitted'])).get(key)))
        for key in pa.keys() & pb.keys():  # same symbol, different delivered window
            if pa[key]['span'] != pb[key]['span']:
                changes.append(dict(task=k, kind='respan', key=key, gold=g(pb[key]), tok=pb[key]['est'] - pa[key]['est']))

ids = sorted(M); n = len(ids)
agg = {arm: {m: sum(M[i][a][m] for i in ids) / n for m in ('cov', 'rel', 'used', 'eff', 'items', 'files')}
       for a, arm in ((0, 'A'), (1, 'B'))}
d_cov = [M[i][1]['cov'] - M[i][0]['cov'] for i in ids]
e0 = [M[i][0]['eff'] for i in ids]; e1 = [M[i][1]['eff'] for i in ids]
rel_eff = (sum(e1) - sum(e0)) / sum(e0) if sum(e0) else 0.0
rnd = random.Random(SEED); bc, br = [], []
for _ in range(B):
    smp = [rnd.randrange(n) for _ in range(n)]
    bc.append(sum(d_cov[j] for j in smp) / n)
    s0 = sum(e0[j] for j in smp); br.append((sum(e1[j] for j in smp) - s0) / s0 if s0 else 0.0)
bc.sort(); br.sort()
imp = [i for i in ids if M[i][1]['cov'] > M[i][0]['cov'] + 1e-12]
reg = [i for i in ids if M[i][1]['cov'] < M[i][0]['cov'] - 1e-12]
changed = sorted({c['task'] for c in changes})
res = dict(n=n, A=agg['A'], B=agg['B'], d_cov=[sum(d_cov) / n, pct(bc, .025), pct(bc, .975)],
           rel_eff=[rel_eff, pct(br, .025), pct(br, .975)], improved=len(imp), regressed=len(reg),
           unchanged=n - len(imp) - len(reg), pack_changed=len(changed),
           eff_down=sum(M[i][1]['eff'] < M[i][0]['eff'] - 1e-12 for i in ids),
           rescued_gold=sum(c['kind'] == 'added' and c['gold'] for c in changes),
           suppressed_gold=sum(c['kind'] == 'removed' and c['gold'] for c in changes),
           fp_tokens_added=sum(c['tok'] for c in changes if c['kind'] == 'added' and not c['gold']),
           tokens_added=sum(c['tok'] for c in changes if c['kind'] == 'added'),
           tokens_removed=sum(c['tok'] for c in changes if c['kind'] == 'removed'),
           rel_over_budget=[agg['A']['rel'] / 4096, agg['B']['rel'] / 4096])
if '--quiet' not in sys.argv:
    print(f"{','.join(sets)} {tag}: n={n} cov A {res['A']['cov']:.4f} B {res['B']['cov']:.4f} "
          f"Δcov {res['d_cov'][0]:+.4f} [{res['d_cov'][1]:+.4f},{res['d_cov'][2]:+.4f}]  "
          f"rel.eff {res['rel_eff'][0]:+.1%} [{res['rel_eff'][1]:+.1%},{res['rel_eff'][2]:+.1%}]  "
          f"eff A {res['A']['eff']:.4f} B {res['B']['eff']:.4f}  used A {res['A']['used']:.0f} B {res['B']['used']:.0f}  "
          f"imp/unch/reg {res['improved']}/{res['unchanged']}/{res['regressed']}  changed {res['pack_changed']}  "
          f"rescued {res['rescued_gold']} suppressed {res['suppressed_gold']}  FP tok +{res['fp_tokens_added']}")
if OUT:
    json.dump(dict(summary=res, metrics=M, changes=changes, improved=imp, regressed=reg, repo=repo), open(OUT, 'w'), indent=1)
