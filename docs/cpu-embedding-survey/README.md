# CPU-first embedding survey (in progress)

Branch/worktree: `embed-cpu-survey`, forked from `main`@`94a98ac` via
`git worktree add -b embed-cpu-survey ../oxide-embed-survey HEAD`. The main
worktree's uncommitted corroboration-experiment changes
(`src/config.rs`/`src/retrieval.rs`/`scripts/agent_eval/*`) were never
touched by this worktree — verified at creation time and rechecked before
every commit below. No retrieval/graph/allocator/default-provider-selection
semantics have been changed; see "Code changes" for exactly what was.

**Status: Stage A complete — all six candidate configurations correctness-
verified and timed on an idle machine (a peer session's background indexing
job was blocking this and was stopped at this task's request; see "Stage A
timed measurements" for how idleness was confirmed). No recommendation is
made in this report; see "Not yet done" for what Stage B still needs.**

## Candidates

| Candidate | Runtime | Native dim | Source | Status |
|---|---|---|---|---|
| `qwen3-Q8_0` | HTTP (llama.cpp) | 1024 | reference — already the default | numbers reused from `docs/embedding-profile-comparison/README.md`, not rerun |
| `minilm-l6-v2` | native (fastembed/ONNX) | 384 | reference — already validated, weights cached (`Qdrant/all-MiniLM-L6-v2-onnx`, 87MB) | correctness ✅, timing pending |
| `nomic-embed-text-v2-moe` (768d) | HTTP (llama.cpp) | 768 | new — official `nomic-ai/nomic-embed-text-v2-moe-GGUF:Q8_0`, 512MiB | correctness ✅, timing pending |
| `nomic-embed-text-v2-moe` (256d, MRL-truncated) | HTTP (llama.cpp) | 256 | new — same weights, client-side truncate+renormalize | correctness ✅, timing pending |
| `jina-code-v2` | native (fastembed/ONNX) | 768 | new — `jinaai/jina-embeddings-v2-base-code`, weights now cached (full repo snapshot 1.7GB; fastembed's own footprint is smaller, see disk-size TODO below) | correctness ✅, timing pending |
| `arctic-embed-xs` | native (fastembed/ONNX) | 384 | new — `snowflake/snowflake-arctic-embed-xs`, weights now cached (364MB) | correctness ✅, timing pending |
| `bge-small-en-v1.5` | native (fastembed/ONNX) | 384 | already validated in a prior session; weights already cached (`Xenova/bge-small-en-v1.5`, 128MB) | correctness ✅, timing pending — include only if it's not dominated by arctic-embed-xs on every axis |

All native profiles were already wired in `native_model_spec`
(`src/embeddings.rs`) from an earlier session ("Phase 3.3b Pareto survey");
this survey needed zero new code for them, only weights + measurement.
`qwen3-Q8_0` is the existing shipped HTTP default; also zero new code.
`nomic-embed-text-v2-moe` was the only candidate with no existing code path —
see "Code changes" below.

**Downloads this session** (explicit, per the task's own model list — that's
the consent, not re-asked): `nomic-ai/nomic-embed-text-v2-moe-GGUF:Q8_0`
(512MiB), `jinaai/jina-embeddings-v2-base-code` (1.7GB full snapshot),
`snowflake/snowflake-arctic-embed-xs` (364MB). `minilm-l6-v2` and
`bge-small-en-v1.5` weights were already cached locally from a prior session.

## Code changes

`src/embeddings.rs`: added `HttpPromptProtocol` (`Qwen3Instruct` | `Prefixed`)
and an optional Matryoshka `truncate_dim` to `HttpEmbedder`, plus a new
`HttpEmbedder::new_with_protocol` constructor. **`open_embedder` and every
production/CLI path still call `HttpEmbedder::new`**, which hardcodes
`Qwen3Instruct` + `truncate_dim: None` and produces byte-identical
`name()`/`embed_query()` output to before — pinned by
`http_embedder_name_discriminates_protocol_and_truncation` and the
pre-existing `qwen3_query_text_matches_legacy_instructed_query_format` test.
`open_embedder`, `configured_provider_name`, and the CLI were not touched at
all. This is a survey-only extension point today; whether to ever wire a
second HTTP protocol into the shipped provider-selection path is a
recommendation question, explicitly out of scope until Stage B/Codex review.

A real latent bug was found and fixed along the way, not introduced by this
work: `HttpEmbedder`'s old name format (`format!("http:{model}@{endpoint}")`)
would produce identical names for two different models sharing an
endpoint+model-string pair (e.g. both left with `$OXIDE_EMBED_MODEL` unset),
which `update_index` would then treat as index-compatible and silently reuse
one model's vectors for the other. `new_with_protocol`'s non-Qwen protocols
always fold their `label` into `name()`, so this can't happen for new
callers; the Qwen3 path's format is unchanged since production has only ever
run one HTTP model per process. Flagging this for whoever reviews the
`open_embedder` production path — this survey does not depend on fixing it
there, and didn't.

New examples (none wired into any binary/CLI/default build target):
- `examples/nomic_correctness_check.rs` / `examples/native_correctness_check.rs`
  — paraphrase-vs-unrelated + L2-norm sanity gate, one embed call per check,
  safe to run on a contended machine (only pass/fail is read).
- `examples/embedding_profile_probe_http_nomic.rs` — `HttpEmbedder::new_with_protocol`
  twin of the existing (untouched) `embedding_profile_probe_http`, same
  methodology, for the Nomic HTTP path.

New script: `scripts/cpu_embedding_survey/nomic_server.sh` — mirrors
`scripts/embedder.sh`'s conventions on a separate port (8192) so it runs
alongside the production Qwen3 server without colliding. Does not modify
`scripts/embedder.sh`.

## Correctness verification (this session, all six configurations)

Run before trusting any timing, per this task's own instruction to gate
Nomic on correctness first (llama.cpp's MoE-embedding path has had upstream
bugs against this exact model — a tokenizer mismatch and a `GGML_ASSERT`
crash, both filed on `ggml-org/llama.cpp`). Local `llama` build: `b10217-ddd4ec142`,
well past the MoE-support merge. Check: reported dimension matches
expectation, every vector is L2-normalized (‖v‖ within 0.05 of 1.0), and a
hand-written paraphrase pair scores above an unrelated pair.

| Candidate | dim | sim(paraphrase) | sim(unrelated) | Verdict |
|---|---|---|---|---|
| nomic-embed-text-v2-moe (full) | 768 | 0.7936 | 0.3702 | PASS |
| nomic-embed-text-v2-moe (truncated) | 256 | 0.8064 | 0.4068 | PASS |
| minilm-l6-v2 | 384 | 0.7983 | −0.0743 | PASS |
| jina-code-v2 | 768 | 0.9310 | 0.0345 | PASS |
| arctic-embed-xs | 384 | 0.9128 | 0.5630 | PASS |
| bge-small-en-v1.5 | 384 | 0.8873 | 0.4559 | PASS |

No `--pooling` override was needed for Nomic (unlike Qwen3, which needs
`--pooling last` explicitly) — the official GGUF's own metadata already
encodes Nomic's documented mean pooling; that's what these correctness
numbers actually confirm, not an assumption.

## Stage A timed measurements

The other session's background `oxide index .` job (the one blocking this
section when this doc was first written) was stopped at this task's request
(peer session `oxide-f2`, confirmed by its own user). Machine verified idle
before every timed run below (`uptime` load average 1.2–1.9 on 16 cores,
~10% utilization, plus checking for no live `oxide index`/`contextbench_run`/
`tierb_agent_run` processes). One false alarm along the way worth recording:
the production Qwen3 server's `ps` `%CPU` column showed 33% and looked like
live load, but that column is a lifetime average since process start, not
current usage — its request log had gone quiet, confirming genuine idle.

All timings below are same-session, same-machine-state, same methodology
(`examples/embedding_profile_probe*`, unmodified for every profile except
Nomic, which uses the new `_http_nomic` twin) — including a fresh Qwen3
re-run, so the whole table is apples-to-apples rather than mixing in the
prior survey's cross-session number. It closely reproduces that prior run
(100-symbol incremental: 105.49ms/item here vs 105.25ms/item there), which
cross-validates both this session's methodology and the prior one's.

| Profile | Runtime | Dim | Cold init | `embed_query` p50 | `embed_query` p95 | 100-sym incremental/item | Throughput (500, items/s) | Peak RSS | Disk (weights) |
|---|---|---|---|---|---|---|---|---|---|
| qwen3-Q8_0 (reference) | HTTP | 1024 | 879ms (client only; server already warm) | 39.39ms | 41.91ms | 105.49ms | 9.2 | 141MB (server, current) | 0 (server-managed) |
| minilm-l6-v2 (reference) | native | 384 | 166.8ms | 1.95ms | 4.87ms | 3.03ms | 310.5 | 629.4MB | 87MB |
| arctic-embed-xs | native | 384 | 177.8ms | 2.48ms | 2.97ms | 3.23ms | 298.3 | 582.2MB | 87MB (fp32 `model.onnx`) |
| bge-small-en-v1.5 | native | 384 | 293.0ms | 4.47ms | 5.32ms | 6.16ms | 156.2 | 640.2MB | 128MB |
| jina-code-v2 | native | 768 | 1172.1ms | 11.71ms | 14.01ms | 25.40ms | 35.1 | 1889.0MB | 615MB (fp32 `model.onnx`) |
| nomic-v2-moe (768d) | HTTP | 768 | 67.8ms (client only) / 4.05s (server cold start) | 84.42ms | 101.25ms | 54.48ms | 19.7 | 409.9MB (server) | 489MB (Q8_0 GGUF) |
| nomic-v2-moe (256d, truncated) | HTTP | 256 | 61.0ms (client only) | 77.24ms | 87.07ms | 50.99ms | 20.1 | 409.9MB (same server) | 489MB (shared with 768d) |

Notes on this table:
- Qwen3's cold-init row is client-only (server pre-warm) — its own server
  cold-start (`scripts/embedder.sh start`) was **not** re-measured this
  session (reusing the prior survey's 6.15s), since the server was already
  up and stopping it just to re-time a well-established number wasn't worth
  the disruption. Nomic's server cold start (4.05s, `nomic_server.sh start`)
  **was** freshly measured, same process-boundary methodology.
- 768d vs 256d Nomic client-side latency is within noise of each other
  (expected: truncation happens client-side after the server does the same
  768d forward pass regardless of requested output size — the compute cost
  is identical, only the wire payload and stored-vector size shrink).
- `jina-code-v2`'s fp32 `model.onnx` (768d, larger transformer) is
  meaningfully heavier on every axis than every other native candidate
  including the reference MiniLM — cold init 7x, peak RSS 3x, throughput
  ~9x slower.
- Disk figures for `jina-code-v2`/`arctic-embed-xs` are the fp32
  `model.onnx` fastembed actually loads by default, not the full HF repo
  snapshot (which includes unused fp16/int8/quantized/bnb4 variants and, for
  Jina, an unrelated raw PyTorch checkpoint — combined snapshot sizes were
  1.7GB and 364MB respectively, both larger than what's actually resident).

### Disk/vector size: `index.db` on a fixed fixture repo

Measured via a new `examples/index_size_probe.rs` (calls `update_index`
directly, like `eval.rs` already does — bypasses the CLI since
`open_embedder` has no path for Nomic's protocol) against `fixtures/py_repo`
(8 files, 54 symbols).

| Profile | Dim | `index.db` bytes | SQLite pages (4096B) |
|---|---|---|---|
| hashed-bow-256 (baseline) | 256 | 143,360 | 35 |
| nomic-v2-moe (256d truncated) | 256 | 143,360 | 35 |
| minilm-l6-v2 / arctic-embed-xs / bge-small-en-v1.5 | 384 | 176,128 | 43 |
| jina-code-v2 | 768 | 278,528 | 68 |
| nomic-v2-moe (768d) | 768 | 278,528 | 68 |
| qwen3-Q8_0 | 1024 | 311,296 | 76 |

**Caveat, stated plainly rather than papered over:** every value above is an
exact multiple of SQLite's 4096-byte page size, and this fixture is tiny (54
symbols) — at this scale, `index.db` size is dominated by page-allocation
granularity and fixed schema overhead (symbols/references/structural-relations
tables), not raw embedding bytes. The task's expectation that "768d vs 256d
is 3x" reflects the *embedding bytes themselves* (768×4 vs 256×4 = exactly
3x, trivially true), but that ratio does **not** show cleanly in total
`index.db` size until a repo is large enough for embedding storage to
dominate the fixed overhead — 54 symbols isn't that scale. A repo with
thousands of symbols (e.g. the `pylint`-scale fixtures used in the
ContextBench corroboration work) would be needed for a fair reading of the
dimension's effect on total on-disk index size; not run here to avoid the
long reindex time that would cost, and because the per-vector byte math
(dim × 4 bytes × symbol count) already answers the question the task is
actually asking without needing to reindex a large repo just to watch SQLite
round up to the next page.

## Not yet done

- Stage A quality screen. `oxide eval --config fixtures/benchmark.json` is
  **not usable for this** — it's the frozen benchmark gate, hardcoded to
  `HashedEmbedder::default()` (`src/eval.rs`), not model-configurable, and
  must not be changed per `CLAUDE.md`'s re-baselining rule. There is no
  existing harness that scores arbitrary embedders' retrieval quality outside
  the full ContextBench pipeline in `eval-agent/` (Python, gitignored,
  main-worktree-only). Stage A quality screening will need either (a) a
  small number of manual `oxide search`/`oxide context` sanity queries per
  candidate against a fixture repo (qualitative, not a metric), or (b)
  waiting for Stage B's corrected ContextBench harness. Reused, not
  re-derived: `qwen3-Q8_0` and `minilm-l6-v2` already have full 21-task
  quality numbers in `docs/embedding-profile-comparison/README.md` (hybrid/
  budgeted R@5, hit@5) — those are the two "reference" candidates and don't
  need rerunning.
- Stage B (full ContextBench quality run on survivors) is explicitly blocked
  on a git operation, not a file copy: the corrected harness lives only in
  the main worktree's **uncommitted** `scripts/agent_eval/contextbench_run.py`.
  When it lands (commits, not working-tree state), pull it into this
  worktree via cherry-pick/merge of that commit specifically — copying
  working-tree files would drag `src/config.rs`/`src/retrieval.rs` along too
  and violate the frozen-semantics constraint.
- This worktree has no `eval-agent/` (`.venv`, `third_party/ContextBench`,
  `results/` are gitignored, main-worktree-only) — Stage B will need to
  either re-clone/re-provision those or run the harness from the main
  worktree against a binary built in this worktree. Not decided yet.
- Final Pareto table, disk/vector-size table, and recommendation. Explicitly
  stopping before any recommendation per the task — this report is for Codex
  review of implementation, provenance, benchmark fairness, and
  interpretation before that.
