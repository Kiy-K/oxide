# Phase 2 — `arctic-embed-xs-q` quality gate (frozen 21-task ContextBench)

Phase 1 (`quantized-tiny-screen.md`) screened three quantized tiny embedders on
CPU cost and a 12-query sanity check, and kept exactly one for a real quality
run. This is that run.

**Causal question**: can Arctic XS Q provide substantially faster CPU semantic
indexing while preserving enough hybrid/budgeted retrieval quality to serve as
OXIDE's fast semantic stage?

This is a quality gate only. No production default changed, no dual-stage
indexing was implemented, and no retrieval, fusion, allocator, graph, token
budget, extraction or reference semantics were touched. The only source change
in this phase is one new read-only example (`examples/corpus_manifest.rs`) plus
eval-harness instrumentation; `src/` retrieval code is untouched.

---

## 1. Provenance

The artifact was resolved to an exact file, not a model name.

| Field | Value | Where it came from |
|---|---|---|
| OXIDE profile | `arctic-embed-xs-q` | `native_model_spec`, `src/embeddings.rs` |
| Recorded provider identity | `native:arctic-embed-xs-q` | index meta `embedder`, all 21 worktrees |
| fastembed enum | `SnowflakeArcticEmbedXSQ` | fastembed **6.0.2** (`Cargo.lock`); ort `2.0.0-rc.13` |
| HF repo | `Snowflake/snowflake-arctic-embed-xs` | fastembed `model_code` |
| **Resolved revision** | **`d8c86521100d3556476a063fc2342036d45c106f`** | `~/.cache/huggingface/hub/…/refs/main`, downloaded 2026-09-03 |
| ONNX artifact | `onnx/model_quantized.onnx`, 22,972,992 bytes | HF snapshot |
| **Artifact sha256** | `e6aa5e656466a73d7c3111e9a3378bd13e5b93af30eaac2b3f13fd56692589a1` | `sha256sum` |
| Quantization | int8; `QuantizationMode::Dynamic` | fastembed `impl.rs`. On disk `model_quantized.onnx` and `model_int8.onnx` resolve to the **same blob** |
| Dimension / architecture | 384 / BertModel, 6 layers, 12 heads, vocab 30522, max_seq 512 | `config.json` |
| Parameters | ≈22.6M — **derived** from `config.json` dimensions, *not* a published figure | computed |
| Pooling | CLS | `1_Pooling/config.json` **and** fastembed `Pooling::Cls` — independent sources agree |
| Normalization | L2 — **measured**, not assumed | `native_correctness_check arctic-embed-xs-q`: dim=384, unit norm, paraphrase 0.911 vs unrelated 0.574, PASS |
| Query prefix | `"Represent this sentence for searching relevant passages: "` | `config_sentence_transformers.json`; matches OXIDE's `query_prefix` byte-for-byte |
| Document prefix | none | Arctic convention |
| Fingerprint (sha256 of the stored JSON) | `15accf713122409be65d64de5fb30316…` | `manifests_arctic/PROVENANCE.tsv`, uniform across all 21 worktrees |

**Reproducibility limitation, stated rather than papered over.**
`EmbeddingSpaceFingerprint.artifact_revision` is the **empty string** for every
native profile — fastembed resolves `main` through hf-hub and exposes no
revision, and `NativeEmbedder::fingerprint`'s own comment says so instead of
inventing one. The fingerprint therefore separates Arctic-Q from Arctic-fp32
(quantization), from BGE/MiniLM (model), and from Qwen (model and dim), but it
would **not** notice a force-push to `main`. The revision above is recorded
here and in `PROVENANCE.tsv`; it is not enforced by the fingerprint. Closing
that needs revision pinning in the provider, which is out of scope here.

---

## 2. Baseline validity — reran, and it mattered

The existing canonical Qwen evidence
(`eval-agent/results/qwen3_llamacpp_repro/ranking_metrics_repro.txt`, captured
2026-08-30) is **not** comparable to a run on today's HEAD. Three reuse
conditions fail:

- **Source/document representation changed.** `86d9c25` migrated Python/TS
  extraction to `tree-sitter-tags`, changing which symbols exist and their
  spans — hence `embed_text` *and* the lexical document corpus.
- **Graph behaviour changed.** `8042bf1` (concurrent retrieval, `RetrievalMode`,
  bounded expansion), `c735f51`, and `c3cd9af` (ast-grep → precomputed
  structural relations) all post-date the capture. The `budgeted` condition
  runs straight through that path.
- Term-coverage scoring also landed (`87a00d0`, `1e20e47`), but it defaults to
  alpha=0 and is pinned byte-identical when unset
  (`term_coverage_alpha_unset_matches_explicit_zero_byte_for_byte`), so that one
  is **not** a reason.

Same 21-task pin, same pinned commits, same fusion weights, same allocator,
same 4096-token budget, same metric code — but reusing the stale numbers would
have been badly wrong:

| Condition | Metric | Stale (2026-08-30) | Fresh (this HEAD) | Δ |
|---|---|---:|---:|---:|
| vec | R@5 | 0.619 | 0.536 | −0.083 |
| hybrid | R@5 | 0.679 | 0.639 | −0.040 |
| budgeted | R@5 | 0.738 | 0.698 | −0.040 |
| hybrid / budgeted | hit@5 | 0.90 | 0.86 | −0.04 |

Every Arctic regression would have been **overstated by 0.04–0.08 R@5**, and
retrieval-migration effects would have been attributed to the model. The
paired baseline correction cost 213 minutes and was the right spend.

---

## 3. Corpus equality — the confound, falsified

`embed_text` includes `Symbol::references`, and cross-file reference resolution
can lag a run (`AGENTS.md`). Since the two providers index the same worktrees
in sequence, a corpus shift between them would confound model quality with
indexing order. Two independent checks, both passed:

**Check 1 — per-symbol manifests.** `examples/corpus_manifest.rs` dumps, per
symbol: id, file, span, kind, qualified name, stored `content_hash`, and the
exact `index::embed_text` string. Both hash columns are recorded because they
are **not** redundant: only the module fallback symbol's `content_hash` is
itself a hash of `embed_text` (the `update_index` override), so any other
symbol's `references` can change — changing what is embedded — while
`content_hash` stays put.

```
21 worktrees, 87,606 symbol lines
diff -rq manifests_qwen3/corpus manifests_arctic/corpus  →  no differences
combined sha256 (both):  f7d5e38f9fface62bce433704f2eee4a
```

The mechanism is structural, and a pilot run confirmed it before the full run
was spent: the embedding-space check lives in the **embedding stage only** —
it calls `clear_embeddings()` and never sets `force_reparse`. Switching
provider on `requests@39d0fdd9096f` reported `35 files scanned, 35 unchanged,
**0 reparsed**, 0 removed; embeddings: 831 written, 0 reused`, and that
worktree's manifest was byte-identical to Qwen's.

**Check 2 — the lexical condition as a free internal control.** Lexical
retrieval never touches embeddings, so if the corpus and lexical pipeline were
unchanged, its numbers must be bit-identical across the two runs. They are, on
every metric:

```
lexical  R@1 0.274  R@3 0.468  R@5 0.560  R@10 0.560  hit@5 0.71  MRR 0.504  nDCG@10 0.470  tok 3076
```

identical in both runs. This was not designed as a control; it fell out of the
experiment and independently corroborates Check 1.

Provider identity is recorded per worktree in `PROVENANCE.tsv` as
`(embedder, embedding_fingerprint)` — uniform within each capture, different
between them, which is exactly the intended asymmetry.

---

## 4. Aggregate metrics

21 tasks (17 Python SWE-Bench-Verified, 2 SWE-PolyBench TS, 2 Multi-SWE-Bench
TS). `ranking_metrics.py`, unchanged scoring math.

| Condition | Metric | Qwen3-Q8_0 | Arctic XS Q | Delta |
|---|---|---:|---:|---:|
| lexical | R@1 | 0.274 | 0.274 | — |
| lexical | R@5 | 0.560 | 0.560 | — |
| lexical | hit@5 | 0.710 | 0.710 | — |
| lexical | MRR | 0.504 | 0.504 | — |
| **vec** | R@1 | 0.353 | **0.397** | **+0.044** |
| **vec** | R@5 | 0.536 | **0.655** | **+0.119** |
| **vec** | R@10 | 0.536 | **0.667** | **+0.131** |
| **vec** | hit@5 | 0.670 | **0.810** | **+0.140** |
| **vec** | MRR | 0.536 | **0.615** | **+0.079** |
| **vec** | nDCG@10 | 0.495 | **0.593** | **+0.098** |
| hybrid | R@1 | 0.452 | 0.456 | +0.004 |
| hybrid | R@5 | 0.639 | 0.615 | −0.024 |
| hybrid | R@10 | 0.639 | 0.685 | +0.046 |
| hybrid | hit@5 | 0.860 | 0.760 | −0.100 |
| hybrid | MRR | 0.655 | 0.681 | +0.026 |
| hybrid | nDCG@10 | 0.590 | 0.624 | +0.034 |
| **budgeted** | R@1 | 0.452 | 0.421 | −0.031 |
| **budgeted** | R@5 | 0.698 | 0.663 | **−0.035** |
| **budgeted** | R@10 | 0.698 | 0.675 | −0.023 |
| **budgeted** | hit@5 | 0.860 | 0.810 | −0.050 |
| **budgeted** | MRR | 0.663 | 0.615 | −0.048 |
| **budgeted** | nDCG@10 | 0.628 | 0.603 | −0.025 |
| budgeted | tokens | 1808 | 1932 | +124 |
| budgeted | items | 6.9 | 7.1 | +0.2 |

The headline is not the one Phase 1 predicted. **Arctic is decisively better
than Qwen at vector-only retrieval** (+0.119 R@5, +0.140 hit@5, +0.098
nDCG@10) — a 384-dimension int8 22.6M-parameter model beating a 1024-dimension
Q8_0 0.6B model on this corpus. Hybrid is roughly a wash (better MRR, nDCG and
R@10; worse R@5 and hit@5). Budgeted is modestly behind on every metric.

`R@10 == R@5` throughout is not an artifact: ten symbol hits collapse to only
3.9–5.3 unique **files**, so ranks 6–10 are usually empty.

---

## 5. Per-task verdict

Budgeted R@5 per task, with the stage evidence OXIDE itself recorded.

| Task | n gold | Qwen budg R@5 | Arctic budg R@5 | Result | Stage evidence (Arctic) |
|---|---:|---:|---:|---|---|
| `2fb50735` | 1 | 1.000 | 0.000 | **regression** | pool=+, dropped by: beyond primary cap |
| `8d780f70` | 4 | 0.500 | 0.500 | flat | pool=+, in pack top-5=yes |
| `049a7048` | 1 | 1.000 | 1.000 | flat | pool=+, in pack top-5=yes |
| `07f7e78f` | 1 | 1.000 | 1.000 | flat | pool=+, in pack top-5=yes |
| `0eecae1e` | 1 | 1.000 | 1.000 | flat | pool=+, in pack top-5=yes |
| `10750f29` | 7 | 0.000 | 0.000 | flat | pool=+, dropped by: beyond primary cap |
| `1397ea97` | 1 | 1.000 | 1.000 | flat | pool=+, in pack top-5=yes |
| `1409977d` | 3 | 0.000 | 0.000 | flat | pool=+, dropped by: beyond primary cap |
| `1fdd9275` | 2 | 1.000 | 1.000 | flat | pool=+, in pack top-5=yes |
| `237decf1` | 1 | 1.000 | 1.000 | flat | pool=+, in pack top-5=yes |
| `23963510` | 2 | 0.500 | 0.500 | flat | pool=+, in pack top-5=yes |
| `2e76c8cd` | 1 | 1.000 | 1.000 | flat | pool=+, in pack top-5=yes |
| `36989b6d` | 6 | 0.333 | 0.500 | win | pool=+, in pack top-5=yes |
| `4b7ae9d9` | 1 | 1.000 | 1.000 | flat | pool=+, in pack top-5=yes |
| `51b4c299` | 1 | 1.000 | 1.000 | flat | pool=+, in pack top-5=yes |
| `60068eb0` | 2 | 1.000 | 1.000 | flat | pool=+, in pack top-5=yes |
| `88e1ffd3` | 1 | 1.000 | 1.000 | flat | pool=+, in pack top-5=yes |
| `9cca0774` | 3 | 0.333 | 0.667 | win | pool=+, in pack top-5=yes |
| `da598baa` | 3 | 0.000 | 0.000 | flat | pool=+, dropped by: beyond primary cap |
| `41cd3842` | 2 | 0.500 | 0.500 | flat | pool=+, in pack top-5=yes |
| `42165c4e` | 4 | 0.500 | 0.250 | **regression** | pool=+, dropped by: module subsumed by concrete symbols |

Win/flat/regression tally (R@5, per condition, n=21):

| Condition | win | flat | regression |
|---|---:|---:|---:|
| vec | 5 | 15 | 1 |
| hybrid | 3 | 16 | 2 |
| budgeted | 2 | 17 | 2 |

**11 of 21 tasks are completely flat across all three conditions.** The
aggregate budgeted delta of −0.035 rests on four tasks — two wins and two
regressions. No significance test was run and none is claimed; at n=21 with
four moving tasks, this difference is not distinguishable from noise on this
evidence.

---

## 6. Regression attribution

Stage evidence was usable for **21/21 tasks on both sides** after the
pack-agreement check (the probe's full pack and gold set had to equal the
scored one, or the task's stage evidence was discarded).

The single most decision-relevant number in this report:

| | gold reached the candidate pool | gold in pack top-5 |
|---|---:|---:|
| Qwen3-Q8_0 | 19/21 | 18/21 |
| **Arctic XS Q** | **21/21** | 17/21 |

**Arctic gets the gold file into the candidate pool on every task; Qwen misses
on two.** Arctic then converts one fewer of those into the pack's top 5. Every
one of Arctic's four pool→pack losses carries the same recorded reason:

```
2fb50735  dropped by: beyond primary cap
10750f29  dropped by: beyond primary cap
1409977d  dropped by: beyond primary cap     (Qwen loses this one too)
da598baa  dropped by: beyond primary cap
```

That is `CONTEXT_MAX_PRIMARIES` in `src/context.rs`, quoted from OXIDE's own
`ContextPack.omitted[].why` — not inferred. The mechanism is an *allocator*
interaction, and it is plausibly caused by Arctic surfacing **more** relevant
candidates, not fewer: more competing primaries in the pool means a fixed
primary cap is likelier to evict a gold one. This is a hypothesis consistent
with the pool counts above, not a proven cause — confirming it needs a
cap-sweep, which is out of scope here and would change allocator behaviour.

Against the requested taxonomy:

1. **Semantic representation loss — 1 task, fully rescued.** `23963510`: vec
   R@5 0.50→0.00, a genuine Arctic semantic miss. Downstream, `lex@10` held
   gold and both hybrid and budgeted stayed flat at 0.50. This is precisely
   the dual-stage hypothesis: vector quality regressed, context quality
   preserved.
2. **Lexical rescue — observed on `23963510`.** Gold was in `lexical@10` and
   absent from `vec@10`. Whether the lexical *arm* caused the hybrid hit is
   not established: RRF fuses both arms and structural expansion contributes,
   and nothing recorded separates them. The observation is reported; the
   causal claim is not made.
3. **Structural rescue — not isolable from this evidence.** Every ranked list
   is a top-10, so a gold file appearing in hybrid but in neither recorded
   top-10 came from expansion *or* from a sub-rank-10 hit on either arm.
   `2fb50735`'s Qwen row is exactly this case and is labelled unattributable
   rather than credited to expansion.
4. **Fusion/ranking loss — 1 task, absorbed downstream.** `41cd3842`: Arctic
   *improves* vec R@5 0.00→0.50, yet hybrid R@5 falls 0.50→0.00. Budgeted is
   flat at 0.50 — the pack recovered what fusion dropped.
5. **Allocator/context loss — the dominant mechanism.** `2fb50735` is the only
   hard, unrescued failure: hybrid and budgeted both 1.000→0.000, with gold
   present in the pool and dropped by `beyond primary cap`. `42165c4e`
   (budgeted 0.500→0.250, 4 gold files) is a partial pack-stage loss.
6. **Benchmark/corpus anomaly — ruled out**, by byte-identical manifests over
   87,606 symbols and the bit-identical lexical control.

**Where lexical + graph cannot rescue Arctic**: one task, `2fb50735`. Its cause
is recorded as a role cap, not a representation failure — Arctic *had* the
file.

---

## 7. Performance

Phase 1 evidence is reused, not re-derived; it was measured against
`jina-code-v2` on a single fixed corpus:

| | full-repo indexing | peak RSS | dim | `embed_query` p50 |
|---|---:|---:|---:|---:|
| `jina-code-v2` (Phase 1 reference) | 1025.5s | 996MB | 768 | — |
| `arctic-embed-xs-q` | **113.6s (9.03×)** | **230MB** | 384 | 1.80ms |

Measured **this session**, against the production default on the real
benchmark: the full 21-task harness took **213 min under `qwen3-Q8_0` (HTTP)
and 23 min under `arctic-embed-xs-q` (in-process)** — a 9.2× wall-clock ratio.

Read that ratio carefully. It is a wall-clock measurement of two harness runs,
not a controlled embedding-throughput benchmark: Arctic's run re-embedded all
87,606 symbols (a fingerprint change forces it), while Qwen's re-embedded an
unmeasured subset. The asymmetry, if anything, understates Arctic. Phase 1's
9.03× on a single fixed corpus is the controlled figure. Arctic's per-symbol
cost was directly observed once during the pilot: 831 symbols in 4430 ms
(5.33 ms/symbol).

Arctic also removes a whole moving part: it runs in-process via ONNX, so there
is no llama.cpp server to start, supervise or keep resident.

---

## 8. Pareto interpretation

Arctic is not merely cheaper-and-worse; it is **better on the vector stage and
behind on the budgeted stage**, at roughly a ninth of the cost and a quarter of
the memory.

- Nothing tested is both cheaper and better on budgeted quality, so Qwen stays
  on the frontier for budgeted output.
- Nothing tested is both cheaper and better on vector-only quality **than
  Arctic** — Arctic dominates Qwen there outright while also being far cheaper.
  It is on the frontier on its own merits, not as a cheap fallback.
- The gap that matters for OXIDE's actual product surface (`oxide context`,
  budgeted) is −0.035 R@5 and −0.05 hit@5, concentrated in four tasks, with the
  single hard failure attributable to a role cap rather than to semantics.

No single weighted score is offered; the two stages disagree and collapsing
them would hide exactly the finding that matters.

---

## 9. Verdict

**KEEP FOR DUAL-STAGE EXPERIMENT — `arctic-embed-xs-q`.**

The acceptance conditions set for this phase are met: the CPU/indexing
advantage was already proven and was reproduced end-to-end here; there is no
catastrophic hybrid or context regression; budgeted quality stays close
(−0.035 R@5); and the regressions are few, understood, and concentrated in a
stage that is not the embedding model.

The strongest evidence for a fast stage is not the aggregate table — it is that
Arctic put gold in the candidate pool on **21/21** tasks versus Qwen's 19/21.
As a first-pass recall stage feeding a later, more expensive stage, that is the
property that matters, and Arctic is ahead on it.

### Should Arctic XS Q become OXIDE's light/fast-stage embedding model?

**Yes, as the documented lightweight profile — and no change is made here.**

It is already reachable as `OXIDE_EMBED_NATIVE=arctic-embed-xs-q`; adopting it
as the *recommended* lightweight profile is a documentation decision, not a
code one, and belongs in a change that also re-measures the incumbent.

Three honest qualifications:

1. **It does not replace the default.** `qwen3-Q8_0` remains better on budgeted
   R@5, hit@5, MRR and nDCG@10 — the condition `oxide context` actually serves.
   Nothing here justifies changing the production default, and none was changed.
2. **The incumbent lightweight recommendation cannot be compared to this.**
   `docs/embedding-profile-comparison/README.md` recommends fp32
   `minilm-l6-v2`, but those numbers were captured 2026-09-02 on a pre-migration
   HEAD — the same staleness that invalidated the Qwen baseline above. Arctic
   beats *stale* MiniLM on every figure, and that comparison is worth nothing.
   Superseding that recommendation honestly requires re-running MiniLM on this
   HEAD (~25 min, native, no server).
3. **n=21, four moving tasks.** The budgeted gap is not statistically
   distinguishable from noise on this evidence, and the decision should be read
   as "close enough to justify the next experiment", not "measured equal".

### One recommended next action

Re-run fp32 `minilm-l6-v2` through this same 21-task pin on this HEAD, with the
same manifest-equality check, so the lightweight-profile recommendation rests
on one comparable set of numbers instead of two baselines a migration apart.
It is the cheapest run in the survey (native, in-process, ~25 min) and it is
the only thing standing between this evidence and a documentation change.

**Not** the dual-stage design — that question is now open, but it should be
opened deliberately, and the primary-cap interaction found above is a
prerequisite to understand first: a fast stage that surfaces *more* candidates
runs straight into `CONTEXT_MAX_PRIMARIES`.

---

## 10. Known gaps

- **Revision pinning.** The fingerprint carries no `artifact_revision`; a
  force-push to the HF repo's `main` would go undetected. Recorded, not fixed.
- **Structural-rescue attribution is not isolable** from top-10 ranked lists.
  Labelled unattributable rather than guessed.
- **Stage evidence is probe-derived.** `kept_pool_probe.py` re-runs `oxide
  context`; its pack and gold set were verified equal to the scored ones per
  task, but only the final pack is recorded on both sides, so identical
  intermediates are not *proven*. Labels are tagged `[probe]` in the
  comparison output.
- **Freshness check unavailable on 5 worktrees.** `oxide status` fails on any
  repo containing a non-UTF-8 file with an indexed extension —
  `scanner`'s binary sniff only rejects NUL bytes, so pylint's Latin-1
  `tests/functional/i/implicit/implicit_str_concat_latin1.py` passes it and
  `current_file_hashes`'s `read_to_string` then errors. `oxide index` and
  `oxide context` handle the same repo fine. This is a **pre-existing OXIDE
  bug, unrelated to this experiment and not fixed here**; the probe records
  `staleness_check: "unavailable: …"` on the affected rows rather than
  claiming a check that never ran.
- **The primary-cap hypothesis is unconfirmed.** That Arctic's larger pool
  causes the cap evictions is consistent with the recorded counts but was not
  tested; testing it means sweeping `CONTEXT_MAX_PRIMARIES`, which changes
  allocator behaviour and was out of scope.
- **Bulk manifests are gitignored.** The 35MB-per-provider per-symbol TSVs are
  regenerable; `corpus/INDEX.txt` (per-worktree symbol count + manifest
  sha256) and `PROVENANCE.tsv` are committed, so the equality claim stays
  checkable.

## Reproducing

```bash
cargo build --release --features native-embed
cargo build --release --example corpus_manifest

# The provider environment must stay active for EVERY command in a phase, not
# just the scoring one: `kept_pool_probe.py` re-runs `oxide context` itself, so
# without it the probe would run under the offline hashed embedder and its
# stage evidence would describe a different embedding space. Export, do not
# prefix a single command.

# --- baseline (needs the llama.cpp server: scripts/embedder.sh start) ---
export OXIDE_EMBED_URL=http://127.0.0.1:8191/v1/embeddings
export OXIDE_EMBED_MODEL=qwen3-Q8_0
unset OXIDE_EMBED_NATIVE                     # oxide prefers the URL; leaving both set is refused
OXIDE_RANKING_PER_TASK_OUT=…/per_task_qwen3.jsonl \
  eval-agent/.venv/bin/python eval-agent/benchmark/ranking_metrics.py
eval-agent/.venv/bin/python scripts/agent_eval/capture_corpus_manifests.py --out …/manifests_qwen3
eval-agent/.venv/bin/python scripts/agent_eval/kept_pool_probe.py --out …/kept_qwen3.jsonl

# --- candidate (in-process, no server) ---
unset OXIDE_EMBED_URL OXIDE_EMBED_MODEL
export OXIDE_EMBED_NATIVE=arctic-embed-xs-q
OXIDE_RANKING_PER_TASK_OUT=…/per_task_arctic.jsonl \
  eval-agent/.venv/bin/python eval-agent/benchmark/ranking_metrics.py
eval-agent/.venv/bin/python scripts/agent_eval/capture_corpus_manifests.py --out …/manifests_arctic
eval-agent/.venv/bin/python scripts/agent_eval/kept_pool_probe.py --out …/kept_arctic.jsonl

# the gate: corpus must be identical before the comparison means anything
diff -rq …/manifests_qwen3/corpus …/manifests_arctic/corpus
eval-agent/.venv/bin/python scripts/agent_eval/compare_per_task.py \
  …/per_task_qwen3.jsonl …/per_task_arctic.jsonl \
  --kept-baseline …/kept_qwen3.jsonl --kept-candidate …/kept_arctic.jsonl
```
