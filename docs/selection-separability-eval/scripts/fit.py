"""Issue #32: fit/select on dev + masked ONLY (PROTOCOL.md §2, §6).
Writes results/frozen_models.json (then hashed). Never reads heldout/cb rows."""
import hashlib, json, os
from common import (ALL_FEATURES, FIXED_COMBOS, RES, eligible, fit_lr, fixed_scorers,
                    load_rows, model_features, per_task)

FIT_SETS = ('dev', 'masked')


def main():
    tasks = {k: v for k, v in load_rows().items() if k[0] in FIT_SETS}
    el = {s: [v for k, v in tasks.items() if k[0] == s and eligible(v)] for s in FIT_SETS}
    out = {'fit_sets': FIT_SETS, 'eligible_tasks': {s: len(v) for s, v in el.items()}}
    # single-feature orientation + selection
    single = {}
    for f in ALL_FEATURES:
        m = [float(per_task(el[s], lambda r, f=f: r['x'][f])['auc'].mean()) for s in FIT_SETS]
        mean = sum(m) / 2
        sign = -1 if mean < 0.5 else 1
        single[f] = {'dev': m[0], 'masked': m[1], 'mean': mean, 'sign': sign,
                     'oriented_mean': mean if sign == 1 else 1 - mean}
    out['single'] = single
    out['best_single'] = max(ALL_FEATURES, key=lambda f: (single[f]['oriented_mean'], f))
    fx = fixed_scorers()
    fixed = {c: {s: float(per_task(el[s], fx[c])['auc'].mean()) for s in FIT_SETS} for c in FIXED_COMBOS}
    for c in fixed:
        fixed[c]['mean'] = (fixed[c]['dev'] + fixed[c]['masked']) / 2
    out['fixed'] = fixed
    out['best_fixed'] = max(FIXED_COMBOS, key=lambda c: (fixed[c]['mean'], c))
    rows = [r for s in FIT_SETS for t in el[s] for r in t]
    out['models'] = {name: fit_lr(rows, feats) for name, feats in model_features().items()}
    p = os.path.join(RES, 'frozen_models.json')
    json.dump(out, open(p, 'w'), indent=1)
    h = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    open(p + '.sha256', 'w').write(f'{h}  frozen_models.json\n')
    print('eligible', out['eligible_tasks'], 'best_single', out['best_single'], 'best_fixed', out['best_fixed'])
    print('converged', {k: v['converged'] for k, v in out['models'].items()})
    print('sha256', h)


if __name__ == '__main__':
    main()
