# Design decisions

Standing rules and their reasons; the owners are [[docs/decisions.md]] and `docs/adr/`.

## One vectorless Python package

ADR-0011 replaced the Rust/Bun implementation with one Python process and no embeddings, vector indexes or model runners.

Native libraries (tree-sitter, LadybugDB, NetworkX) do recursive and latency-sensitive work; Python holds bounded policy. The old stack is recoverable at `f79808a` and is evidence, not a template.

## Structure before similarity

Typed relations, not text similarity, decide what context to include.

Graph selection must use a named standard algorithm over stored relations with a stated objective. Personalized PageRank and community detection were rejected after measurement.

## Models judge, deterministic code decides

Judges are optional external services behind `DecisionProvider`; the offline path is fully deterministic.

JEV ordering validated in studies but stays off (ADR-0009): upstream FTS pin and provider-terms issues.

## Never execute repository code

Capture only lists, stats and reads files; it never runs Git, hooks, build tools or repo scripts ([[src/oxide/capture.py]]).

## Measured claims only

Quality or performance claims need before/after numbers with the pass rule fixed first, recorded as one row of research verdicts.

Frozen baselines are never regenerated to make a failure pass. Notable verdicts: path hints promoted; split lexical and automatic freshness promoted; precomputed blast radius passed (ADR-0014); agent-swe showed no demonstrated improvement; structural search stays API-only.

## Size and ownership limits

Source files stay at or below 500 lines; only `oxide.storage` touches LadybugDB; clients of `oxide.api` never reimplement intelligence.
