# `CONTEXT_MAX_PRIMARIES` sensitivity (frozen 21-task ContextBench)

**Causal question**: Phase 2 (`docs/cpu-embedding-survey/phase2-arctic-quality-gate.md`)
found that every task where `arctic-embed-xs-q` had a gold file in the
candidate pool but not in the finished context pack carried the same
`ContextPack.omitted[].why`: **`beyond primary cap`**. Four tasks, one reason.
Do those four losses explain Arctic's budgeted deficit — i.e. is the shortfall
an allocator decision rather than a weakness of the embedding model?

**Answer: the mechanism is confirmed, the attribution is not.** Raising the cap
demonstrably recovers all four — the omission reason disappears and the gold
returns to the scored pack. But a second model sweeps almost identically, so
the cap is a general allocator/budget trade, not something that particularly
penalises a small embedder. The shipped default is unchanged.

---

## 1. What changed in the code

One env override, `$OXIDE_CONTEXT_MAX_PRIMARIES`, mirroring
`resolve_term_coverage_alpha`'s established shape exactly: any parse failure,
including unset, falls back to the frozen `config::CONTEXT_MAX_PRIMARIES`. Unset
is byte-identical to the pre-change allocator.

`tests/context_max_primaries_override.rs` pins both halves — unset yields the
shipped cap, a parseable value replaces it, an unparseable one falls back
rather than panicking. It lives in its own test binary for the same reason
`tests/debug_dump_kept.rs` does (commit `f66a4d6`): the variable is
process-global and `src/context.rs` has a dozen other tests calling
`build_context` in that binary, one of which asserts the exact shipped cap.

Nothing else was touched. No fusion weight, no relevance floor, no expansion
bound, no token budget, no diversity or test cap.

---

## 2. Method

Same frozen 21-task pin, same pinned repository commits, same lexical index,
same symbol corpus, same `ranking_metrics.py` scoring math as Phase 2 and
Phase 3. Only `$OXIDE_CONTEXT_MAX_PRIMARIES` varies within a model's sweep.

Indexes were left warm between cap points, so a sweep re-runs retrieval and
allocation over byte-identical vectors — no re-embedding, no opportunity for
corpus drift inside a sweep.

**Two controls, both passed:**

- **Determinism.** The `cap 5` point is the shipped default, so it must
  reproduce Phase 2's committed Arctic run exactly. It does: all 21
  `kept_pool_probe.py` records are field-for-field identical (pool membership,
  pack contents, omission reasons, gold sets, query hashes, fingerprint), and
  every aggregate cell matches.
- **Locality.** The cap acts only on allocation, so `lexical`, `vec` and
  `hybrid` must not move at all. They are bit-identical across cap 5, 8 and 12
  for both models — every digit of R@1/R@5/R@10/hit@5/MRR/nDCG@10/tokens.

---

## 3. Results

Only `budgeted` is shown; the other three conditions are unchanged by
construction (see the locality control above).

### `arctic-embed-xs-q`

| Metric | cap 5 (shipped) | cap 8 | cap 12 |
|---|---:|---:|---:|
| R@5 | 0.663 | **0.710** | 0.710 |
| R@10 | 0.675 | **0.796** | 0.796 |
| hit@5 | 0.810 | **0.857** | 0.857 |
| MRR | 0.615 | 0.648 | 0.648 |
| nDCG@10 | 0.603 | 0.658 | 0.658 |
| tokens | 1932 | 2629 (+36%) | 2942 (+52%) |
| items | 7.1 | 9.3 | 10.5 |

### `minilm-l6-v2` (fp32)

| Metric | cap 5 (shipped) | cap 8 | cap 12 |
|---|---:|---:|---:|
| R@5 | 0.639 | **0.683** | 0.683 |
| R@10 | 0.639 | **0.718** | 0.730 |
| hit@5 | 0.810 | **0.857** | 0.857 |
| MRR | 0.651 | 0.660 | 0.660 |
| nDCG@10 | 0.579 | 0.612 | 0.618 |
| tokens | 1801 | 2418 (+34%) | 2680 (+49%) |
| items | 7.2 | 9.2 | 10.0 |

Quality saturates at cap 8 on R@5 for both models; token cost does not.

---

## 4. Mechanism — the four losses, individually

From the scored per-task sink, not the probe, so no probe-to-sink join is
needed. Arctic's four total pool-to-pack losses at cap 5 all carried the single
reason `beyond primary cap`:

| Task | gold files | budgeted R@10, cap 5 → cap 8 | budgeted R@5, cap 5 → cap 8 |
|---|---:|---|---|
| `10750f29` | 7 | 0.000 → **0.143** | 0.000 → 0.000 |
| `1409977d` | 3 | 0.000 → **0.333** | 0.000 → 0.000 |
| `da598baa` | 3 | 0.000 → **0.333** | 0.000 → 0.000 |
| `2fb50735` | 1 | 0.000 → **1.000** | 0.000 → **1.000** |

All four recover gold into the pack, and the `beyond primary cap` reason is
gone from all four in the cap-8 probe. **The mechanism is confirmed: those
losses were allocator decisions, not retrieval failures.**

But only `2fb50735` converts at the scored R@5 predicate. The other three
return below rank 5. So Arctic's entire +0.047 aggregate budgeted R@5 gain is
**one task out of twenty-one** (1/21 = 0.0476). The R@10 gain (+0.121) is the
broader and more honest measure of what the cap recovers.

---

## 5. Why this does *not* attribute the deficit to the cap

The decisive observation is the second arm. MiniLM gains **+0.044** budgeted
R@5 from the same cap change; Arctic gains **+0.047**. Two different embedding
models, near-identical response. Both reach hit@5 0.857 at cap 8. Whatever the
cap is doing, it is not compensating for a specific model's semantic weakness.

Raising it is also not monotonically good. Per-task, cap 5 → cap 8:

- both models gain `2fb50735` (0.000 → 1.000)
- MiniLM additionally gains `36989b6d` (0.333 → 0.500)
- MiniLM **regresses** on `42165c4e` (0.500 → **0.250**)

That regression is the mechanism working against itself: R@5 scores the first
five *files* in the pack, so admitting more primaries can push a gold file out
of the top five even while adding others further down. A larger cap buys recall
at depth and pays for it in precision at the head, plus a third more tokens.

**The control this experiment does not have:** the `qwen3-Q8_0` arm was started
and cut short at the user's direction after ~11 of 21 tasks (a full Qwen pass
costs ~3.5h of server-backed re-embedding, and the model was being retired as
the default in the same session). The partial sink was discarded rather than
reported. So the strongest statement supported here is "the cap benefits two
different small models near-identically", not "it benefits every model
identically". A Qwen arm would have made that third point; its absence is why
§6 recommends no change rather than a change.

---

## 6. Verdict

**Do not change `CONTEXT_MAX_PRIMARIES`.** Three reasons, in order of weight:

1. **It is not the model-specific fix the question hoped for.** Both models
   respond the same, so raising the cap does not close a gap *between* models —
   it moves both, which is a re-baselining decision about the allocator, not a
   finding about embedders.
2. **The R@5 gain is one task.** n=21 with a single-task mover is not evidence
   that survives contact with a different task set.
3. **It costs a third more tokens** (+36% Arctic, +34% MiniLM) and demonstrably
   regresses at least one task. `oxide context` sells signal per token; a change
   that spends 36% more of the budget needs a much stronger result than this.

Per `src/config.rs`'s own rule, promoting a different value would require a
fresh canonical benchmark and an intentional re-baseline. This evidence does
not justify opening that.

**What is worth keeping** is the finding underneath: under `arctic-embed-xs-q`
the candidate pool contains a gold file on **21/21** tasks, and every remaining
pack-level loss is an allocator decision. That is a much better place to be
than a retrieval miss, and it means further gains on this corpus are an
allocator problem — ranking within the pack, the per-file diversity cap, the
subsumption rules — not an embedding-model problem. That is the direction a
follow-up should take.

---

## 7. Known gaps

- **No Qwen arm** (§5). Two models, both small, both 384-dimension.
- **Per-task stage evidence thins out at higher caps.** Packs get longer, and
  `compare_per_task.py` correctly discards stage evidence for any task whose
  scored list fills the recorded 10-file cap (5 of 21 tasks at cap 8, most at
  cap 12), since full-pack equality with the probe becomes unknowable. §4 works
  around this by reading the scored sink directly.
- **Only three cap points** (5, 8, 12), chosen because the observed candidate
  pools hold 8–14 items — cap 12 is effectively "no cap" for this corpus.
- **`oxide status` cannot read the five pylint worktrees** (pre-existing
  non-UTF-8 source file), so the probe records `staleness_check: "unavailable"`
  there. Unchanged from Phase 2.

---

## 8. Reproducing

```bash
cargo build --release
OUT=eval-agent/results/primary_cap
export OXIDE_EMBED_NATIVE=arctic-embed-xs-q     # or minilm-l6-v2
for cap in 5 8 12; do
  if [ "$cap" = 5 ]; then unset OXIDE_CONTEXT_MAX_PRIMARIES
  else export OXIDE_CONTEXT_MAX_PRIMARIES=$cap; fi
  OXIDE_RANKING_PER_TASK_OUT=$OUT/per_task_arctic_cap$cap.jsonl \
    eval-agent/.venv/bin/python eval-agent/benchmark/ranking_metrics.py \
    | tee $OUT/ranking_arctic_cap$cap.txt
  eval-agent/.venv/bin/python scripts/agent_eval/kept_pool_probe.py \
    --out $OUT/kept_arctic_cap$cap.jsonl
done
```

The determinism control is `diff <(jq -S . kept_arctic_cap5.jsonl) <(jq -S .
../arctic_phase2/kept_arctic.jsonl)` — cap 5 must reproduce the Phase 2 run.

Committed evidence: `eval-agent/results/primary_cap/` — per-task sinks,
kept-pool probes and aggregate tables for both models at all three cap points.
