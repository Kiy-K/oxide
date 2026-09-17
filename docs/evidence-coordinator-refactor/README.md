# Evidence coordinator refactor: performance and architecture notes

Companion doc to `docs/superpowers/specs/2026-09-15-evidence-coordinator-refactor-design.md`
and `docs/superpowers/plans/2026-09-15-evidence-coordinator-refactor.md`.

## Performance

Before/after comparison of `oxide query "where is retry logic" --path fixtures/py_repo`
across four conditions (base, `--git`, `--lsp`, `--git --lsp`), using the
deterministic offline `hashed` embedder throughout.

**Before** (commit `ae79d7b`, after audit fixes, before the async evidence
coordinator): raw numbers in `before.json`, `before_*.json`, `before_*_time.txt`.

| Condition | Wall time | RSS |
|---|---:|---:|
| base | ~0.00s | 15.4 MB |
| `--git` | ~0.01s | 16.0 MB |
| `--lsp` | ~0.11s | 178.2 MB |
| `--git --lsp` | ~0.12s | 178.4 MB |

At this fixture's scale, git's own cost (~10ms) is small next to LSP's
(~110ms, dominated by spawning and initializing a real `ty` server), so this
single-run "before" capture cannot yet distinguish a serialized-sum
architecture from a concurrent-max one by wall time alone — see
`before.json`'s caveats. The "after" section (added once the coordinator
lands) is where the concurrency claim actually gets tested.

**After**: added in a later task, once the `EvidenceCoordinator` refactor is
complete — see the bottom of this file.

## Duplicate evidence across sources

When the same symbol is surfaced by more than one evidence source in the
same call, `context.rs`'s `order_note` sums their scores rather than taking
the max — a property of the merge step itself, not specific to any one
source. Applied to git-vs-structural before this refactor and applies
identically to git/LSP/structural/blast-radius after it; not a regression
introduced here, documented because no single doc stated it generally
before.
