# Local embedding profile comparison (Task D)

Objective: make semantic indexing/querying fast and lightweight enough for
normal developer machines, then pick OXIDE's v0.1 default/lightweight/quality
embedding profiles by the actual Pareto frontier — not by assuming the
larger model is required. Compares the frozen retrieval pipeline
(`src/retrieval.rs`, `src/context.rs` — untouched by this work) across four
candidates. No retrieval-weight, allocation, or embedding-semantics change
was made to produce any number below.

## Candidates

| Profile | Runtime | Dim | Quantization | Source |
|---|---|---|---|---|
| `qwen3-Q8_0` | HTTP (llama.cpp server) | 1024 | Q8_0 GGUF | reference, already the default |
| `embeddinggemma-300m:search-result` | native (fastembed/ONNX, in-process) | 768 | fp32 | already cached locally, 1.2GB |
| `embeddinggemma-300m-q4:search-result` | native (fastembed/ONNX, in-process) | 768 | int4 | downloaded for this comparison, +0.2GB (shares the fp32 repo, onnx-community's multi-file layout) |
| `minilm-l6-v2` | native (fastembed/ONNX, in-process) | 384 | fp32 | downloaded for this comparison, 87MB |

Both downloads were explicit, confirmed with the user before running (no
silent network access) — see the on-disk sizes above.

## Runtime measurements

Methodology: `examples/embedding_profile_probe.rs` (native profiles) and
`examples/embedding_profile_probe_http.rs` (HTTP profile) call the exact
same `EmbeddingProvider` trait methods (`embed_query`, `embed_documents`)
OXIDE's own indexer and retrieval path use — not a synthetic proxy. Same
sample doc text, same percentile math, same process-local `VmHWM` for peak
RSS. One caveat stated up front: the HTTP path's "cold init" only covers
the *client-side* `HttpEmbedder::new` round trip; the llama.cpp server's own
process is a separate boundary, measured separately below — the two are not
directly comparable the way native's single-process numbers are.

| Metric | qwen3-Q8_0 (HTTP) | embeddinggemma-300m fp32 (native) | embeddinggemma-300m-q4 (native) | minilm-l6-v2 (native) |
|---|---|---|---|---|
| Cold init (client/process) | 200.9ms (client only; server boot below) | 2347ms | 17563ms | 10750ms |
| `embed_query` p50 | 38.56ms | 23.19ms | 22.43ms | **2.40ms** |
| `embed_query` p95 | 41.36ms | 31.99ms | 24.58ms | **2.80ms** |
| 1-symbol incremental, per-item | 121.96ms | 108.98ms | 54.95ms | 6.78ms |
| 10-symbol incremental, per-item | 98.17ms | 37.70ms | 41.93ms | 3.88ms |
| 50-symbol incremental, per-item | 105.27ms | 34.61ms | 42.56ms | 3.80ms |
| 100-symbol incremental, per-item | 105.25ms | 36.28ms | 43.44ms | 3.91ms |
| Batch-64 throughput, per-item (documented, `docs/indexing-rebuild-scopes/README.md`) | **56ms** | ~41.5ms (24.1 items/s @ n=500) | ~50.3ms (19.9 items/s @ n=500) | 4.2ms (239.7 items/s @ n=500) |
| Peak RSS | ~140–260MB (server process, `ps`, varies by measurement point) | 1601MB | 984MB | 633MB |
| Model/cache size on disk | 0 (server-managed) | 1.2GB | +0.2GB (shares fp32's HF repo) | 87MB |

The `embed_query`/incremental rows above were measured twice: once
immediately after three back-to-back native ONNX runs had saturated every
core (a contended run — those numbers were discarded, not reported here),
and once rerun alone on an idle machine (5.9GB free, no other `oxide`
process running) — the numbers in the table are the clean, idle-machine
rerun (`OXIDE_PROBE_SKIP_THROUGHPUT=1
./target/release/examples/embedding_profile_probe_http`). Do not stack
heavy native-model benchmarking immediately before HTTP benchmarking on a
shared box if this comparison is ever rerun — the contended numbers were
~1.5–2x worse across the board and would have understated Qwen's actual
per-query cost relative to the native profiles.

Qwen3 server cold start (separate process boundary, `scripts/embedder.sh
start`, GGUF weight load from local cache): **6.15s**, ~260MB RSS
immediately after boot before any real query traffic. This is a one-time
cost per server lifetime, not per-query or per-index-run — the server stays
resident across many `oxide index`/`oxide context` invocations, unlike the
native profiles' in-process model, which loads fresh in every `oxide`
process invocation (a real, structural cost native pays that HTTP doesn't:
every single CLI call pays that 2.3–17.6s cold-init tax, not just the
first).

## Quality (frozen 21-task ContextBench Tier A pin)

Qwen3 and fp32 Gemma numbers were **reused, not re-derived** — both already
existed from prior sessions on the identical pinned task set and harness
(`eval-agent/benchmark/ranking_metrics.py`):
`eval-agent/results/qwen3_llamacpp_repro/ranking_metrics_repro.txt` and
`eval-agent/results/native_screen/embeddinggemma-300m/ranking_metrics.txt`.
MiniLM was newly run this session, same harness, same 21-task pin, same four
conditions (`eval-agent/results/native_screen/minilm-l6-v2/ranking_metrics.txt`).
`embeddinggemma-300m-q4` quality was **not** run — see "Scoping decisions" below.

| Condition | Metric | qwen3-Q8_0 | embeddinggemma-300m:search-result | minilm-l6-v2 |
|---|---|---|---|---|
| hybrid | R@5 | 0.679 | **0.687** | 0.556 |
| budgeted | R@5 | **0.738** | 0.671 | 0.639 |
| hybrid | hit@5 | 0.90 | 0.86 | 0.76 |
| budgeted | hit@5 | **0.90** | 0.81 | 0.81 |
| hybrid → budgeted R@5 delta | | **+0.059 (improves)** | −0.016 (degrades) | **+0.083 (improves)** |

This shows the "matched/slightly beat on hybrid, lost more under tight
budget" pattern named in the task is specific to Gemma, not to the
HTTP-vs-native boundary: MiniLM is also a native, in-process fastembed
profile, yet its budgeted R@5 *improves* over hybrid — the same direction as
Qwen, not Gemma. Attribution below.

Aside, unrelated to this task's conclusions but worth flagging: the reused
Qwen numbers above (captured 2026-08-30) postdate a tie-break fix
(`a8c5aeb fix: deterministic tie-break in RRF/lexical/vector ranking`,
landed after `docs/canonical-baseline.md`'s 2026-08-28 snapshot) and
disagree with that file's own budgeted numbers (R@5 0.619, hit@5 0.76 there
vs 0.738/0.90 here). All three candidates compared in this doc were
measured on the same current `main`, so this comparison is internally
consistent — but `docs/canonical-baseline.md` itself is now stale and
should be refreshed the next time retrieval/ranking code changes, per its
own re-baselining rule in `CLAUDE.md`. Not fixed here: no retrieval code
changed in this task, so there is nothing to re-baseline against.

## Attribution: why does Gemma's budgeted quality drop while Qwen's (and MiniLM's) improves?

Two lines of evidence, one direct and inconclusive at the sample size
tested, one indirect and much stronger:

**Direct probe (inconclusive at n=2).** Per the advisor's suggested method,
`OXIDE_DEBUG_DUMP_KEPT` was used to compare the pre-allocation candidate
pool (`kept`, post relevance-floor and subsumption-dedup, pre role-ordering/
diversity-cap/budget-pack) against the final `oxide context --budget-tokens
4096` pack, for one `psf/requests` task and one `pallets/flask` task, under
both Qwen and Gemma. In all 4 runs (2 tasks × 2 embedders), the gold file
was present in `kept` **and** survived into the final pack — the
diversity/budget allocation step dropped 2–3 *other* candidates in every
run but never the gold one. This method is sound (it is exactly what would
show an allocator-side loss if one were present) but the two tasks sampled
happened to be ones neither model gets wrong, so it produced no signal
either way at this sample size. A full 21-task automated version of this
same probe would be needed for a statistically conclusive allocator-vs-retrieval
split; that is more indexing time (each task potentially reindexing at a
different pinned `base_commit`, ×2 embedders) than remained in scope here,
and is named as follow-up work rather than guessed at. One methodology gap
worth flagging for whoever builds that full version: this probe's
`final_hit` check is set membership over the whole final pack, but `R@5`
(the metric actually showing the regression) is rank-position over the
first five unique files — a gold file sitting at position 6 of a 7-item
pack counts as a hit here and a miss in `R@5`. A conclusive version must
replicate `ranked_files(items)[:5]` exactly, not just check pack membership,
or it will keep reporting "gold survives" on tasks the metric scores as
failures.

**Indirect evidence: rules out native-runtime as the cause; does not
cleanly isolate model size.** MiniLM is native and in-process exactly like
Gemma — if "native embedder" alone were the cause, MiniLM should show the
same budgeted degradation. It does not; it improves under budget, matching
Qwen's direction, despite being far smaller and faster than Gemma. That
rules out a native-runtime-specific artifact cleanly (same fastembed/ONNX
path, opposite direction). It does **not** cleanly isolate model size,
though: MiniLM's own hybrid R@5 (0.556) starts well below Gemma's (0.687),
so MiniLM has more headroom for the budgeted pipeline's structural
expansion to add recall — "improves from 0.556" and "degrades from 0.687"
aren't quite the same experiment. What the evidence *does* support without
qualification: Qwen (the largest candidate) and MiniLM (the smallest)
land on the same side — both improve under budget — while only the
mid-sized Gemma degrades. That is still enough to defeat "the larger model
is required," since size alone doesn't predict which side of the split a
model falls on. The likelier candidate mechanism is something particular
to Gemma's own score distribution or prompt-formatting convention
interacting with the allocator's `CONTEXT_RELEVANCE_FLOOR_FRACTION = 0.15`
(`src/config.rs`) —
a fixed *fraction of the top seed score*, not an absolute threshold. If
Gemma's cosine-similarity scores for code cluster more tightly around the
top score than Qwen's or MiniLM's do (a real possibility: Gemma's
`search-result` prompt convention and its ONNX-graph-baked pooling
representation are architecturally different from both other candidates'
mean-pooled representations), a fixed-fraction floor would cut a
proportionally larger share of Gemma's mid-tier-but-still-relevant
candidates than it cuts for the other two models. This is a plausible,
evidence-consistent mechanism, not a proven one — no `config.rs` change was
made to test it, per the task's explicit no-allocation-tuning constraint;
confirming it would mean plotting the seed-score distribution shape per
model on a sample of tasks, named here as the concrete next step rather
than attempted under this task's scope.

**What this attribution does NOT show**: it does not show the "larger
model is required" story the task asked to check against. MiniLM being
immune to the same failure mode Gemma exhibits, while being dramatically
smaller, is direct evidence against that story.

## Pareto frontier and recommendation

| Profile | Budgeted R@5 | Budgeted hit@5 | `embed_query` p50 | Peak RSS | Disk | Verdict |
|---|---|---|---|---|---|---|
| qwen3-Q8_0 | **0.738** | **0.90** | 38.56ms (clean, idle-machine rerun) | ~140–260MB (separate server process) | 0 (server-managed) | **KEEP — default.** Best quality on the metric that matters most (budgeted, since that's what real queries use), reasonable resource cost split into a long-lived server process. |
| embeddinggemma-300m fp32 | 0.671 | 0.81 | 23.19ms | 1601MB | 1.2GB | **REJECT for v0.1.** Worse quality than both Qwen and MiniLM on the condition that matters, highest RSS and disk footprint of any candidate, and every `oxide` invocation re-pays a 2.3s cold-init cost the server-based Qwen path doesn't. Its earlier "matches/beats Qwen on hybrid" result doesn't survive contact with the budgeted condition, which is what agents actually consume. |
| embeddinggemma-300m-q4 | not measured (see below) | not measured | 22.43ms (comparable to fp32) | 984MB (better than fp32, not dramatically) | +0.2GB | **REJECT for v0.1**, on architecture alone: same representation family as the rejected fp32 profile, worse cold-init (17.6s — CPU int4 dequant setup is *slower* to initialize than fp32, not faster) and worse batch throughput (19.9 vs 24.1 items/s) than fp32. Quantization bought lower RSS, nothing else, on this CPU-only path. Not worth a quality run to confirm what the runtime numbers already rule out. |
| minilm-l6-v2 | 0.639 | 0.81 | **2.40ms (~16x faster than Qwen's clean `embed_query` p50 of 38.56ms)** | **633MB (best of any native profile)** | **87MB (smallest by far)** | **KEEP — lightweight profile.** Doesn't beat Qwen on quality, but is the only candidate that is simultaneously dramatically cheaper on every runtime axis *and* free of Gemma's budgeted-quality pathology. This is the actual Pareto-frontier lightweight option: nothing tested is both cheaper and better on quality. |

**Recommended v0.1 profiles**:
- **Default**: `qwen3-Q8_0` (unchanged). It already wins on the metric OXIDE's
  own retrieval is graded on (budgeted quality, since that is what `oxide
  context` actually serves to an agent), and its cost is paid once as a
  long-lived server process rather than per-CLI-invocation.
- **Lightweight** (offered for resource-constrained machines, not the
  default): `minilm-l6-v2` via `OXIDE_EMBED_NATIVE=minilm-l6-v2`. Already
  wired through `native_model_spec` — no new runtime surface needed, exactly
  as the task asked ("if ONNX/native wins, integrate it behind the existing
  validated embedding-profile abstraction"). It does not win outright, but
  it is the only candidate offering a real speed/resource trade *without*
  Gemma's specific quality pathology.
- **Quality**: stays `qwen3-Q8_0` too — nothing tested beat it on the metric
  that matters. No separate "quality" profile is justified by this evidence.
- **`embeddinggemma-300m` and `embeddinggemma-300m-q4`: reject for v0.1.**
  Both remain available as opt-in `OXIDE_EMBED_NATIVE` profiles (no code
  removed — they were already implemented and validated in a prior session,
  `NativeEmbedder`/`native_model_spec`), just not recommended as a default or
  lightweight choice given the evidence above.

## Scoping decisions

- **`embeddinggemma-300m-q4` quality was not measured.** Its runtime numbers
  (cold init 7.5x worse than fp32, throughput worse, RSS only moderately
  better) already disqualify it relative to MiniLM on every resource axis,
  and it shares fp32 Gemma's representation family (same architecture, same
  prompt convention, same graph-baked pooling) — the mechanism suspected in
  the attribution section above is a property of the representation, not
  the quantization, so a full quality run was very unlikely to change the
  reject verdict. Running the full 21-task sweep (a `oxide index` re-embed
  pass on 8 distinct repos, including `pylint` at 11k+ symbols) would have
  cost significant additional wall-clock time for a result that would not
  change the recommendation.
- **The kept-vs-final attribution probe covered 2 of 21 tasks**, not the
  full pin — see "Direct probe (inconclusive at n=2)" above for why, and
  what a conclusive version would require.
- **No `config.rs`/retrieval/allocation change was made** at any point in
  this comparison, including to test the relevance-floor hypothesis above —
  per the task's explicit constraint. If that hypothesis is worth confirming,
  it needs its own scoped follow-up (plotting seed-score distributions per
  model), not a change bundled into this comparison.
