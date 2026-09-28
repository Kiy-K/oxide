"""Shared scorers and within-task metrics (PROTOCOL.md §6-7)."""
import gzip, hashlib, json, os, random
import numpy as np
from scipy.stats import rankdata

from features import FEATURES, REL_W

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, '..', 'results')
ALL_FEATURES = [f for fam in 'RSIC' for f in FEATURES[fam]]
FAMILY_SETS = {'LR-R': 'R', 'LR-S': 'S', 'LR-I': 'I', 'LR-C': 'C', 'LR-RS': 'RS', 'LR-ALL': 'RSIC'}
STRUCT_SUB = {
    'LR-R+reltype': ['rel_uses', 'rel_imported_definition', 'rel_parent', 'rel_child',
                     'rel_sibling', 'rel_test', 'rel_weight'],
    'LR-R+distance': ['rel_distance'],
    'LR-R+seedrank': ['best_seed_rank', 'link_top1'],
    'LR-R+degree': ['n_seed_links', 'seed_fanout'],
}


def model_features():
    m = {name: [f for fam in fams for f in FEATURES[fam]] for name, fams in FAMILY_SETS.items()}
    for name, extra in STRUCT_SUB.items():
        m[name] = FEATURES['R'] + extra
    return m


def load_rows():
    tasks = {}
    for l in gzip.open(os.path.join(RES, 'rows.jsonl.gz'), 'rt'):
        r = json.loads(l)
        tasks.setdefault((r['set'], r['task']), []).append(r)
    return tasks


# ---------------------------------------------------------------- scorers
def fixed_scorers():
    def fx4(r):
        a = r['aux']
        return 1 / (60 + a['fr']) + sum(0.5 * REL_W.get(rel, 0.0) / (60 + j) for j, rel in a['links'])
    return {
        'fused_order': lambda r: r['x']['fused_rank'],
        'pool_order': lambda r: max(r['aux']['fscore'], 0.4 * r['aux']['best_link_seed_score']),
        'lexical_only': lambda r: r['x']['lex_rank'] + 1e-3 * r['x']['sem_rank'],
        'semantic_only': lambda r: r['x']['sem_rank'] + 1e-3 * r['x']['lex_rank'],
        'rrf_baseline': lambda r: r['x']['rrf_lex'] + r['x']['rrf_sem'],
        'rel_only_weight': lambda r: r['x']['rel_weight'],
        'rel_only_links': lambda r: r['x']['n_seed_links'],
        'rel_only_seedrank': lambda r: r['x']['best_seed_rank'],
        'FX1_best_channel_rr': lambda r: max(1 / (60 + r['aux']['lr']), 1 / (60 + r['aux']['sr'])),
        'FX2_lexical_first': lambda r: r['x']['lex_rank'] + 1e-3 * r['x']['fused_rank'],
        'FX3_agreement_first': lambda r: 2 * r['x']['in_both'] + r['x']['fused_score'],
        'FX4_struct_vote': fx4,
    }


FIXED_COMBOS = ['FX1_best_channel_rr', 'FX2_lexical_first', 'FX3_agreement_first', 'FX4_struct_vote']


def lr_scorer(m):
    mu = np.array(m['mean']); sd = np.array(m['std']); w = np.array(m['coef']); b = m['intercept']
    feats = m['features']

    def f(r):
        v = (np.array([r['x'][k] for k in feats]) - mu) / sd
        return float(v @ w + b)
    return f


def fit_lr(rows, feats, C=1.0):
    from scipy.optimize import minimize
    X = np.array([[r['x'][k] for k in feats] for r in rows], dtype=float)
    y = np.array([r['y'] for r in rows], dtype=float)
    mu = X.mean(0); sd = X.std(0); sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    npos = y.sum(); nneg = len(y) - npos
    sw = np.where(y == 1, len(y) / (2 * npos), len(y) / (2 * nneg))

    def obj(p):
        w, b = p[:-1], p[-1]
        z = Z @ w + b
        ll = np.logaddexp(0, z) - y * z
        p_ = 1 / (1 + np.exp(-z))
        g = sw * (p_ - y)
        loss = (sw * ll).sum() + 0.5 / C * (w @ w)
        grad = np.concatenate([Z.T @ g + w / C, [g.sum()]])
        return loss, grad
    res = minimize(obj, np.zeros(len(feats) + 1), jac=True, method='L-BFGS-B',
                   options={'maxiter': 5000})
    return {'features': feats, 'mean': mu.tolist(), 'std': sd.tolist(),
            'coef': res.x[:-1].tolist(), 'intercept': float(res.x[-1]),
            'converged': bool(res.success), 'n_rows': len(y), 'n_pos': int(npos)}


# ---------------------------------------------------------------- metrics
def eligible(rows):
    ys = [r['y'] for r in rows]
    return len(rows) >= 5 and 0 < sum(ys) < len(ys)


def task_auc_pairs(scores, ys):
    """AUROC (ties 0.5) and (wins, pairs) for pooled pairwise accuracy."""
    s = np.asarray(scores, float); y = np.asarray(ys)
    rk = rankdata(s)
    npos = int(y.sum()); nneg = len(y) - npos
    wins = rk[y == 1].sum() - npos * (npos + 1) / 2
    return wins / (npos * nneg), wins, npos * nneg


def _tb(key):
    return hashlib.sha1(key.encode()).hexdigest()


def task_ap(scores, ys, keys):
    order = sorted(range(len(ys)), key=lambda i: (-scores[i], _tb(keys[i])))
    hits = 0; ap = 0.0; npos = sum(ys)
    for n, i in enumerate(order, 1):
        if ys[i]:
            hits += 1; ap += hits / n
    return ap / npos


def per_task(tasks, scorer):
    """tasks: list of row lists (eligible). -> arrays auc, ap, wins, pairs"""
    out = {'auc': [], 'ap': [], 'wins': [], 'pairs': [], 'base': []}
    for rows in tasks:
        sc = [scorer(r) for r in rows]; ys = [r['y'] for r in rows]
        a, w, p = task_auc_pairs(sc, ys)
        out['auc'].append(a); out['wins'].append(w); out['pairs'].append(p)
        out['ap'].append(task_ap(sc, ys, [r['key'] for r in rows]))
        out['base'].append(sum(ys) / len(ys))
    return {k: np.array(v, float) for k, v in out.items()}


def random_scorer(seed):
    rng = random.Random(seed)
    cache = {}

    def f(r):
        k = (r['task'], r['key'])
        if k not in cache:
            cache[k] = rng.random()
        return cache[k]
    return f


def boot_idx(n, B=2000, seed=32):
    rng = np.random.default_rng(seed)
    return rng.integers(0, n, size=(B, n))


def summarize(pt, idx):
    auc = pt['auc']; ap = pt['ap']; w = pt['wins']; p = pt['pairs']
    bm = auc[idx].mean(1); bpw = w[idx].sum(1) / p[idx].sum(1); bap = ap[idx].mean(1)
    ci = lambda a: [float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))]
    return {'n': len(auc), 'auroc': float(auc.mean()), 'auroc_ci': ci(bm),
            'auprc': float(ap.mean()), 'auprc_ci': ci(bap), 'ap_random': float(pt['base'].mean()),
            'pairwise': float(w.sum() / p.sum()), 'pairwise_ci': ci(bpw)}


def delta(pt, ctl, idx):
    d = pt['auc'] - ctl['auc']
    bd = d[idx].mean(1)
    pw = pt['wins'].sum() / pt['pairs'].sum() - ctl['wins'].sum() / ctl['pairs'].sum()
    bpw = pt['wins'][idx].sum(1) / pt['pairs'][idx].sum(1) - ctl['wins'][idx].sum(1) / ctl['pairs'][idx].sum(1)
    return {'d_auroc': float(d.mean()), 'd_auroc_ci': [float(np.percentile(bd, 2.5)), float(np.percentile(bd, 97.5))],
            'd_pairwise': float(pw), 'd_pairwise_ci': [float(np.percentile(bpw, 2.5)), float(np.percentile(bpw, 97.5))]}
