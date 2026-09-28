"""Issue #32: build per-candidate feature rows (PROTOCOL.md §3-5).

Reads only the committed ranking-fusion dumps + task files. Labels are
attached in a separate field `y` and never enter a feature.
Output: results/rows.jsonl.gz, one line per (set, task, candidate).
"""
import gzip, json, math, os, re, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
R = f'{REPO}/docs/ranking-fusion-eval/results/'
OUT = os.path.join(HERE, '..', 'results')
sys.dont_write_bytecode = True  # never leave __pycache__ in the taxonomy dir
sys.path.insert(0, f'{REPO}/docs/retrieval-failure-taxonomy/scripts')
from analyze import classify  # query-only deterministic regex classifier

SETS = [('dev', 'dump-plain', 'tasks'), ('masked', 'dump-masked', 'tasks-masked'),
        ('heldout', 'dump-heldout', 'heldout-clean'), ('cb', 'dump-contextbench', 'cb-tasks')]
REL_W = {'uses': 1.0, 'imported-definition': 1.0, 'parent': 0.5, 'child': 0.5,
         'sibling': 0.25, 'test': 0.25}
RELS = ['uses', 'imported-definition', 'parent', 'child', 'sibling', 'test']

FEATURES = {
    'R': ['fused_rank', 'fused_score', 'lex_rank', 'lex_score', 'sem_rank', 'sem_score',
          'in_lex', 'in_sem', 'in_both', 'rrf_lex', 'rrf_sem', 'rank_disagree', 'lex_minus_sem'],
    'S': ['rel_distance', 'n_seed_links', 'best_seed_rank', 'rel_uses', 'rel_imported_definition',
          'rel_parent', 'rel_child', 'rel_sibling', 'rel_test', 'seed_fanout', 'rel_weight', 'link_top1'],
    'I': ['is_module', 'is_test', 'is_nested', 'path_depth', 'name_in_query', 'file_stem_in_query',
          'same_file_top1', 'same_file_top5'],
    'C': ['span_lines', 'span_missing', 'in_seed_cut', 'neighbor_only'],
}


def rank_map(lst):
    m = {}
    for i, e in enumerate(lst):
        m.setdefault(e[0], i + 1)
    return m


def split_key(k):
    f, q = k.split('#', 1)
    return f, q


def simple_name(q):
    return re.split(r'\.|::', q)[-1]


def is_test(f, name):
    # production context::is_test_symbol (src/context.rs)
    f = f.lower(); n = name.lower()
    return (f.startswith('test_') or '_test.' in f or '.test.' in f or '.spec.' in f
            or '/tests/' in f or n.startswith('test_'))


def word_in(word, text_lc):
    return len(word) >= 3 and re.search(r'(?<![A-Za-z0-9_])' + re.escape(word.lower()) + r'(?![A-Za-z0-9_])', text_lc) is not None


def cb_gold():
    """ContextBench gold lines, if the parquet is available (PROTOCOL §4, §9)."""
    p = os.environ.get('CB_PARQUET')
    if not p or not os.path.exists(p):
        return None
    import pandas as pd
    norm = re.compile(r"^(?:/workspace/[^/]+/|/testbed/)")
    d = pd.read_parquet(p)
    out = {}
    for _, r in d.iterrows():
        g = json.loads(r.gold_context) if isinstance(r.gold_context, str) else list(r.gold_context)
        gl = {}
        for it in g:
            f = norm.sub('', it.get('file') or '')
            if f:
                gl.setdefault(f, []).append((int(it.get('start_line', 1)), int(it.get('end_line', 1))))
        out[r.instance_id] = gl
    return out


def task_rows(setname, d, t, cbg):
    q = t['query']; qlc = q.lower()
    fused = d['fused']; F = rank_map(fused); L = rank_map(d['lexical']); S = rank_map(d['semantic'])
    fscore = {e[0]: e[1] for e in fused}; lscore = {e[0]: e[1] for e in d['lexical']}
    sscore = {e[0]: e[1] for e in d['semantic']}
    top_f = fused[0][1] if fused else 1.0
    max_l = max(lscore.values()) if lscore else 1.0
    min_s = min(sscore.values()) if sscore else 0.0
    nf = len(fused)
    seeds = [s['seed'] for s in d['neighbors']]
    assert seeds == [e[0] for e in fused[:len(seeds)]]
    links = {}  # key -> list of (seed_rank, rel, fanout)
    fan = {}
    for j, s in enumerate(d['neighbors'], 1):
        fan[j] = len(s['neighbors'])
        for rel, k in s['neighbors']:
            if k == s['seed']:
                continue
            links.setdefault(k, []).append((j, rel))
    max_fan = max(fan.values()) if fan else 0
    top50 = [e[0] for e in fused[:50]]
    U = list(dict.fromkeys(top50 + sorted(links)))
    seed_files = {j: split_key(k)[0] for j, k in enumerate(seeds, 1)}
    # labels
    if setname == 'cb':
        gold = None
        if cbg is not None:
            gl = cbg.get(d['id'], {})
    else:
        gold = set(t['gold'])
    med_span = statistics.median([e - s + 1 for s, e in d['spans'].values()]) if d['spans'] else 1
    qclass = classify(q)
    rows = []
    for k in U:
        f, qn = split_key(k)
        name = simple_name(qn)
        fr = F.get(k, nf + 1); lr = L.get(k, 201); sr = S.get(k, 201)
        lk = links.get(k, [])
        jset = {j for j, _ in lk}
        rels = {r for _, r in lk}
        sp = d['spans'].get(k)
        x = {
            'fused_rank': -math.log(fr),
            'fused_score': fscore.get(k, 0.0) / top_f,
            'lex_rank': -math.log(lr),
            'lex_score': lscore.get(k, 0.0) / max_l if k in lscore else 0.0,
            'sem_rank': -math.log(sr),
            'sem_score': sscore.get(k, min_s),
            'in_lex': float(k in L), 'in_sem': float(k in S), 'in_both': float(k in L and k in S),
            'rrf_lex': 0.6 / (60 + lr) if k in L else 0.0,
            'rrf_sem': 0.4 / (60 + sr) if k in S else 0.0,
            'rank_disagree': abs(math.log(lr) - math.log(sr)),
            'lex_minus_sem': math.log(sr) - math.log(lr),
            'rel_distance': -(0 if fr <= 5 else 1 if lk else 2),
            'n_seed_links': float(len(jset)),
            'best_seed_rank': -float(min(jset)) if jset else -6.0,
            'seed_fanout': -math.log(1 + min(fan[j] for j in jset)) if jset else -math.log(1 + max_fan + 1),
            'rel_weight': max((REL_W.get(r, 0.0) for r in rels), default=0.0),
            'link_top1': float(1 in jset),
            'is_module': float(qn.endswith(':__module__')),
            'is_test': float(is_test(f, name)),
            'is_nested': float(('.' in qn or '::' in qn) and not qn.endswith(':__module__')),
            'path_depth': float(f.count('/')),
            'name_in_query': float(not qn.endswith(':__module__') and word_in(name, qlc)),
            'file_stem_in_query': float(word_in(os.path.splitext(os.path.basename(f))[0], qlc)),
            'same_file_top1': float(fr != 1 and f == seed_files.get(1)),
            'same_file_top5': float(any(sf == f and seeds[j - 1] != k for j, sf in seed_files.items())),
            'span_lines': math.log(1 + (sp[1] - sp[0] + 1)) if sp else math.log(1 + med_span),
            'span_missing': float(sp is None),
            'in_seed_cut': float(fr <= 16),
            'neighbor_only': float(fr > 50),
        }
        for r in RELS:
            x['rel_' + r.replace('-', '_')] = float(r in rels)
        # pool order control inputs (label-free)
        best_seed_score = max((fscore[seeds[j - 1]] for j in jset), default=0.0)
        aux = {'fr': fr, 'lr': lr, 'sr': sr, 'fscore': fscore.get(k, 0.0),
               'best_link_seed_score': best_seed_score, 'links': sorted(lk)}
        band = 'B1' if fr <= 5 else 'B2' if fr <= 16 else 'B3' if fr <= 50 else 'B4'
        if setname == 'cb':
            if cbg is None:
                y = None
            elif sp is None:
                continue  # unlabeled (PROTOCOL §4)
            else:
                y = int(not qn.endswith(':__module__') and any(
                    sp[0] <= b and a <= sp[1] for a, b in gl.get(f, [])))
        else:
            y = int(k in gold)
        rows.append({'set': setname, 'task': d['id'], 'key': k, 'band': band, 'qclass': qclass,
                     'x': x, 'aux': aux, 'y': y})
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    cbg = cb_gold()
    n = 0
    with gzip.open(os.path.join(OUT, 'rows.jsonl.gz'), 'wt') as out:
        for s, dump, tasks in SETS:
            T = {json.loads(l)['id']: json.loads(l) for l in open(R + tasks + '.jsonl')}
            for l in gzip.open(R + dump + '.jsonl.gz', 'rt'):
                d = json.loads(l)
                if d['id'] not in T:
                    continue
                for row in task_rows(s, d, T[d['id']], cbg):
                    out.write(json.dumps(row) + '\n'); n += 1
    print('rows', n, 'cb gold', 'available' if cbg is not None else 'NOT available')


if __name__ == '__main__':
    main()
