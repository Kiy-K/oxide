"""NON-PREREGISTERED diagnostic (explanatory only, never gating): rank-band
cells on the artifact-free universe (no modules / test files), to check
whether in-band LR gains are the commit-gold label-construction artifact
named a priori in PROTOCOL §5. Uses the same frozen scorers."""
import json, os
from common import RES, delta, load_rows
from evaluate import cell, labeled, load_frozen, scorers
fr, _ = load_frozen(); sc = scorers(fr); tasks = load_rows(); out = {}
names = ['fused_order', 'LR-R', 'LR-RS', 'LR-ALL', 'best_single']
for s in ['dev', 'masked', 'heldout']:
    tl = labeled(tasks, s)
    for b in ['B1', 'B2', 'B3']:
        for tag, filt in [('full', lambda r: True), ('artifact_free', lambda r: not r['x']['is_module'] and not r['x']['is_test'])]:
            c, st, el = cell([[r for r in t if r['band'] == b and filt(r)] for t in tl], names, sc)
            if not st: continue
            pts, idx = st
            mods = int(sum(r['x']['is_module'] or r['x']['is_test'] for t in el for r in t))
            out[f'{s}/{b}/{tag}'] = {'n': c['n'], 'pos': c['pos'], 'neg': c['neg'], 'module_or_test_cands': mods,
                **{n: c['scorers'][n]['auroc'] for n in names},
                **{f'd {n} vs fused': delta(pts[n], pts['fused_order'], idx) for n in names[1:]}}
            d = out[f'{s}/{b}/{tag}']
            print(f"{s:8s} {b} {tag:13s} n={d['n']:2d} pos/neg={d['pos']}/{d['neg']} mod/test={mods:3d} fused={d['fused_order']:.3f} LR-R={d['LR-R']:.3f} LR-RS={d['LR-RS']:.3f} LR-ALL={d['LR-ALL']:.3f}  dALL={d['d LR-ALL vs fused']['d_auroc']:+.3f} [{d['d LR-ALL vs fused']['d_auroc_ci'][0]:+.3f},{d['d LR-ALL vs fused']['d_auroc_ci'][1]:+.3f}] dR={d['d LR-R vs fused']['d_auroc']:+.3f} [{d['d LR-R vs fused']['d_auroc_ci'][0]:+.3f},{d['d LR-R vs fused']['d_auroc_ci'][1]:+.3f}]")
json.dump(out, open(os.path.join(RES, 'diag_bands_artifact_free.json'), 'w'), indent=1)
