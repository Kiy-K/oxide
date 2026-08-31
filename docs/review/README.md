# OXIDE review policy

For a reviewer (Codex, Claude, or human) looking at a diff against this repo.
Generic Rust/style feedback is not the point — clippy/rustfmt already gate
that (`AGENTS.md`). This policy exists because OXIDE has invariants a
competent reviewer cannot reliably infer from the diff alone: frozen
benchmark-affecting constants, an index-compatibility contract keyed off a
struct most providers don't override, a deliberately bounded structural-
search cost model, and a benchmark-gated ranking pipeline. Read the relevant
file below before reviewing a change in that area.

## Rule files

| File | Covers |
|---|---|
| `retrieval-and-config.md` | Fusion/context weights, `RetrievalMode` default and scope, lexical/semantic concurrency |
| `embeddings-and-index.md` | Fingerprint compatibility, provider selection (no silent fallback/download), degradation on provider failure |
| `structural-and-language.md` | Bounded structural-relation lookups, `structural_relations.rs` attribution tie-breaks, declarative vs. bespoke language extractors |
| `evidence-and-benchmarks.md` | Benchmark provenance, incomplete/cancelled runs, what a passing test actually proves |
| `api-surface.md` | MCP/CLI surface growth, JSON output contract stability |

## Load-bearing invariants (AGENTS.md)

`AGENTS.md`'s "Load-bearing invariants" section (symbol id composition, the
`i64` bit-casts, first-wins definition dedup, expansion-ordering, the
`std::thread::scope` rationale, `RetrievalMode`'s gating scope, lexical body
weighting, the embedding cache-invalidation key, the cross-file staleness
gap, read-only SQLite semantics, atomic meta writes) is not restated here —
read it directly, since duplicating it risks drifting out of sync as it's
edited. Treat every entry in that list as **BLOCKER** severity under this
policy: these are exactly the invariants that "look fine until a benchmark
or a targeted regression test fails."

## Severity

- **BLOCKER** — silently wrong behavior: data/index corruption, a violated
  load-bearing invariant, a regression in a benchmark-gated property, or
  unconsented network/model-download activity.
- **MAJOR** — a real functional or contract regression that isn't silently
  catastrophic: unbounded structural search, unscoped API/MCP surface
  growth, a benchmark claim with no reproducible provenance.
- **MINOR** — a real deviation from stated design intent with limited blast
  radius (e.g. a degradation path that used to be explicit becoming
  implicit, without changing observable behavior yet).
- **NIT** — cosmetic drift that doesn't change behavior (e.g. a rationale
  comment removed without the value it explains changing).

## Reporting a finding

- Point to concrete code: file, and line or function name.
- State the realistic failure path — what input or sequence of operations
  actually reaches the bug, not just that the code "could" be wrong.
- Name the violated rule (its stable ID) or invariant.
- Gather the "evidence required" listed under the rule before reporting it.
  A rule you can't find the required evidence for is a question to ask
  ("does X change Y's fingerprint?"), not a finding to file.
- A possibility that isn't tied to a concrete reachable path is speculation.
  Ask it as a question in the review; don't file it as a bug.
