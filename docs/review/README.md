# OXIDE review policy (v2)

For a reviewer (Codex, Claude, or human) looking at a diff against this repo.
Generic Python/style feedback is not the point — Ruff and mypy `--strict` already
gate that (`docs/agents/workflow.md`). This policy exists because OXIDE v2 has
invariants a competent reviewer cannot reliably infer from the diff alone:
one-owner storage, published generations, deterministic core policy over a
pinned read view, a vectorless retrieval/routing/packing pipeline, and a
stable typed public API. Read the relevant file below before reviewing a
change in that area.

OXIDE v2 is one Python package (`oxide`, ADR-0011): `oxide.api`/`oxide.models`
are the public boundary; `oxide.core` holds pure repository intelligence and
policy (no database or filesystem access); `oxide.capture` and
`oxide.storage` own worktree capture and LadybugDB; the CLI and `oxide mcp`
are clients of the public API only. Architecture authority is
`docs/spec/SPEC.md` and `docs/BOOTSTRAP.md`. When they disagree with this
file, they win.

## Rule files

| File | Covers |
|---|---|
| `api-surface.md` | Public API stability, integration boundaries, MCP/CLI as clients only, JSON/output contracts |
| `storage-and-generations.md` | LadybugDB ownership, locks, published generations, capture identity, damage classification |
| `core-and-retrieval.md` | Pure core, read view, retrieval/routing/packing, budgets, deterministic ordering, FTS availability |
| `capture-and-language.md` | Worktree capture, symlinks/special files, language extraction, coverage diagnostics |
| `evidence-and-benchmarks.md` | Benchmark provenance, frozen baselines, incomplete runs, what a passing test proves |

## Load-bearing invariants

The v2 invariants live in `docs/spec/SPEC.md` ("Core invariants", "Engine
resources and ownership", "Failure contract") and `docs/BOOTSTRAP.md`. Read those
directly rather than relying on this policy's summary; duplicating them risks
drift. Treat every entry there as **BLOCKER** severity under this policy.

## Severity

- **BLOCKER** — silently wrong behavior: data/index corruption, a violated
  load-bearing invariant, a regression in a benchmark-gated property, or
  unconsented network/model-download activity.
- **MAJOR** — a real functional or contract regression that isn't silently
  catastrophic: unbounded traversal, an integration reimplementing repository
  intelligence, a benchmark claim with no reproducible provenance.
- **MINOR** — a real deviation from stated design intent with limited blast
  radius.
- **NIT** — cosmetic drift that doesn't change behavior.

## Reporting a finding

- Point to concrete code: file, and line or function name.
- State the realistic failure path — what input or sequence of operations
  actually reaches the bug.
- Name the violated rule (its stable ID) or invariant.
- Gather the "evidence required" listed under the rule before reporting it.
  A rule you can't find the required evidence for is a question to ask, not
  a finding to file.
- A possibility that isn't tied to a concrete reachable path is speculation.
  Ask it as a question in the review; don't file it as a bug.
