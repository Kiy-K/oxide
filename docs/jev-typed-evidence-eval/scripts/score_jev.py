"""#40 Jev scoring (PROTOCOL §2, §5 V2/V3, §7). Written and hashed before any Jev call.

States: docs/typed-evidence-eval/scripts/score.py build_states() unchanged (Julia-1 tokenizer
@ a85b127 for truncation only; query <= 192 tokens, source <= 640, <= 20 declarations).
The no-source `meta` state is the typed state cut at len(prefix), prefix = the build_states
template up to the declarations; the builder asserts the remainder starts with "\\n\\nSource:\\n".
Questions and request shape: score.py QUESTIONS and jev_engine's body, verbatim.

Single pass: each (file, arm) is requested once; only HTTP 429/5xx, timeouts (60 s) and
connection errors are retried (3 retries, 1/2/4 s or a larger Retry-After); a malformed
response or a missing question key is a failure. A request that fails after its retries is
final. Every raw response is logged. Per task, requests go out with concurrency 12.

usage: score_jev.py states                     # tasks -> results/states.jsonl.gz (no network)
       score_jev.py canary pre|post            # 5 synthetic canaries -> results/canary-<x>.jsonl
       score_jev.py run                        # all states -> results/scores.jsonl
       score_jev.py latency                    # 50 synthetic matched-size states, sequential
       unshare -rn score_jev.py offline        # network off: one synthetic canary -> results/offline.json
env:   JULIA_TOKENIZER (dir with tokenizer.json), TYPESAFE_API_KEY (or TYPSAFE_API_KEY in .env)
"""
import gzip, json, os, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
RES = os.path.join(HERE, '..', 'results')
J = os.path.expanduser('~/.cache/oxide-jev-eval')
sys.dont_write_bytecode = True
sys.path.insert(0, f'{REPO}/docs/typed-evidence-eval/scripts')
from score import JEV_MODEL, QUESTIONS, Q_TOK, build_states  # noqa: E402

URL = 'https://api.typesafe.ai/v1/systemone'
RETRY_CODES = {429} | set(range(500, 600))
BACKOFF = (1, 2, 4)
CONCURRENCY = 12
QKEYS = set(QUESTIONS)


def tokenizer():
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(os.environ.get('JULIA_TOKENIZER', os.path.expanduser(
        '~/.cache/oxide-typed-eval/Julia-1/tokenizer')))


def meta_state(state, query, path, decls, tok):
    enc = lambda s: tok(s, add_special_tokens=False)['input_ids']
    q = query if len(enc(query)) <= Q_TOK else tok.decode(enc(query)[:Q_TOK]) + ' …'  # build_states' cut
    prefix = f"Coding task:\n{q}\n\nFile: {path}\nDeclarations: {', '.join(decls[:20])}"
    assert state.startswith(prefix) and state[len(prefix):].startswith('\n\nSource:\n'), path
    return prefix


def states():
    tok = tokenizer()
    tasks = [json.loads(l) for s in ('heldout', 'cb') for l in open(f'{J}/tasks/{s}-frozen.jsonl')]
    views = [{'set': t['set'], 'task': t['id'], 'query': t['query'], 'files': t['views']} for t in tasks]
    os.makedirs(RES, exist_ok=True)
    with gzip.open(os.path.join(RES, 'states.jsonl.gz'), 'wt', compresslevel=9) as out:
        for t, s in zip(tasks, build_states(views, tok)):
            for f, v in zip(s['files'], t['views']):
                assert f['path'] == v['path']
                f['meta_state'] = meta_state(f['state'], t['query'], v['path'], v['decls'], tok)
            out.write(json.dumps(s) + '\n')


def api_key():
    return os.environ.get('TYPESAFE_API_KEY') or next(
        l.split('=', 1)[1].strip() for l in open(os.path.join(REPO, '.env')) if l.startswith('TYPSAFE_API_KEY='))


def ask(state, key):
    """One request under the V2 retry policy. Returns a log record; never raises."""
    body = json.dumps({'state': state, 'model': JEV_MODEL, 'questions': QUESTIONS}).encode()
    attempts, t0 = [], time.time()
    for attempt in range(1 + len(BACKOFF)):
        ts = time.time()
        try:
            req = urllib.request.Request(URL, body, {'Authorization': f'Bearer {key}',
                                                     'Content-Type': 'application/json'})
            raw = urllib.request.urlopen(req, timeout=60).read().decode()
            attempts.append({'ms': (time.time() - ts) * 1e3, 'status': 200})
            try:
                r = json.loads(raw)
                p = {k: float(a['noul']) for k, a in r['answers'].items()}
                ok = set(p) >= QKEYS
                return {'ok': ok, 'model': r.get('model'), 'p': p if ok else None, 'raw': raw,
                        'why': None if ok else 'missing question key', 'attempts': attempts,
                        'ms': (time.time() - t0) * 1e3, 'usage': r.get('usage')}
            except (ValueError, KeyError, TypeError) as e:
                return {'ok': False, 'model': None, 'p': None, 'raw': raw, 'why': f'malformed: {e}',
                        'attempts': attempts, 'ms': (time.time() - t0) * 1e3}
        except urllib.error.HTTPError as e:
            attempts.append({'ms': (time.time() - ts) * 1e3, 'status': e.code})
            if e.code not in RETRY_CODES or attempt == len(BACKOFF):
                return {'ok': False, 'model': None, 'p': None, 'raw': None, 'why': f'http {e.code}',
                        'attempts': attempts, 'ms': (time.time() - t0) * 1e3}
            wait = max(BACKOFF[attempt], float(e.headers.get('retry-after') or 0))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            attempts.append({'ms': (time.time() - ts) * 1e3, 'status': f'conn: {e}'[:120]})
            if attempt == len(BACKOFF):
                return {'ok': False, 'model': None, 'p': None, 'raw': None, 'why': 'connection/timeout',
                        'attempts': attempts, 'ms': (time.time() - t0) * 1e3}
            wait = BACKOFF[attempt]
        time.sleep(wait)


def run():
    key = api_key()
    out_p = os.path.join(RES, 'scores.jsonl')
    done = set()
    if os.path.exists(out_p):  # an interrupted run continues with never-attempted tasks only
        done = {json.loads(l)['task'] for l in open(out_p)}
    with open(out_p, 'a') as out, ThreadPoolExecutor(CONCURRENCY) as pool:
        for s in map(json.loads, gzip.open(os.path.join(RES, 'states.jsonl.gz'), 'rt')):
            if s['task'] in done:
                continue
            jobs = [(f['path'], arm, f['state'] if arm == 'typed' else f['meta_state'])
                    for f in s['files'] for arm in ('typed', 'meta')]
            t0 = time.time()
            res = list(pool.map(lambda j: ask(j[2], key), jobs))
            rec = {'set': s['set'], 'task': s['task'], 'task_wall_ms': (time.time() - t0) * 1e3,
                   'calls': [{'path': p, 'arm': a, **r} for (p, a, _), r in zip(jobs, res)]}
            out.write(json.dumps(rec) + '\n')
            out.flush()
            bad = sum(not r['ok'] for r in res)
            print(f"{s['set']} {s['task']} {len(res)} calls, {bad} failed, {rec['task_wall_ms']:.0f} ms", flush=True)


def canary(which):
    key = api_key()
    with open(os.path.join(RES, f'canary-{which}.jsonl'), 'w') as out:
        for c in map(json.loads, open(os.path.join(HERE, '..', 'canaries.jsonl'))):
            out.write(json.dumps({'id': c['id'], **ask(c['state'], key)}) + '\n')


def latency():
    """50 synthetic states whose lengths match the frozen states' length quantiles."""
    key = api_key()
    lens = sorted(len(f['state']) for s in map(json.loads, gzip.open(os.path.join(RES, 'states.jsonl.gz'), 'rt'))
                  for f in s['files'])
    filler = open(os.path.join(HERE, '..', 'canaries.jsonl')).read()
    with open(os.path.join(RES, 'latency.jsonl'), 'w') as out:
        for i in range(50):
            n = lens[min(len(lens) - 1, int((i + 0.5) / 50 * len(lens)))]
            state = ('Coding task:\nsynthetic latency probe\n\nFile: synth.py\nDeclarations: f\n\nSource:\n'
                     + filler * (1 + n // max(1, len(filler))))[:n]
            r = ask(state, key)
            out.write(json.dumps({'i': i, 'chars': n, 'ok': r['ok'], 'ms': r['ms'], 'attempts': r['attempts']}) + '\n')


def offline():
    """§7 failure behavior with the network off (run inside `unshare -rn`)."""
    c = json.loads(open(os.path.join(HERE, '..', 'canaries.jsonl')).readline())
    json.dump(ask(c['state'], api_key()), open(os.path.join(RES, 'offline.json'), 'w'), indent=1)


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'canary':
        canary(sys.argv[2])
    else:
        {'states': states, 'run': run, 'latency': latency, 'offline': offline}[mode]()
