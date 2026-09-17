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

**After** (post-coordinator, commit `5e1542c`): raw numbers in
`after_*.json`, `after_*_time.txt`.

| Condition | Wall time | RSS |
|---|---:|---:|
| base | ~0.01s | 15.6 MB |
| `--git` | ~0.02s | 15.8 MB |
| `--lsp` | ~0.13s | 63.1 MB |
| `--git --lsp` | ~0.12s | 61.0 MB |

Output parity: `items`/`omitted` for `base`/`--git` are structurally
identical to the pinned `evidence_coordinator_compat.rs` fixtures (strict
byte-for-byte comparison, enforced by CI). `--lsp`/`--git --lsp` are not
byte-comparable run to run for a reason unrelated to this refactor — see
below.

**Was the concurrency claim measured, and does the wall-clock table above
support it?** No — at this fixture's scale git's own cost (~10-20ms) and
LSP's (~110-130ms) are close enough together, and single-run wall-clock
noisy enough, that `--git --lsp`'s ~0.12s sitting closer to `max(0.02,
0.13)=0.13` than to `sum=0.15` is *consistent* with the concurrent
architecture but is not, by itself, strong evidence — a 10-30ms
difference on a shared laptop is within normal single-run variance. The
RSS drop for `--lsp`/`--git --lsp` between before (178 MB) and after
(61-63 MB) looks dramatic but should **not** be read as a refactor
effect either: real `ty` server sessions vary in memory footprint run to
run by a similar magnitude on their own (see the nondeterminism finding
below) — attributing it to the coordinator would be exactly the kind of
unsupported claim this report is instructed not to make.

What *does* directly and reliably support the concurrency claim is a
controlled measurement using the coordinator's own artificial-delay test
hook (`OXIDE_EVIDENCE_ARTIFICIAL_DELAY_MS`), which removes both git's and
ty's own timing noise from the picture: injecting a fixed 200ms delay into
*both* `git_io` and `lsp_io` for the same `--git --lsp` query produced an
elapsed wall time of **~230ms** — close to `max(200, 200) + overhead`,
nowhere near `sum(200, 200) + overhead = ~430ms`. This is the number that
actually confirms `git_io` and `lsp_io` run concurrently rather than
serially; see `tests/evidence_coordinator_determinism.rs` for the same
mechanism used as a correctness gate, not just a performance probe.

All of the above is a single-run capture on a shared, non-idle
development laptop, not a 5-repeat median — treat the specific millisecond
figures as illustrative, not precise benchmarks.

### A regression found and fixed during this capture

The first attempt at this after-capture surfaced a real bug: the CLI's
`--lsp` path (`service.rs::context()`, `use_process_cache: false`) was
silently producing **zero** LSP evidence — `EvidenceCoordinator::collect`
only ran `lsp_io` when handed an already-spawned `LspClient`, but the CLI
never provides one (`lsp_client: None`, matching `build_context`'s own
documented spawn-per-call contract). The pre-refactor code's inline LSP
block had a `None => { spawn a client here }` branch that never made it
into `lsp_io` during the port. Fixed in commit `9ce7a40`: `lsp_io` now
spawns its own client when handed `None`. The `before_lsp*`/`before_git_lsp*`
captures above are the true pre-refactor baseline; an earlier, since-discarded
after-capture (made before this fix) showed a suspiciously large RSS
drop and zero `lsp-*` reason tags — that was this bug, not a genuine
improvement, and would have been reported as one had it not been checked
against the true baseline's reason tags.

### `ty`'s own response nondeterminism (pre-existing, not introduced here)

Restoring real LSP evidence surfaced a second, independent finding: five
consecutive `oxide query --lsp` runs against an unmodified fixture, same
binary, no code changes in between, produced five different
`lsp-reference`/score combinations for two symbols (confirmed with `diff`),
while the *set* of item ids surfaced stayed identical across all five. This
is believed to be pre-existing behavior of `enrich_seeds`/`ty` itself
(the enrichment code and its caps, `LSP_MAX_SEEDS`/`LSP_PER_SEED_ITEMS`,
are an unchanged, verbatim port — see `coordinator.rs`'s
`structural_evidence`/`lsp_io` vs. the pre-refactor inline block), not
something the coordinator's concurrency changes could introduce. It is
**not A/B-confirmed against the pre-refactor binary at `ae79d7b`** —
that binary is no longer built, and re-verifying this specific claim
would mean checking out and building that commit separately, which
wasn't done. `tests/evidence_coordinator_compat.rs` and
`tests/evidence_coordinator_determinism.rs` were both adjusted to compare
item-id sets rather than byte-identical JSON for any LSP-touched
condition, documented in both files' module docs.

## Hardening pass (post-freeze-review) performance recapture

The `after_*` captures above predate hardening-pass items #1-#4 (commits
`e137846`..`d1bbe00`), which changed real LSP wire behavior — item #1
specifically adds more `ensure_open` calls per query (resyncing the whole
open-document set, plus opening every `scope_files` entry, not just the
current seed's own file). Reusing the old captures would have been
stale, not evidence; re-ran the same matrix at the post-hardening commit
instead (`after_*_prehardening.{json,txt}` preserve the pre-hardening
numbers for comparison; `after_*.{json,txt}` are the current ones).

| Condition | Pre-hardening wall time | Post-hardening wall time | Pre-hardening RSS | Post-hardening RSS |
|---|---:|---:|---:|---:|
| `--lsp` | ~0.13s | ~0.08s | 64.6 MB | 50.1 MB |
| `--git --lsp` | ~0.12s | ~0.09s | 62.5 MB | 52.7 MB |

Both wall time and RSS moved *down* slightly, not up as the extra
`ensure_open` traffic from item #1 would predict on its own. Reported as
measured, not adjusted to match the prediction: this fixture's `--lsp`
numbers have already been shown (see `ty`'s own response nondeterminism,
below) to vary by a similar magnitude between two runs of the *identical*
binary with *no* code change in between, so a same-direction, similar-
magnitude move here is consistent with ordinary single-run noise on a
shared, non-idle machine, not with a confirmed net effect either way.
`--lsp`'s wire-traffic increase from item #1 is real (more `didOpen`/
`didChange` calls per query against this fixture's small file count) but
too small relative to this fixture's own run-to-run noise floor to show
up cleanly in a single-run wall-clock/RSS capture.

The controlled delay-hook concurrency probe was re-run against the
post-hardening binary: injecting 200ms into both `git_io` and `lsp_io`
for the same `--git --lsp` query produced **~300ms** elapsed, against an
~88ms undelayed baseline on this run — `max(200, 200) + baseline
overhead (~88ms) ≈ 288ms`, matching closely; `sum(200, 200) + baseline
(~88ms) = 488ms` does not. The concurrency finding holds after the
hardening pass, consistent with the pre-hardening ~230ms measurement
(commit `73ffae8`) under the same method.

Not a claim this report makes: that "concurrent" is free. Hardening item
#2 holds a per-root `Mutex` guard across `build_context_with`'s full
span, including its retrieval-search phase, for the LSP-cache-enabled
MCP path — the six-way concurrency regression test
(`lsp_process_cache_concurrency.rs`) measured 6 serialized same-root
calls completing in ~24ms total (a ~20ms single-call baseline on that
run), which is fast because each individual call is fast on this
fixture, not because serialization has no cost. Contention cost scales
with query cost, not with the number of waiting callers; a slower query
under this same lock would serialize its waiters for that query's full
duration, including retrieval search — see item #2's commit message for
why that trade-off wasn't narrowed further in this pass.

Retracted claim (Codex review, final hardening-pass round): an earlier
version of this paragraph read the same 6-concurrent-caller test as
evidence that `max_blocking_threads(4)` doesn't saturate. That's wrong —
the held per-root guard this same paragraph just described *serializes*
those 6 calls onto one root's session, so only one of them is ever
actually inside `spawn_blocking` work at a time; the test cannot have
exercised more than 1 concurrent blocking-pool thread, let alone 4.
Whether the blocking pool saturates under genuinely concurrent
*different-root* `--lsp` load (which wouldn't serialize on this guard)
is untested by anything in this repository and is not claimed here
either way.

## Duplicate evidence across sources

When the same symbol is surfaced by more than one evidence source in the
same call, `context.rs`'s `order_note` sums their scores rather than taking
the max — a property of the merge step itself, not specific to any one
source. Applied to git-vs-structural before this refactor and applies
identically to git/LSP/structural/blast-radius after it; not a regression
introduced here, documented because no single doc stated it generally
before.
