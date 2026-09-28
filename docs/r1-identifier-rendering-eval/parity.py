"""R1 parity gates (ANALYSIS_PLAN §Parity): (a) D0 dumps vs committed
ranking-fusion dumps, (b) harness D0 re-embed vs stored production vectors,
(c) Rust R1 text vs the frozen Python rule. Prints a verdict per gate."""
import glob, gzip, json, os, re, sqlite3, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__)); W = os.path.join(HERE, 'work')
R = '/home/user/oxide/docs/ranking-fusion-eval/results/'
SPLIT = re.compile(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')
COMMITTED = {'dev': 'dump-plain', 'parity_heldout_head': 'dump-heldout', 'cb': 'dump-contextbench'}


def load_committed():
    out = {}
    for s, f in COMMITTED.items():
        for l in gzip.open(R + f + '.jsonl.gz', 'rt'):
            d = json.loads(l); out[(s, d['id'])] = d
    return out


def parity_a():
    C = load_committed(); res = collections.Counter(); bad = []
    for tf in glob.glob(f'{W}/tasks/*__*.jsonl'):
        s, corp = os.path.basename(tf)[:-6].split('__')
        if s not in COMMITTED:
            continue
        dp = f'{W}/dumps/D0/{corp}.jsonl'
        if not os.path.exists(dp):
            res['missing_dump', s] += 1; continue
        mine = {json.loads(l)['id']: json.loads(l) for l in open(dp)}
        for t in map(json.loads, open(tf)):
            a = mine.get(t['id']); b = C[(s, t['id'])]
            if a is None:
                res['missing_task', s] += 1; continue
            ok = {
                'sem_keys': [k for k, _ in a['semantic']] == [k for k, _ in b['semantic']],
                'sem_scores': len(a['semantic']) == len(b['semantic']) and all(abs(x[1] - y[1]) <= 1e-6 for x, y in zip(a['semantic'], b['semantic'])),
                'lex_keys': [k for k, _ in a['lexical']] == [k for k, _ in b['lexical']],
                'fused_order': [x[0] for x in a['fused']] == [x[0] for x in b['fused']],
            }
            for k, v in ok.items():
                res[s, k, v] += 1
            if not all(ok.values()):
                bad.append((s, t['id'], [k for k, v in ok.items() if not v]))
    return res, bad


def vecs(db):
    c = sqlite3.connect(db)
    return {sid: v for sid, v in c.execute('select symbol_id, vec from embeddings')}


def parity_b():
    out = {}
    for p in sorted(glob.glob(f'{W}/db/*.d0re.db')):
        corp = os.path.basename(p)[:-len('.d0re.db')]
        a = vecs(f'{W}/corp/{corp}/.oxide/index.db'); b = vecs(p)
        same = sum(1 for k in a if b.get(k) == a[k])
        out[corp] = {'stored': len(a), 'reembedded': len(b), 'bit_identical': same, 'pass': same == len(a) == len(b)}
    return out


def parity_c():
    res = collections.Counter(); bad = []
    for p in sorted(glob.glob(f'{W}/texts/D0/*.jsonl')):
        corp = os.path.basename(p)[:-6]
        d0 = [json.loads(l) for l in open(p)]
        r1 = [json.loads(l) for l in open(f'{W}/texts/R1/{corp}.jsonl')]
        assert len(d0) == len(r1)
        for a, b in zip(d0, r1):
            assert a['id'] == b['id']
            res['d0_text_eq_symbol_embed_text', a['text'] == a['d0']] += 1
            head = f"{a['file']} {a['kind']} "
            if not a['d0'].startswith(head):
                res['head_mismatch'] += 1; bad.append((corp, a['id'], 'head')); continue
            exp = head + SPLIT.sub(' ', a['d0'][len(head):])
            ok = b['text'] == exp
            res['r1_eq_python', ok] += 1
            res['r1_changed', b['text'] != a['d0']] += 1
            if not ok and len(bad) < 20:
                bad.append((corp, a['id'], b['text'][:120], exp[:120]))
    return res, bad


if __name__ == '__main__':
    out = {}
    ra, ba = parity_a(); out['a'] = {str(k): v for k, v in ra.items()}; out['a_bad'] = ba
    rb = parity_b(); out['b'] = rb
    rc, bc = parity_c(); out['c'] = {str(k): v for k, v in rc.items()}; out['c_bad'] = bc
    out['pass'] = {'a': not ba and not any('missing' in str(k) for k in ra),
                   'b': bool(rb) and all(v['pass'] for v in rb.values()),
                   'c': not bc and rc.get(('d0_text_eq_symbol_embed_text', False), 0) == 0}
    json.dump(out, open(os.path.join(HERE, 'results', sys.argv[1] if len(sys.argv) > 1 else 'parity.json'), 'w'), indent=1)
    for k, v in sorted(ra.items(), key=str): print('a', k, v)
    print('a mismatches', ba[:10])
    for k, v in rb.items(): print('b', k, v)
    for k, v in sorted(rc.items(), key=str): print('c', k, v)
    print('c mismatches', bc[:5])
    print('PASS', out['pass'])
