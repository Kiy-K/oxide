# RepoBench-derived context-growth validation plan

This is an independent validation protocol, not a retrieval result. Do not
combine its rows with the pinned ContextBench aggregate.

## Frozen inputs

Record before each run:

- OXIDE commit, `cargo --version`, and `target/release/oxide --version`.
- Exact RepoBench source URL, dataset revision, license, and the selected
  instance ids with their repository commit hashes in a new, append-only
  manifest under `eval-agent/results/`.
- Embedder endpoint/model identity and the index meta embedder fingerprint.
- The unmodified `src/retrieval.rs`, `src/context.rs`, `src/index.rs`, and
  `src/config.rs` diff against the recorded commit (must be empty).

Exclude tasks whose checkout fails, has no Python/TypeScript sources, or has
no file-level oracle. Report exclusions and their reasons; never replace them
silently.

## Independent comparison

For every retained task and its frozen checkout, index once and compare:

1. `git grep` term-occurrence ranking;
2. symbol-map lexical ranking;
3. OXIDE lexical, vector, hybrid, and budgeted context.

Score file coverage/precision, first useful file rank, and returned tokens.
The RepoBench-derived oracle must be recorded separately from ContextBench
gold spans; a result is only comparable within its own manifest.

## Controlled context growth

Hold query, checkout, embedder, retrieval mode, and candidate pool fixed.
Run `oxide context` at 512, 1024, 2048, and 4096 tokens, in randomized task
order. Record the complete JSON pack, used tokens, items, omitted reasons,
wall time, and oracle coverage. Repeat the 4096-token condition once to
detect nondeterminism.

Keep a larger budget only if coverage increases without a material precision
drop; otherwise retain the smallest budget on the observed Pareto frontier.
This validation may inform a later, explicitly proposed config change; it
must not tune the frozen retrieval stack while it is running.
