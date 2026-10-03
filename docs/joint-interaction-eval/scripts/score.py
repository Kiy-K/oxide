"""Issue #39: score (query, candidate) pairs with the tiny cross-encoder (PROBE.md §2).

Reads results/cands.jsonl.gz (labels are never read).

usage: score.py probe                  # PROBE §3 tasks -> results/scores-probe.jsonl
       score.py profile <set> <task>   # timing only, scores discarded (PROBE §4)
env:   BATCH (default 16), THREADS (default 2)
"""
import gzip, json, os, resource, sys, time
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from probe_tasks import pick

RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'results')
MODEL, REV = 'cross-encoder/ms-marco-MiniLM-L6-v2', '233902d25c440f23af6f7d6e94d2946bac0bee0a'
Q_TOK, MAX_LEN = 128, 512
BATCH, THREADS = int(os.environ.get('BATCH', 16)), int(os.environ.get('THREADS', 2))


def main():
    mode = sys.argv[1]
    torch.set_num_threads(THREADS)
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(MODEL, revision=REV)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL, revision=REV, dtype=torch.float32).eval()
    load_s = time.time() - t0

    want = set(pick()) if mode == 'probe' else {(sys.argv[2], sys.argv[3])}
    tasks = {}
    for l in gzip.open(os.path.join(RES, 'cands.jsonl.gz'), 'rt'):
        r = json.loads(l)
        if (r['set'], r['task']) in want:
            tasks.setdefault((r['set'], r['task']), []).append(r)
    out_p = os.path.join(RES, 'scores-probe.jsonl') if mode == 'probe' else os.devnull
    print(f'load {load_s:.2f}s, {len(tasks)} tasks, batch {BATCH}, threads {THREADS}', flush=True)
    with open(out_p, 'w') as out:
        for (s, t), rows in sorted(tasks.items()):
            ts = time.time()
            q = tok.decode(tok(rows[0]['query'], add_special_tokens=False)['input_ids'][:Q_TOK])
            docs = [f"{r['path']}\n{r['qname']}\n{r['text']}" for r in rows]
            enc = tok([q] * len(docs), docs, truncation='only_second', max_length=MAX_LEN)
            tok_s = time.time() - ts
            # Length-sorted batches cut padding; scores are mapped back to candidate order.
            lens = [len(x) for x in enc['input_ids']]
            order = sorted(range(len(docs)), key=lens.__getitem__)
            scores = [0.0] * len(docs)
            for i in range(0, len(order), BATCH):
                idx = order[i:i + BATCH]
                b = tok.pad({k: [enc[k][j] for j in idx] for k in enc}, return_tensors='pt')
                with torch.no_grad():
                    for j, v in zip(idx, model(**b).logits[:, 0].tolist()):
                        scores[j] = v
            out.write(json.dumps({'set': s, 'task': t, 'keys': [r['key'] for r in rows], 'scores': scores,
                                  'secs': time.time() - ts, 'tok_secs': tok_s, 'tokens': sum(lens),
                                  'batch': BATCH, 'threads': THREADS, 'load_s': load_s,
                                  'rss_mb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}) + '\n')
            out.flush()
            print(f'{s} {t} {time.time() - ts:.2f}s tok {tok_s:.2f}s tokens {sum(lens)} '
                  f'rss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f}MB', flush=True)


if __name__ == '__main__':
    main()
