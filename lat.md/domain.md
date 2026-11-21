# Domain concepts

The vocabulary of OXIDE's repository knowledge model and its context results.

## Entities and relations

Repository knowledge is a graph of four entity kinds joined by seven typed relations.

Entities: Repository, Module, File, Symbol ([[src/oxide/models.py]] `Category`); tests are a facet of symbols, not a kind. Relations: `contains`, `contains_logical`, `defines`, `references`, `calls`, `imports`, `tested_by` (`RelationKind`). Every file is evidence with explicit coverage whether or not a language slice understands it ([[src/oxide/core/indexing.py]]).

## Identity and source references

Entity keys are deterministic and snapshot-local, never database row ids (ADR-0010).

A source reference ([[src/oxide/models.py]] `SourceRef`) is repo-relative path, half-open byte range and content digest. Hydration verifies the digest, so stale text is never served as current. Line numbers derive from captured bytes, not the live worktree ([[src/oxide/models_nav.py]]).

## Snapshot and generation

A snapshot is one immutable capture of a worktree; a generation is its sealed, published derived store.

[[src/oxide/capture.py]] reads once, never follows symlinks, never runs Git, hooks or repository code, and reports every skipped in-scope entry.

## Entry points versus context

Retrieval finds entry points; routing finds context.

Lexical hits and structural seeds start TreeRouter and are never final output. An unavailable lexical channel is reported, never scored as zero.

## Candidate, capsule, bundle

Candidates are routed entities; capsules are bounded evidence about one candidate; a bundle is the packed answer.

The heuristic admits candidates by relevance value; greedy-v6 selects groups (item plus header) within budget, halving the value of test-role evidence only when it crowds production evidence out of the budget (never below the admit floor); the `ContextBundle` carries provenance and reports what depends on its entry points (blast radius).

## Derivation identity

The derivation identity names every component that shapes derived data, so a change forces a rebuild.

Store schema, payload and capsule formats are versioned; an incompatible index is rebuilt, never migrated.

## Error contract

Failures are typed with stable codes: `not_indexed`, `capture_conflict`, `store_corrupt`, `resource_exhausted`, `incompatible_index`, `ownership_conflict`.

The CLI prints `error[<code>]: <message>` ([[src/oxide/cli.py]]).
