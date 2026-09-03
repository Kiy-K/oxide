# CPU-first embedding survey (in progress)

Branch/worktree: `embed-cpu-survey`, forked from `main`@`94a98ac` via
`git worktree add -b embed-cpu-survey ../oxide-embed-survey HEAD`. The main
worktree's uncommitted corroboration-experiment changes
(`src/config.rs`/`src/retrieval.rs`/`scripts/agent_eval/*`) were never
touched by this worktree — verified at creation time and rechecked before
every commit below. No retrieval/graph/allocator/default-provider-selection
semantics have been changed; see "Code changes" for exactly what was.

**Status: Stage A setup complete, correctness-verified for all six candidate
configurations, timed measurements not yet taken — the machine has had a
background `oxide index` job running continuously (another session's
ContextBench corroboration sweep) since before this worktree was created.
Per this task's own instruction, timed runs only happen on an idle machine.**

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

## Blocked: Stage A timed measurements

Per this task's instruction to time only on an idle machine: `pgrep -fa
"target/release/oxide index"` has shown the same background `oxide index .`
process (pid 72492, another session's ContextBench corroboration sweep,
apparently indexing a large repo — the process predates this worktree and
was still running at last check, load average 1.5–3.0) continuously since
before this survey started. The Nomic server was stopped rather than left
idling through this wait, since a cold-start timing run needs a fresh server
start anyway.

**Not yet measured, pending an idle window:** cold init/load, `embed_query`
p50/p95, incremental (1/10/50/100-symbol) latency, batch-64/500 throughput,
peak RSS, and per-candidate `index.db` size on a fixed fixture repo (the
disk/vector-size axis specifically named in the task — 768d vs 256d Nomic
should show roughly a 3x difference in stored embedding bytes, which is the
actual point of the 256d variant).

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
