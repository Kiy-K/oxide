"""Typed-evidence probe: ask independent typed noul questions per file view (PROBE.md §4).

The model sees the query, the file path, the declaration names and the candidate source. It
never sees OXIDE ranks, scores or labels. Every question is pointwise (one file per state), so
there is no presentation order and no `choice`. Both arms read the same results/states.jsonl.

usage: score.py states                # results/views.jsonl -> results/states.jsonl (tokenizer only)
       score.py julia                 # local Julia-1 -> results/scores-julia.jsonl (run via run.sh)
       score.py jev                   # hosted Jev    -> results/scores-jev.jsonl
       score.py smoke <set> <task>    # one non-probe task with local Julia, timing only (discarded)
env:   JULIA (checkout of SupersonicLabs/Julia-1 @ REV), THREADS (default 2),
       TYPESAFE_API_KEY (jev; falls back to TYPSAFE_API_KEY in the repo .env)
"""
import json, os, resource, sys, time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, '..', 'results')
REV = 'a85b127321d580d65176c89ced8273f305745d85'
JEV_MODEL = 'jev-1.13.0'
Q_TOK, SRC_TOK = 192, 640

# Six typed questions adapted from jevgrep's evidence/file-assessment criteria
# (packages/core/src/requests.ts @ 703aba1), plus #31's J1 wording verbatim as control.
NO_YES = lambda no, yes: {'false': no, 'true': yes}
QUESTIONS = {
    'relevant': dict(type='noul', instructions=(
        "Does this file's source directly implement, control, or test the behavior the coding task asks "
        "to change or investigate? Count the current implementation even if it contains the bug. Topic "
        "similarity, generic utilities and incidental imports are insufficient."),
        criteria=NO_YES('No: unrelated, generic, or only topically similar code.',
                        'Yes: directly implements, controls, or tests the behavior.')),
    'in_scope': dict(type='noul', instructions=(
        "Does this file belong to the specific API, entry point, or component whose behavior the task "
        "asks to change or understand? A separate API with similar functionality is out of scope unless "
        "the source shows the targeted one uses it."),
        criteria=NO_YES('Out of scope: a different component or an analogous API.',
                        'In scope: part of the targeted API or component.')),
    'primary': dict(type='noul', instructions=(
        "Should this file be read early as primary evidence for this task? For behavior, implementation "
        "or debugging tasks, favor the actual implementation and controlling code over loosely related "
        "helpers. Judge priority for this task, not general topical similarity."),
        criteria=NO_YES('Secondary: can be skipped or read later.', 'Primary: should be read first.')),
    'implementation': dict(type='noul', instructions=(
        "Does this file contain code that directly executes or controls the current behavior described "
        "by the task, including buggy or missing behavior? Generic support, configuration and tests "
        "alone do not count."),
        criteria=NO_YES('No: not the code that runs this behavior.',
                        'Yes: code that executes or controls this behavior.')),
    'test': dict(type='noul', instructions=(
        "Does this file contain executable tests that validate the behavior described by the task?"),
        criteria=NO_YES('No: no tests of this behavior.', 'Yes: tests that validate this behavior.')),
    'concrete_reference': dict(type='noul', instructions=(
        "Does this file define or directly use a specific identifier, option, error message, or file "
        "explicitly named in the task text? Shared topic words or generic terms do not count."),
        criteria=NO_YES('No: no concrete name from the task appears here.',
                        'Yes: defines or uses a name explicitly given in the task.')),
    'J1': dict(type='noul', instructions='Would this evidence materially help solve the task?',
               criteria=NO_YES('The evidence is irrelevant, redundant, or unlikely to help implement/debug the task.',
                               'The evidence directly contributes information useful for implementing/debugging the task.')),
}

def build_states(views, tok):
    enc = lambda s: tok(s, add_special_tokens=False)['input_ids']
    cut = lambda s, n: s if len(enc(s)) <= n else tok.decode(enc(s)[:n]) + ' …'
    out = []
    for v in views:
        q = cut(v['query'], Q_TOK)
        files = []
        for f in v['files']:
            state = (f"Coding task:\n{q}\n\nFile: {f['path']}\nDeclarations: {', '.join(f['decls'][:20])}"
                     f"\n\nSource:\n{cut(f['source'], SRC_TOK)}")
            files.append({'path': f['path'], 'state': state, 'state_tokens': len(enc(state))})
        out.append({'set': v['set'], 'task': v['task'], 'files': files})
    return out


def julia_engine():
    import torch
    from julia import load_model
    from julia.typed import predict_typed
    t0 = time.time()
    eng = load_model(os.environ['JULIA'], device='cpu', strict_encoding=True, max_length=8192, head_length=512)
    torch.set_num_threads(int(os.environ.get('THREADS', 2)))  # load_model resets it
    meta = {'backend': f'julia@{REV[:12]}', 'load_s': time.time() - t0, 'threads': torch.get_num_threads()}
    return (lambda state: {k: a['noul'] for k, a in predict_typed(eng, state, QUESTIONS)['answers'].items()}), meta, eng.tokenizer


def jev_engine():
    key = os.environ.get('TYPESAFE_API_KEY') or next(
        l.split('=', 1)[1].strip() for l in open(os.path.join(HERE, '..', '..', '..', '.env'))
        if l.startswith('TYPSAFE_API_KEY='))

    def ask(state):
        body = json.dumps({'state': state, 'model': JEV_MODEL, 'questions': QUESTIONS}).encode()
        req = urllib.request.Request('https://api.typesafe.ai/v1/systemone', body, {
            'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
        for attempt in range(5):
            try:
                r = json.load(urllib.request.urlopen(req, timeout=60))
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 4:
                    raise
                time.sleep(float(e.headers.get('retry-after') or 2 ** attempt))
        assert r['model'] == JEV_MODEL, r['model']
        return {k: a['noul'] for k, a in r['answers'].items()}
    return ask, {'backend': JEV_MODEL, 'load_s': 0.0, 'threads': None}


def run(states, ask, meta, out_p):
    with open(out_p, 'w') as out:
        for v in states:
            files, ts = [], time.time()
            for f in v['files']:
                tf = time.time()
                files.append({'path': f['path'], 'p': ask(f['state']), 'ms': (time.time() - tf) * 1e3,
                              'state_tokens': f['state_tokens']})
            rec = {'set': v['set'], 'task': v['task'], 'files': files, 'secs': time.time() - ts,
                   'rss_mb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, **meta}
            out.write(json.dumps(rec) + '\n')
            out.flush()
            ms = sorted(f['ms'] for f in files)
            print(f"{meta['backend']} {v['set']} {v['task']} files {len(files)} {rec['secs']:.1f}s "
                  f"median/file {ms[len(ms) // 2]:.0f}ms rss {rec['rss_mb']:.0f}MB", flush=True)


def main():
    mode = sys.argv[1]
    states_p = os.path.join(RES, 'states.jsonl')
    if mode == 'states':
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(os.path.join(os.environ['JULIA'], 'tokenizer'))
        views = [json.loads(l) for l in open(os.path.join(RES, 'views.jsonl'))]
        with open(states_p, 'w') as f:
            for s in build_states(views, tok):
                f.write(json.dumps(s) + '\n')
    elif mode == 'jev':
        ask, meta = jev_engine()
        run([json.loads(l) for l in open(states_p)], ask, meta, os.path.join(RES, 'scores-jev.jsonl'))
    elif mode == 'julia':
        ask, meta, _ = julia_engine()
        print(f"load {meta['load_s']:.1f}s threads {meta['threads']}", flush=True)
        run([json.loads(l) for l in open(states_p)], ask, meta, os.path.join(RES, 'scores-julia.jsonl'))
    elif mode == 'smoke':
        import gzip
        from views import CANDS, file_view
        ask, meta, tok = julia_engine()
        print(f"load {meta['load_s']:.1f}s threads {meta['threads']}", flush=True)
        rows = [r for r in map(json.loads, gzip.open(CANDS, 'rt')) if (r['set'], r['task']) == tuple(sys.argv[2:4])]
        view = {'set': rows[0]['set'], 'task': rows[0]['task'], 'query': rows[0]['query'], 'files': file_view(rows)}
        run(build_states([view], tok), ask, meta, os.devnull)


if __name__ == '__main__':
    main()
