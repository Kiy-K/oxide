# Phase 3 — fp32 `minilm-l6-v2` rerun on this HEAD (frozen 21-task ContextBench)

Phase 2 (`phase2-arctic-quality-gate.md`) kept `arctic-embed-xs-q` for a
dual-stage experiment but recorded one qualification it could not resolve:

> **The incumbent lightweight recommendation cannot be compared to this.**
> `docs/embedding-profile-comparison/README.md` recommends fp32
> `minilm-l6-v2`, but those numbers were captured 2026-09-02 on a
> pre-migration HEAD […] Arctic beats *stale* MiniLM on every figure, and
> that comparison is worth nothing.

This is that rerun. **Causal question**: on today's HEAD, over the same pinned
21 tasks and the same symbol corpus, is `arctic-embed-xs-q` actually a better
lightweight profile than fp32 `minilm-l6-v2`, or was Phase 2's apparent lead an
artifact of comparing against a stale baseline?

No retrieval, fusion, allocator, graph, token-budget, extraction or reference
semantics were touched by this arm. The production *default embedder* did
change as a result of it — see §7.

---

## 1. Provenance

Resolved to an exact file, same discipline as Phase 2.

| Field | Value | Where it came from |
|---|---|---|
| OXIDE profile | `minilm-l6-v2` | `native_model_spec`, `src/embeddings.rs` |
| Recorded provider identity | `native:minilm-l6-v2` | index meta `embedder`, all 21 worktrees |
| fastembed enum | `AllMiniLML6V2` | fastembed **6.0.2** (`Cargo.lock`); ort `2.0.0-rc.13` |
| HF repo | `Qdrant/all-MiniLM-L6-v2-onnx` | fastembed `model_code` (`models/text_embedding.rs`) |
| **Resolved revision** | **`5f1b8cd78bc4fb444dd171e59b18f3a3af89a079`** | `~/.cache/huggingface/hub/…/refs/main` |
| ONNX artifact | `model.onnx`, 90,387,630 bytes | HF snapshot (the repo ships exactly one `.onnx`) |
| **Artifact sha256** | `bbd7b466f6d58e646fdc2bd5fd67b2f5e93c0b687011bd4548c420f7bd46f0c5` | `sha256sum` on the resolved blob |
| Quantization | fp32 — `AllMiniLML6V2` is absent from fastembed's `get_quantization_mode` match, so `QuantizationMode::None` | fastembed `text_embedding/impl.rs` |
| Dimension / architecture | 384 / BertModel, 6 layers, 12 heads, intermediate 1536, vocab 30522, max_seq 512 | `config.json` (upstream `sentence-transformers/all-MiniLM-L6-v2`) |
| Parameters | ≈22.7M — **derived** from `config.json` dimensions, not a published figure | computed; fp32 at that count ≈ 90.9MB, consistent with the 90.4MB artifact |
| Pooling | mean | fastembed `Pooling::Mean` for this enum, mirrored by OXIDE's `native_model_spec` |
| Normalization | L2 — **measured**, not assumed | `native_correctness_check minilm-l6-v2`: dim=384, unit norm, paraphrase 0.7983 vs unrelated −0.0743, PASS |
| Query prefix / document prefix | none / none | `native_model_spec`; MiniLM predates instruction prompts |
| Fingerprint | `{"schema_version":1,"model":"all-MiniLM-L6-v2","artifact_revision":"","quantization":"fp32",…,"pooling":"mean","normalization":"l2",…}` | `manifests_minilm/PROVENANCE.tsv`, uniform across all 21 worktrees |

Two limitations, stated rather than papered over:

- **Pooling has one source here, not two.** For Arctic, `1_Pooling/config.json`
  in the HF snapshot independently corroborated fastembed's `Pooling::Cls`.
  The `Qdrant/all-MiniLM-L6-v2-onnx` repo ships five files and none of them is
  a pooling config, so `mean` rests on fastembed's own table alone. The
  measured unit-norm/paraphrase check above constrains the *output*, not the
  pooling choice.
- **Revision pinning is still absent**, identically to Phase 2:
  `EmbeddingSpaceFingerprint.artifact_revision` is the empty string for every
  native profile. The revision above is recorded, not enforced.

The three fingerprints in play are pairwise distinct on `model`, and on
`quantization`/`pooling`/`query_profile` as well — no vector was ever reused
across spaces.

---

## 2. Corpus equality — verified before comparing

Phase 2's confound guard applies unchanged: `embed_text` includes
`references`, which can shift between indexing runs, so model order could
otherwise confound a paired comparison.

`capture_corpus_manifests.py` dumped every symbol (id, path, span, kind,
qualified name, `content_hash`, `embed_text` and its hash) for all 21 pinned
worktrees under MiniLM, and both diffs are clean:

```
diff -rq manifests_arctic/corpus manifests_minilm/corpus   → CORPUS_EQUAL_ARCTIC
diff -rq manifests_qwen3/corpus  manifests_minilm/corpus   → CORPUS_EQUAL_QWEN
```

The **`lexical` condition is the free control** and it holds exactly: R@1
0.274, R@5 0.560, hit@5 0.714, MRR 0.504, nDCG@10 0.470, tok 3076 — bit-identical
across all three providers, as it must be if the document corpus and the BM25
index are the same and only the vector space differs.

---

## 3. Aggregate metrics

21 tasks (17 Python SWE-Bench-Verified, 2 SWE-PolyBench TS, 2 Multi-SWE-Bench
TS), `ranking_metrics.py`, unchanged scoring math, shipped
`CONTEXT_MAX_PRIMARIES = 5`.

| Condition | Metric | Qwen3-Q8_0 | Arctic XS Q | MiniLM-L6-v2 (fp32) |
|---|---|---:|---:|---:|
| lexical | R@5 | 0.560 | 0.560 | 0.560 |
| lexical | hit@5 | 0.714 | 0.714 | 0.714 |
| **vec** | R@1 | 0.353 | **0.397** | 0.298 |
| **vec** | R@5 | 0.536 | **0.655** | 0.460 |
| **vec** | R@10 | 0.536 | **0.667** | 0.491 |
| **vec** | hit@5 | 0.667 | **0.810** | 0.619 |
| **vec** | MRR | 0.536 | **0.615** | 0.491 |
| **vec** | nDCG@10 | 0.495 | **0.593** | 0.441 |
| hybrid | R@1 | 0.452 | **0.456** | 0.274 |
| hybrid | R@5 | **0.639** | 0.615 | 0.563 |
| hybrid | R@10 | 0.639 | **0.685** | 0.563 |
| hybrid | hit@5 | **0.857** | 0.762 | 0.762 |
| hybrid | MRR | 0.655 | **0.681** | 0.552 |
| hybrid | nDCG@10 | 0.590 | **0.624** | 0.488 |
| **budgeted** | R@1 | **0.452** | 0.421 | 0.393 |
| **budgeted** | R@5 | **0.698** | 0.663 | 0.639 |
| **budgeted** | R@10 | **0.698** | 0.675 | 0.639 |
| **budgeted** | hit@5 | **0.857** | 0.810 | 0.810 |
| **budgeted** | MRR | **0.663** | 0.615 | 0.651 |
| **budgeted** | nDCG@10 | **0.628** | 0.603 | 0.579 |
| budgeted | tokens | 1808 | 1932 | **1801** |
| budgeted | items | 6.9 | 7.1 | 7.2 |

**Arctic is ahead of fresh MiniLM on every metric of `vec` and `hybrid` except
one tie, and on `budgeted` except one tie and one loss.** Stated exactly, since
"beats on everything" would not be true:

- `lexical` is tied on all six metrics — it is the control, identical by
  construction for every provider.
- `vec`: Arctic ahead on all six.
- `hybrid`: Arctic ahead on five; **hit@5 is tied** at 0.762.
- `budgeted`: Arctic ahead on R@1, R@5, R@10 and nDCG@10; **hit@5 is tied** at
  0.810; **MiniLM wins MRR** 0.651 vs 0.615.

The budgeted split is the interesting one: MiniLM more often puts *a* gold file
first, while Arctic more often gets *more* of the gold set into the pack — same
hit@5, higher R@5/R@10/nDCG@10.

**The staleness that invalidated the Qwen baseline barely moved MiniLM.**
The 2026-09-02 capture recorded budgeted R@5 **0.639** / hit@5 **0.81** and
hybrid R@5 0.556; today's rerun gives budgeted R@5 **0.639** / hit@5 **0.81**
and hybrid R@5 0.563. That is a coincidence worth naming rather than
generalising from: it establishes that Phase 2's MiniLM comparison happened to
reach the right *ordering*, and does **not** establish that stale numbers are
generally safe — the same migration moved Qwen's budgeted R@5 from 0.738 to
0.698. Rerunning was the only way to know which case this was.

---

## 4. Per-task verdict

`compare_per_task.py`, paired, metric `r@5`, stage evidence usable for 21/21
tasks on both sides of both comparisons.

| Comparison | Condition | win | flat | regression |
|---|---|---:|---:|---:|
| Qwen → MiniLM | vec | 3 | 11 | 7 |
| Qwen → MiniLM | hybrid | 2 | 16 | 3 |
| Qwen → MiniLM | **budgeted** | **0** | 19 | **2** |
| MiniLM → Arctic | vec | 9 | 10 | 2 |
| MiniLM → Arctic | hybrid | 3 | 17 | 1 |
| MiniLM → Arctic | **budgeted** | **3** | 17 | **1** |
| Qwen → Arctic *(Phase 2)* | vec | 5 | 15 | 1 |
| Qwen → Arctic *(Phase 2)* | hybrid | 3 | 16 | 2 |
| Qwen → Arctic *(Phase 2)* | **budgeted** | **2** | 17 | **2** |

MiniLM never wins a budgeted task against Qwen and loses two. Against MiniLM,
Arctic wins three budgeted tasks and loses one.

The third block is Phase 2's own paired run
(`eval-agent/results/arctic_phase2/per_task_comparison.txt`), included because
**pairwise tallies are not transitive** — the first two blocks on their own
establish only Qwen vs MiniLM and MiniLM vs Arctic, and could not order Qwen
against Arctic. With the direct comparison in hand, budgeted is 2-2 between
Qwen and Arctic on tasks moved, which is a genuine wash rather than the clear
Qwen lead the aggregate R@5 gap suggests; the ordering
**Qwen ≳ Arctic > MiniLM** on budgeted quality is what all three direct
comparisons plus the aggregate table support.

---

## 5. Where each model loses gold

The most informative result is not the aggregate — it is *at which stage* the
gold file disappears. From `kept_pool_probe.py`, which records the
pre-allocation candidate pool (dumped **before** the relevance floor) and
`ContextPack.omitted[].why` for gold-file candidates:

| Model | gold in candidate pool | gold anywhere in pack | gold in pack top-5 |
|---|---:|---:|---:|
| Qwen3-Q8_0 | 19/21 | 18/21 | 18/21 |
| **Arctic XS Q** | **21/21** | 17/21 | 17/21 |
| MiniLM-L6-v2 | 18/21 | 17/21 | 17/21 |

**Arctic surfaces a gold file into the candidate pool on every single task.**
Its four pack-level losses are all recorded with the reason
`beyond primary cap` — an allocator decision, not a retrieval failure.

Qwen (2 tasks) and MiniLM (3 tasks) fail *earlier*: the gold file is not in the
pool at all, so no allocator setting can recover it. The three tasks MiniLM
misses at pool level are `10750f29`, `1409977d` and `da598baa` — on all three,
Arctic has the gold in the pool and loses it only to the primary cap.

That distinction is what makes Arctic and MiniLM genuinely different candidates
rather than near-twins: they fail in different places, and only one of the two
failure modes is addressable without changing the model. The primary-cap
follow-up is in `docs/primary-cap-sensitivity/README.md`.

Read with the same caution Phase 2 recorded: these labels are the *probe's*
stages, joined to the scored run by checking the probe's full pack and gold set
against the scored pack per task. That check passed for 21/21 tasks on every
arm in this section.

---

## 6. Runtime

Not re-measured in this arm; reused from Phase 1
(`quantized-tiny-screen.md`) and the Stage-A survey (`README.md`), with the
gaps named.

| Axis | Arctic XS Q | MiniLM fp32 | Source |
|---|---:|---:|---|
| Full-repo embed pass (matplotlib, 18,532 symbols) | **113.6s** | *not measured* | Phase 1 measured `minilm-l6-v2-**q**` at 117.3s, not the fp32 profile |
| Peak RSS, same pass | **230MB** | *not measured* (int8 twin: 233MB) | Phase 1 |
| `embed_query` p50 | 1.80ms | 1.95ms (fp32, Stage A) | Phase 1 / survey README — different sessions, read as same-order, not as a ranking |
| 100-symbol incremental, per item | *not measured for the int8 profile* | 3.03ms | survey README |
| Weights on disk | **22.97MB** (`model_quantized.onnx`) | 90.39MB (`model.onnx`) | this doc's provenance table + Phase 1 |
| Vector dimension / `index.db` per symbol | 384 (tied) | 384 (tied) | survey README |

Disk is the one runtime axis measured cleanly for both here, and Arctic wins it
4x. Everything else is either a tie or a cross-session comparison that should
not be read as a ranking. **The recommendation below does not rest on runtime**
— it rests on quality, where the measurement is direct, paired and same-corpus.

---

## 7. Verdict

**Lightweight profile: switch the recommendation from `minilm-l6-v2` to
`arctic-embed-xs-q`.** On identical corpora, identical retrieval and an
identical 21-task pin, Arctic is ahead of MiniLM on `vec` and `hybrid` (one
tie, no losses) and on `budgeted` R@5/R@10/nDCG@10, tying budgeted hit@5 and
losing only budgeted MRR — see §3 for the exact split. It is also the only
profile of the three that surfaces gold into the candidate pool for all 21
tasks; it is 4x smaller on disk; and it is at worst comparable on every runtime
axis that was measured.

**Production default: changed to `arctic-embed-xs-q`.** This is a decision the
quality numbers alone do not make — Qwen still leads budgeted R@5 (0.698 vs
0.663), budgeted MRR and budgeted nDCG@10, and wins the paired budgeted tally
2-0. It is a decision the *whole* cost picture makes, and the repository owner
made it explicitly: Qwen requires a separate long-lived llama.cpp server, an
`OXIDE_EMBED_URL`/`OXIDE_EMBED_MODEL` pair, and roughly 9x the indexing wall
clock (~213 min vs ~23 min over this 21-repo pin). A 0.035 budgeted-R@5 margin
— one to two tasks out of twenty-one, inside this evidence's own noise — does
not pay for that.

Concretely, `open_embedder`'s fallback moved from the offline `HashedEmbedder`
to `DEFAULT_NATIVE_PROFILE = "arctic-embed-xs-q"`, and `native-embed` became a
default Cargo feature. Two consequences are stated rather than buried:

- **An unconfigured `oxide index` is no longer offline.** It downloads ~23MB of
  ONNX weights on first use. `OXIDE_EMBED_NATIVE=hashed` restores the previous
  behaviour, and so does `--no-default-features`.
- **The benchmark gate is unaffected.** `src/eval.rs` constructs
  `HashedEmbedder` directly and never calls `open_embedder`, so
  `tests/benchmark_gate.rs` scores the same embedder it always did.

Qwen remains fully supported via `OXIDE_EMBED_URL` and remains the better
choice if budgeted R@5 is worth a server to you.

**MiniLM is not removed.** `minilm-l6-v2` and `minilm-l6-v2-q` remain valid
`OXIDE_EMBED_NATIVE` profiles; they are simply no longer the recommended
lightweight choice.

`docs/embedding-profile-comparison/README.md` is superseded on this point only;
a pointer was added there rather than rewriting its 2026-09-02 numbers, which
remain the correct record of what that session measured.

---

## 8. Known gaps

- **n=21.** The budgeted gaps between all three models are small relative to a
  single task's contribution (1/21 ≈ 0.048 of R@5 for a single-gold task). Read
  the consistent *ordering* across aggregate and paired tallies, not the
  individual deltas.
- **fp32 MiniLM's real-corpus embedding wall-clock was never measured**, in
  Phase 1 or here. Only its int8 twin was. The runtime table says so.
- **Pooling for MiniLM has a single source** (fastembed's table); see §1.
- **Revision pinning is still not enforced** by the fingerprint; see §1.
- **`oxide status` cannot read the five pylint worktrees** (a pre-existing
  non-UTF-8 source file defeats `current_file_hashes`'s `read_to_string`), so
  the probe records `staleness_check: "unavailable: …"` there rather than a
  freshness confirmation. Unchanged from Phase 2; not fixed here, since fixing
  it is a source change to the scanner unrelated to this question.

---

## 9. Reproducing

```bash
cargo build --release --features native-embed
cargo build --release --example corpus_manifest

# The provider environment must stay active for EVERY command in a phase:
# kept_pool_probe.py re-runs `oxide context` itself. Export, do not prefix.
unset OXIDE_EMBED_URL OXIDE_EMBED_MODEL
export OXIDE_EMBED_NATIVE=minilm-l6-v2
OUT=eval-agent/results/minilm_rerun
OXIDE_RANKING_PER_TASK_OUT=$OUT/per_task_minilm.jsonl \
  eval-agent/.venv/bin/python eval-agent/benchmark/ranking_metrics.py
eval-agent/.venv/bin/python scripts/agent_eval/capture_corpus_manifests.py --out $OUT/manifests_minilm
eval-agent/.venv/bin/python scripts/agent_eval/kept_pool_probe.py --out $OUT/kept_minilm.jsonl

# the gate: corpus must be identical before the comparison means anything
diff -rq eval-agent/results/arctic_phase2/manifests_arctic/corpus $OUT/manifests_minilm/corpus
diff -rq eval-agent/results/arctic_phase2/manifests_qwen3/corpus  $OUT/manifests_minilm/corpus

eval-agent/.venv/bin/python scripts/agent_eval/compare_per_task.py \
  eval-agent/results/arctic_phase2/per_task_qwen3.jsonl $OUT/per_task_minilm.jsonl \
  --kept-baseline eval-agent/results/arctic_phase2/kept_qwen3.jsonl \
  --kept-candidate $OUT/kept_minilm.jsonl
eval-agent/.venv/bin/python scripts/agent_eval/compare_per_task.py \
  $OUT/per_task_minilm.jsonl eval-agent/results/arctic_phase2/per_task_arctic.jsonl \
  --kept-baseline $OUT/kept_minilm.jsonl \
  --kept-candidate eval-agent/results/arctic_phase2/kept_arctic.jsonl
```

Committed evidence: `eval-agent/results/minilm_rerun/` (per-task sink, kept-pool
probe, aggregate table, both comparisons, `PROVENANCE.tsv`, `corpus/INDEX.txt`).
The 35MB of per-symbol `corpus/*.tsv` manifests are gitignored and regenerable
by the command above.
