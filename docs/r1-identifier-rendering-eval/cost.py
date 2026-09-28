"""R1 cost measurement (ANALYSIS_PLAN §Cost): document token length (shipped
tokenizer), embedding storage (full D0 vs R1 re-embeds of the same corpus),
latency (bench results in results/bench.jsonl, produced by run_bench.sh)."""
import glob, json, os, sqlite3, statistics
from tokenizers import Tokenizer

HERE = os.path.dirname(os.path.abspath(__file__)); W = os.path.join(HERE, 'work')
TOK = Tokenizer.from_file(os.path.join(HERE, 'tok', 'tokenizer.json'))
# Count raw pieces: tokenizer.json ships batch padding + truncation, which
# would pad every text in an encode_batch call to the batch maximum.
TOK.no_padding(); TOK.no_truncation()


def p95(v):
    v = sorted(v); return v[int(0.95 * (len(v) - 1))]


def tokens(prefix):
    out = {}
    for p in sorted(glob.glob(f'{W}/texts/D0/{prefix}*.jsonl')):
        corp = os.path.basename(p)[:-6]
        d0 = [json.loads(l)['text'] for l in open(p)]
        r1 = [json.loads(l)['text'] for l in open(f'{W}/texts/R1/{corp}.jsonl')]
        a = [len(e.tokens) for e in TOK.encode_batch(d0, add_special_tokens=False)]
        b = [len(e.tokens) for e in TOK.encode_batch(r1, add_special_tokens=False)]
        out[corp] = (a, b)
    A = [x for a, _ in out.values() for x in a]; B = [x for _, b in out.values() for x in b]
    cap = lambda v: [min(x, 510) for x in v]
    return {'corpora': len(out), 'symbols': len(A),
            'mean_D0': statistics.fmean(A), 'mean_R1': statistics.fmean(B),
            'mean_delta_pct': 100 * (sum(B) / sum(A) - 1),
            'median_D0': statistics.median(A), 'median_R1': statistics.median(B),
            'p95_D0': p95(A), 'p95_R1': p95(B),
            'capped_mean_D0': statistics.fmean(cap(A)), 'capped_mean_R1': statistics.fmean(cap(B)),
            'truncated_D0': sum(x > 510 for x in A), 'truncated_R1': sum(x > 510 for x in B),
            'symbols_changed': sum(1 for p in glob.glob(f'{W}/texts/D0/{prefix}*.jsonl')
                                   for a, b in zip(open(p), open(p.replace('/D0/', '/R1/')))
                                   if json.loads(a)['text'] != json.loads(b)['text'])}


def storage():
    out = {}
    for p in sorted(glob.glob(f'{W}/db/*.r1.db')):
        corp = os.path.basename(p)[:-len('.r1.db')]
        q = p.replace('.r1.db', '.d0re.db')
        if not os.path.exists(q):
            continue
        s = lambda db: sqlite3.connect(db).execute('select sum(length(vec)), count(*) from embeddings').fetchone()
        (va, na), (vb, nb) = s(q), s(p)
        out[corp] = {'vec_bytes_D0': va, 'vec_bytes_R1': vb, 'rows': (na, nb),
                     'db_bytes_D0': os.path.getsize(q), 'db_bytes_R1': os.path.getsize(p)}
    tot = lambda k: sum(v[k] for v in out.values())
    return {'corpora': out, 'vec_delta_pct': 100 * (tot('vec_bytes_R1') / tot('vec_bytes_D0') - 1) if out else None,
            'db_delta_pct': 100 * (tot('db_bytes_R1') / tot('db_bytes_D0') - 1) if out else None}


if __name__ == '__main__':
    res = {'tokens_heldout_parents': tokens('par-'), 'tokens_dev': tokens('head-'), 'tokens_cb': tokens('cb-'),
           'storage': storage()}
    bp = os.path.join(HERE, 'results', 'bench.jsonl')
    if os.path.exists(bp):
        res['latency'] = [json.loads(l) for l in open(bp)]
    json.dump(res, open(os.path.join(HERE, 'results', 'cost.json'), 'w'), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != 'storage'}, indent=1))
    print('storage', res['storage']['vec_delta_pct'], res['storage']['db_delta_pct'])
