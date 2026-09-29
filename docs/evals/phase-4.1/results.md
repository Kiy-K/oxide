# Phase 4.1 results

## Tier 1 — offline trigger accuracy (n=34, zero agent runs, fully reproducible)

`python3 docs/evals/phase-4.1/raw/tier1_offline_eval.py` (deterministic; rerun
twice during this phase, byte-identical both times once a harness bug — see
below — was fixed):

| bucket | n | fired | correct |
|---|---|---|---|
| A (should fire) | 14 | 13 | 13 |
| B (optional, treated as should-fire) | 6 | 3 | 3 |
| C (must not fire) | 14 | 0 | 14 |

**precision = 1.00, recall = 0.80, false-positive rate = 0.00**
(tp=16, fp=0, tn=14, fn=4)

- **Zero false positives across 14 Bucket-C prompts**, including adversarial
  ones designed to trip the positive-match regex list while still being an
  exact-file tiny edit (e.g. `C13`: "Find the line in `src/main.rs` that
  prints the banner and change the text..." — contains "find" but the
  negative gate correctly wins).
- **Misses are real regex-coverage gaps, not implementation bugs**: `A6`
  ("Where is the retry backoff... implemented?") missed because the
  `where...implemented` pattern requires the two words within 40 characters
  of each other, and this prompt's clause is 52 characters; `B2`/`B3`/`B4`
  missed because they say "find that test" / "find every place" / "find all
  the files" — phrasings one step outside the curated pattern list ("find
  where", "find the", "find every/all ... files"). All four are legitimate,
  fixable heuristic-coverage gaps, listed here rather than quietly patched
  away, since a hand-tuned regex list tuned against its own eval set proves
  nothing.
- **Unindexed-repo control: 0/34 fires.** Every prompt, including the
  strongest Bucket-A ones, produced silence when `.oxide/index.db` was
  absent — the index-presence gate works as designed.
- **Hook latency (subprocess spawn + heuristic, n=34): mean 37-41ms, p50
  ~38-42ms, max ~45-71ms** across two runs — Python interpreter startup
  dominates; the regex/filesystem-check logic itself is sub-millisecond.
  Far under Claude Code's 30s `UserPromptSubmit` timeout.
- **Injected tokens: ~70 on fire (282 chars, chars/4 estimate — matches the
  E1 snippet's already-documented size in `docs/agent-context-overhead.md`
  and `protocol.md §0`), exactly 0 on no-fire** (confirmed empty stdout).

A harness bug was found and fixed during this measurement, not swept under
the rug: the first version of `tier1_offline_eval.py` used a fixed
`f"eval-{row['id']}"` session id, which collided with state left behind by
this session's own manual testing of the hook (a real feature — the
one-suggestion-per-session cap — being tripped by the *harness*, not the
hook). Fixed by making every run use a fresh `uuid`-suffixed session id, so
re-running the script twice in a row now produces byte-identical results
(verified).

## Tier 2 — real agent, hook vs. skill-only (n=6 attempted, small and directional, real cost incurred)

Run via `docs/evals/phase-4.1/raw/tier2_agent_run.py`, the actual `claude`
CLI headlessly, per-condition-isolated fixture copies (`protocol.md §4`).
**Scope (3 tasks x 2 conditions x 1 rep) and real-money cost were confirmed
with the user before running**, given no `--bare`-isolated `ANTHROPIC_API_KEY`
was available, so every run billed the session's own subscription.

| task | condition | result | wall | tool calls | used_oxide | tokens | cost |
|---|---|---|---|---|---|---|---|
| T2-A1 (notifiers, py) | hook | success | 25.2s | 7 | **False** | 370,684 | $0.34 |
| T2-A1 (notifiers, py) | skill | 2x timeout (180s), 3rd manual attempt success | 41.7s (3rd try) | 8 | **True** | 431,879 (3rd try) | $0.26 (3rd try) |
| T2-A2 (JWT decode, ts) | hook | success | 55.6s | 7 | **True** | 436,286 | $0.29 |
| T2-A2 (JWT decode, ts) | skill | timeout (180s), not retried further | — | — | — | — | — |
| T2-C1 (rename, py) | hook | success, correct (no oxide call) | 29.3s | 4 | False (correct) | 258,892 | $0.21 |
| T2-C1 (rename, py) | skill | success, correct (no oxide call), but stalled on a blocked `Edit` permission before this phase's `allowedTools` fix | 178.0s | 5 | False (correct) | 311,520 | $0.25 |

**Appropriate usage / false positives**: both conditions correctly avoided
OXIDE on the Bucket-C rename task (0 false positives, n=2). On Bucket-A, the
hook condition activated OXIDE in 1 of 2 completed runs (it suggested;
T2-A1's agent chose not to follow the suggestion — a real, working example
of "suggestion, not compulsion," not a miss); the skill condition activated
in its 1 completed run (T2-A1's 3rd attempt) and never completed on T2-A2.

**A harness bug was found, fixed, and only partially re-verified given the
cost decision above**: the first full run's `--allowedTools` list
(`Bash,Read,Grep,Glob`) omitted `Edit` and `Skill`. `T2-C1`'s skill-condition
178s wall time is fully explained by a blocked `Edit` permission request the
agent stalled on waiting for approval that headless mode never provides
(`result_text`: *"The edit requires your explicit permission approval —
please grant access when prompted"*). Adding `Edit,Skill` to `allowedTools`
and rerunning `T2-A1`/skill in isolation (outside the full harness, to avoid
re-paying for the already-good hook-condition data) still timed out twice
before a third manual attempt succeeded in 41.7s.

**Root cause of the skill-condition timeouts, diagnosed from the successful
attempt's own tool-call trace, not guessed**: the skill condition's agent
ran `oxide query` once, then — apparently unconvinced by the result — ran
`oxide index .` *without* `OXIDE_EMBED_NATIVE=hashed` set in its own
environment (this phase's harness only set that variable for the
*indexing-during-setup* step, not for the agent's runtime environment). Per
`AGENTS.md`'s own documented behavior ("The default is no longer offline —
an unconfigured `oxide index` loads real ONNX weights through fastembed and
downloads ~23MB on first use"), that reactive re-index attempts a real
network fetch inside a sandboxed subprocess — a slow-to-hanging path
depending on network conditions, and the most likely explanation for two
near-180s timeouts against one 41.7s success. **This is a harness
environment-parity bug** (the fix is to also pass `OXIDE_EMBED_NATIVE=hashed`
into the agent's own env, not just the setup step) **that surfaced because of
a real, load-bearing behavioral difference between the two activation
surfaces**: `skills/oxide-code-context/SKILL.md` documents an "Index
behavior" section describing `oxide index` as something the agent may
reasonably invoke itself; the hook's injected snippet is deliberately
shorter and says nothing about indexing at all. In this small sample, that
extra documented surface is exactly what invited the reactive-reindex path
that hit the network-dependency risk — the hook's terser suggestion
happened not to. This is not evidence that hooks are categorically safer
than skills; it is evidence that a longer, more complete activation surface
has a larger action space, and that OXIDE's own environment-variable
propagation needs to be a harness concern wherever an agent might invoke
`oxide index` itself, not only where a human does.

**Known confound, disclosed rather than hidden**: unlike phase-3.1's
OpenCode runs (which used an isolated `OPENCODE_CONFIG`), these Tier-2
`claude -p` runs were **not** run with `--bare` (unavailable without a
separate `ANTHROPIC_API_KEY`) and so loaded this machine's real, large
personal `~/.claude` configuration — global skills, plugins, and MCP
servers — on every run. The diagnostic success run's own tool-call listing
shows entries for `codegraph`, `codex`, `ecc`, `engineering`, `humanizer`,
`superpowers`, and `tinyfish` alongside `Skill`/`Bash`/`Read`, and its token
count (431,879, mostly `cache_read`/`cache_creation`) is roughly 4-25x every
other cell in the table. **This means Tier 2's absolute token and cost
numbers are inflated by ambient, OXIDE-unrelated context specific to this
machine, and are not representative of what a real user's install would
cost.** The relative pattern within a matched pair (hook vs. skill, same
ambient pollution present in both) is more trustworthy than any single
absolute number here. This also surfaces a structural point worth
stating plainly: **the Skill tool's discoverability cost composes with
however many other skills are installed system-wide; a hook's injected
context does not** — a hook's overhead is bounded by its own code
regardless of what else is installed, while a Skill's invocation path is
not, at least on this machine's evidence.

## Hook overhead (isolated, not conflated with Tier 2's agent-run cost)

From Tier 1's own subprocess measurements (the cleanest source — no agent,
no network, no ambient config): **~40ms wall-clock per `UserPromptSubmit`
invocation, 0 tokens injected when it doesn't fire, ~70 tokens injected when
it does.** This is the number to cite for "hook overhead," not anything from
the Tier 2 table above, which is dominated by unrelated ambient-config noise
on this particular machine.

## `mise run verify`

Full pass, byte-identical to the frozen baseline (`docs/canonical-baseline.md`),
as expected for a phase with zero `src/` diff:

- `cargo fmt --check` — clean.
- `cargo clippy --all-targets -- -D warnings` (default and
  `--no-default-features`) — clean, both.
- `cargo test` — 237 + 228 unit/integration tests passed, 0 failed (default
  and `--no-default-features` feature sets).
- Canonical benchmark (`oxide eval --config fixtures/benchmark.json`):
  **hybrid recall@5 0.909, vector-only recall@5 0.818** — exact match to
  `docs/canonical-baseline.md`.
- Installer checks (`install.sh`/`shellcheck`): 64 passed, 0 failed.

## Greptile review

Not run this phase. This repo's `CLAUDE.md`/`AGENTS.md` workflow (fmt →
clippy → test → benchmark gate, "Work happens directly on main, no PR
workflow") pairs Greptile review with a pushed commit
(git log shows Greptile findings addressed in follow-up commits on `main`,
e.g. `5726999`, `4626a4b`). Nothing in this phase has been committed —
per the roadmap's own "before committing" ordering, review is the next step
once the user decides what (if anything) here gets committed, not something
to run against an uncommitted working tree.
