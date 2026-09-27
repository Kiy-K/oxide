"""Score OXIDE candidate pools with Julia-1 (research only, local, offline).

usage: julia_score.py <ch0-dump.jsonl> <form> <out.jsonl> [--ablation A] [--order O]

Reads the ch0 (production) trace pool of each task: the post-dedup candidate
pool, each with its 350-token-capped snippet (exactly what the allocator
would deliver). Julia sees only the task text, candidate provenance and code.
It never sees OXIDE scores, ranks, or gold. Task text comes from
tasks/*.jsonl by id.

forms:
  J1  noul  "Would this evidence materially help solve the task?"   -> P(true)
  J2  choice over the pool (<=20 options/call)                       -> P(option)
  J3  score rubric 0..3                                              -> E[score]/3
ablations (J1/J3 only): full (task+prov+code), nocode (task+prov),
  notask (prov+code), codeonly (code)
order (J2 only): hash (default, deterministic pseudo-random), oxide, reverse
"""
import glob, hashlib, json, math, os, sys, time
from julia import load_model

J = os.path.expanduser('~/.cache/oxide-julia-eval')
dump, form, out = sys.argv[1], sys.argv[2], sys.argv[3]
abl = sys.argv[sys.argv.index('--ablation') + 1] if '--ablation' in sys.argv else 'full'
order = sys.argv[sys.argv.index('--order') + 1] if '--order' in sys.argv else 'hash'
TASK_TOK, J2_SNIP_TOK = 256, 96
QUERY = {}
for f in glob.glob(f'{J}/tasks/*.jsonl'):
    for l in open(f):
        t = json.loads(l)
        QUERY[t['id']] = t['query']

eng = load_model(f'{J}/Julia-1', device='cpu', strict_encoding=True, max_length=8192, head_length=512)
tok = eng.tokenizer


def cut(text, n):
    ids = tok(text, add_special_tokens=False)['input_ids']
    return text if len(ids) <= n else tok.decode(ids[:n]) + ' …'


def prov(p):
    f, qn = p['key'].split('#', 1)
    via = [r for r in p['reasons'] if not r.startswith(('lexical=', 'semantic='))]
    s = f'file: {f}\nsymbol: {qn} ({p["kind"].lower()}, {p["role"]})'
    return s + (f'\nfound via: {", ".join(via)}' if via else '')


def state(task, p):
    parts = []
    if abl in ('full', 'nocode'):
        parts.append('Coding task:\n' + task)
    if abl != 'codeonly':
        parts.append('Candidate evidence:\n' + prov(p))
    if abl != 'nocode':
        parts.append('Code:\n' + p['snip'])
    return '\n\n'.join(parts)


Q1 = 'Would this evidence materially help solve the task?'
C1 = ['The evidence is irrelevant, redundant, or unlikely to help implement/debug the task.',
      'The evidence directly contributes information useful for implementing/debugging the task.']
Q3 = 'How useful is this evidence for solving the coding task?'
R3 = ['Irrelevant to the task.', 'Weakly useful: related, but not needed.',
      'Useful: context needed to understand the change.', 'Directly useful: code that must be read or edited.']
Q2 = 'Which candidate is the most useful evidence for solving this task?'


def softmax(v):
    m = max(v); e = [math.exp(x - m) for x in v]; s = sum(e)
    return [x / s for x in e]


with open(out, 'w') as fh:
    for line in open(dump):
        r = json.loads(line)
        if r['ch'] != 0 or r['mode'] != 'balanced' or r['blast']:
            continue
        task = cut(QUERY[r['id']], TASK_TOK)
        pool = r['trace']['pool']
        t0 = time.perf_counter()
        scores, extra = {}, {}
        if form in ('J1', 'J3'):
            rows = [dict(state=state(task, p), question=Q1 if form == 'J1' else Q3,
                         type='noul' if form == 'J1' else 'score', options=C1 if form == 'J1' else R3)
                    for p in pool]
            for p, v in zip(pool, eng.logits(rows)):
                pr = softmax(v)
                scores[p['key']] = pr[1] if form == 'J1' else sum(i * x for i, x in enumerate(pr)) / 3
        elif form == 'J2':
            ps = list(pool)
            if order == 'hash':
                ps.sort(key=lambda p: hashlib.sha256(p['key'].encode()).hexdigest())
            elif order == 'reverse':
                ps.reverse()
            # Native calls take 2–20 options: split larger pools into balanced groups.
            k = math.ceil(len(ps) / 20) or 1
            groups = [ps[i::k] for i in range(k)]
            extra['groups'] = [len(g) for g in groups]
            for g in groups:
                if len(g) == 1:
                    scores[g[0]['key']] = 1.0
                    continue
                body = '\n\n'.join(f'[c{j}] {p["key"]}\n{cut(p["snip"], J2_SNIP_TOK)}' for j, p in enumerate(g))
                crit = {f'c{j}': cut(f'c{j}: {p["key"].split("/")[-1]}', 40) for j, p in enumerate(g)}
                a = eng.predict(state='Coding task:\n' + task + '\n\nCandidates:\n' + body,
                                questions={'q': dict(type='choice', instructions=Q2, criteria=crit)})
                pr = a['answers']['q']['probabilities']
                for j, p in enumerate(g):
                    scores[p['key']] = pr[f'c{j}']
        ms = (time.perf_counter() - t0) * 1e3
        fh.write(json.dumps(dict(id=r['id'], form=form, ablation=abl, order=order, ms=ms, n=len(pool),
                                 scores=scores, **extra)) + '\n')
        fh.flush()
