# #40 Jev typed-evidence evaluation: results

**Verdict: INCONCLUSIVE (validity).** V3 failed: one canary probability changed by
**0.19** between the passes run immediately before and after the scoring run, against a
limit of 0.02. Under PROTOCOL §5/§6, once a V check fails no G gate is read. **The gate
statistics (G1–G5, per-dimension, J1+F) were never computed or inspected.** At the owner's
decision (2026-10-04) they stay sealed. The frozen task set and the raw scores are therefore
uncontaminated by any look at results, and a follow-up preregistration can reuse them.

What #40 established is operational, not about signal:
- every other validity check passed;
- Jev 1.13.0 is **not deterministic for identical input** (post-hoc diagnostic below);
- the cost, latency and failure record in §7.

Whether Jev's typed signals select better context than OXIDE's fused order remains
**untested**.

Preregistration: `PROTOCOL.md` (freeze 1, `6ff5a76`). Frozen tasks, harness and hashes:
`ff0f0c8` (freeze 2, made before the first Jev call on evaluation data).

## Validity (PROTOCOL §5)

| check | result | detail |
|---|---|---|
| V1 size floors | pass | 149 tasks (held-out 80, ContextBench 69); 51 repos, max share 7.4 %; description 45, quoted 37 |
| V2 failures | pass | 3,420 requests, 0 failed, 0 retries, 0 tasks excluded |
| V3 model and canaries | **FAIL** | every response reported `jev-1.13.0`; max canary drift 0.19 (> 0.02) |
| V4 hashes | pass | `PROTOCOL.sha256`, `results/prereg.sha256` (32 files); committed task files byte-identical to the cache copies the run read |
| V5 arm-A parity | pass | 149/149 `oxide context --json` byte-identical (research binary vs frozen binary) |

Canary drift (post − pre; `results/canary-{pre,post}.jsonl`). Both passes were about 3 min apart:

| canary | changes |
|---|---|
| c2 (unrelated file) | concrete_reference **−0.19**; in_scope, primary, J1 −0.01 |
| c4 (test file) | in_scope **−0.08**, primary **−0.05**, J1 −0.02, implementation −0.01 |
| c5 | concrete_reference −0.04, relevant +0.02, three questions ±0.01 |
| c1, c3 | ≤ 0.01 |

## Frozen task set

| | held-out | ContextBench |
|---|---|---|
| source | 8 fresh upstream clones (`results/freeze2.json`); `make_tasks.py` max 20 per repo → 160 raw → 151 at parent (7 had no gold at the parent, 2 near-duplicates) → 149 primary (2 fallback) | pinned parquet `2f56535b…`; 1,050 instances after exclusions |
| draws → eligible | 108 → **80** (first 10 eligible per repo; 28 failed the file-view eligibility rule) | 88 → **69** (17 failed the file-view eligibility rule, 2 failed checkout) |
| strata | description 20, quoted 3, other 57 | description 25, quoted 34, other 10 |
| post-date (> 2026-09-20) | 1 (G3d not applicable, as declared at freeze 1) | n/a (public; possibly seen) |

1,710 view files (11.5 per task). psf/requests and BurntSushi/ripgrep appear in both
sets and count as one repo each. Both sets met their expected sizes from the feasibility
check.

## POST-HOC: Jev repeatability (not preregistered; does not affect the verdict)

Written after V3 failed: `scripts/posthoc_stability.py` → `results/posthoc-stability.jsonl`.
It uses the 5 synthetic canaries only, each sent 10× sequentially and 10× concurrently (100
requests, all reporting `jev-1.13.0`, 0 failures).

- **Identical input gives different probabilities.** 69 of the 70 pre/post canary values
  (5 canaries × 7 questions × 2 passes) fall inside the spread of the 20 repeats. So the V3
  failure is per-request nondeterminism, not a model swap behind the ID. Sequential and
  concurrent repeats spread alike, so server-side batching is not the cause.
- **The noise depends on confidence.** Answers near 0 or 1 vary by ±0.01–0.02. Mid-range
  answers vary a lot: c2 `concrete_reference` 0.44–0.63, c5 `concrete_reference`
  0.27–0.38, c4 `in_scope` 0.29–0.37, c4 `primary` 0.26–0.33.
- **The frozen combined score S is much steadier.** S is the mean of P(primary),
  P(in_scope) and P(implementation). Its per-canary SD over the 20 repeats is 0.0015–0.0078
  and its range 0.007–0.033. The 0.19 swing was in `concrete_reference`, which S does not
  use. V3's 0.02 bound applies per question, so it was stricter than S's own noise.
- **Exposure in the evaluation run** (label-free):
  - 57.7 % of the 1,710 files have S in (0.15, 0.85), where per-question noise is largest;
  - 10.0 % of within-task file pairs (912 of 9,103) have an S gap smaller than
    2·√2·0.0078, so their order could flip on a re-request.
- **Unresolved:** the one out-of-range value is c4's pre-run `in_scope` (0.39), above all
  20 later repeats (0.29–0.37). One tail value in 70 is weak evidence of drift over time,
  but it is not excluded.
- **Limitations:** five short synthetic states, about 12 minutes apart from the run. The
  evaluation states are longer (838–1,981 input tokens per typed request), and nothing
  here measures noise on them.

## §7 Operational record (measured; not part of the verdict)

**Latency.** Scoring run, concurrency 12:
- per request p50 / p95 / p99: **510 / 611 / 653 ms**;
- per task wall time (about 23 requests in parallel) p50 / p95: **1,038 / 1,277 ms**.

50 sequential synthetic states at matched sizes: p50 512 ms, p95 578 ms; the cold first
request took 511 ms, so there was no warm-up effect. OXIDE's synchronous budget is a median
of ≤ 250 ms per request. One Jev request alone is about 2× that budget, before any OXIDE
work, and a 12-file task fan-out is about 4×.

**Failure behaviour.**
- Scoring run: 0 timeouts, 0 4xx/5xx, 0 rate limits, 0 retries; 3,420 HTTP 200s.
- Network off (`unshare -rn`, synthetic state; `results/offline.json`): every attempt
  fails at once (`Errno 101`), and the call gives up after **7.0 s** of retry backoff
  (1 + 2 + 4 s).

A local-first tool would need a hard timeout and a fallback to the local fused order,
without the backoff on the synchronous path (noted, not designed).

**Network dependency.** `POST https://api.typesafe.ai/v1/systemone`; no region is
documented. A hosted call breaks OXIDE's offline guarantee.

**Privacy and data retention** (accessed 2026-10-04). Only public open-source task text
and source were sent. The API key came from the gitignored `.env` and was never logged.

- [Privacy Policy](https://typesafe.ai/legal/privacy-policy): "We will not train or fine
  tune any artificial intelligence or machine learning models on your prompts or other
  Input." and "We retain personal data about you for as long as reasonably necessary to
  provide you with the Services, or otherwise in support of our business or commercial
  purposes."
- [Data Processing Addendum](https://typesafe.ai/legal/data-processing) §8: "Customer
  Personal Data will be retained for as long as necessary taking into account the purpose
  of the Processing, and in compliance with applicable laws".
- [Legal](https://docs.typesafe.ai/legal): zero data retention is offered only to
  enterprise customers.
- Request logging is not addressed in either document.

**Cost.** [Models page](https://docs.typesafe.ai/models), accessed 2026-10-04: $0.042 per
million input tokens, output free; rate limits 80 requests/s and 100K tokens/s.

| arm | input tokens / file | input tokens / task (12-file view) | projected cost / 1,000 queries |
|---|---:|---:|---:|
| typed | 1,348 | 15,468 | **$0.65** |
| no-source (`meta`) | 930 | 10,678 | $0.45 |

The whole scoring run used 3,895,718 input tokens over 3,420 requests: **$0.16**.

**Indexing** (under the caps; not part of the Jev cost):
- held-out parent indexes took 12–73 s each (one rayon parent: 3,606 symbols, 58 s, of
  which 1.3 s is non-embedding work);
- ContextBench indexes took 16 s – 25 min (a 70,603-symbol TypeScript monorepo).

Index time is dominated by the neural embedding of every symbol, and nothing is reused
across per-commit worktrees.

## Incidents and harness changes (all before freeze 2 and before any Jev evaluation call)

- **Shutdown during generation.** The machine was shut down mid-rayon. Rayon's possibly
  partial worktrees were removed and the repo regenerated from scratch. The 6 finished
  repos were kept: their tasks were already written, and their indexes completed before
  emission.
- **CB crash 1.** A shadowed `rows` variable in the draw loop crashed at the start of the
  quoted stratum. After the fix, the 25 description draws replayed byte-identically.
- **CB crash 2.** `fused_dump` output was parsed with `str.splitlines()`, which also splits
  on the `U+2028` that serde_json leaves unescaped. The dump itself had succeeded. Output is
  now parsed on `\n`, and the replay is identical. `build.py`'s source reader keeps
  `prep.py`'s `splitlines()` per the protocol; 0 of the 1,710 view files contain such a
  separator.
- **Parallelism.** Parent indexing ran 2-wide (side indexers working backward, stopping
  before the main loop's position). Same commands, so the indexes are identical; speed
  only. The CPU cap went from 600 % to 400 % (throttling only).
- **Harness additions** (described in `ff0f0c8`): `cand` rows for arm B, `packs.py` (V5,
  G5), the artifact-free J1+F check, the §7 ops record, the V4 check that committed task
  files equal the cache copies, and the network-off probe.
- **Jev calls before the run** (synthetic only): 1 smoke request, the offline probe, and a
  harness dry run on random scores (not Jev).

## What a follow-up would need

A new preregistration, as §6 requires for any rerun. The frozen task set, states and
unread scores here can be reused, because nothing has been compared with labels. It would
need to settle, before any data:
1. a V3 that tolerates measured per-request noise: for example, a bound on S (or on each
   gated scorer) set from a fresh repeatability run, rather than a per-question 0.02;
2. whether to score each file k times and average, with k chosen from that noise and the
   cost (k = 3 is about $2 per 1,000 queries at this view size);
3. whether to drop `concrete_reference` (the noisiest question) from the continuity
   scorers.

## Files

- `PROTOCOL.md`, `PROTOCOL.sha256`: freeze 1.
- `results/prereg.sha256`, `results/freeze2.json`: freeze 2 hashes and pins.
- `tasks/`: frozen task, gold, draw and exclusion files (`*-frozen.jsonl.gz` are the run's
  inputs).
- `results/states.jsonl.gz`: typed and no-source states.
- `results/scores.jsonl`: every raw response, both arms, with per-request timings and usage.
- `results/canary-{pre,post}.jsonl`, `results/latency.jsonl`, `results/offline.json`:
  validity and §7.
- `results/parity_v5.tsv`: V5. `results/packs.jsonl`: G5 pack arms (computed, not scored;
  sealed with the gates).
- `results/eval.json`, `results/eval.txt`: the frozen `eval_40.py` output (stops at
  validity).
- `results/posthoc-stability.jsonl`, `scripts/posthoc_stability.py`: POST-HOC.

## Reproduce

```sh
# binaries: 84c4d4a (frozen) and 84c4d4a + research.patch, release, into
# ~/.cache/oxide-jev-eval/target-{frozen,research}; Python: results/freeze2.json packages
python scripts/excl.py 84c4d4a <repos> ~/.cache/oxide-jev-eval/excl --expected-n 2026-09-20
bash scripts/gen_heldout.sh                       # held-out raw → parent → filters
python scripts/build.py heldout && python scripts/build.py cb <parquet>
python scripts/score_jev.py states                # no network
python scripts/packs.py parity                    # V5
python scripts/score_jev.py canary pre && python scripts/score_jev.py run && python scripts/score_jev.py canary post
python scripts/score_jev.py latency; unshare -rn python scripts/score_jev.py offline
python scripts/packs.py arms && python scripts/eval_40.py
```
