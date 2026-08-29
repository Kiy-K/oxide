# Compact-toolset agent evaluation (section I)

A small, real, headless-agent experiment (`opencode` +
`opencode/muse-spark-1.2-contributor-free`) comparing how a coding agent
uses OXIDE's tool surface under three conditions, on a copy of
`fixtures/py_repo` (already indexed before any run; index held constant
across conditions).

- **A — no OXIDE**: task text only, no mention of `oxide`.
- **B — full CLI**: preamble lists all 7 subcommands (`index`, `status`,
  `search`, `review`, `stats`, `context`, `eval`) as in `README.md`.
- **C — compact surface**: preamble covers only `oxide context --task ...`
  and `oxide search "..."`, using the guidance already in
  `docs/agent-usage-policy.md` / `skills/oxide-code-context/SKILL.md`.

Two tasks: an unfamiliar-repo localization task (retry eligibility for 4xx
vs 5xx) where OXIDE is expected to help, and a trivial single-file rename
edit where the correct behavior is to **not** need OXIDE at all.

## Results

| task | condition | correct | oxide calls | wall time | preamble tokens (est.) | note |
|------|-----------|:-------:|--------------|----------:|------------------------:|------|
| localize (4xx/5xx retry) | A (none) | Y | 0 | 29.7s | 0 | never touched oxide, read files directly, still correct |
| localize | B (full CLI) | Y | 2 (`search`×2) | 33.7s | ~170 | broad query then a narrower one — redundant second call |
| localize | C (compact) | Y | 1 (`search`×1) | 23.0s | ~169 | one targeted call, correct, fastest of the three |
| trivial rename edit | A | Y | 0 | 29.6s | 0 | correct, all sites |
| trivial rename edit | B | Y | 0 | 33.5s | ~170 | correct, did not misuse oxide despite full knowledge |
| trivial rename edit | C | Y | 0 | 44.8s | ~169 | correct, slower run but no oxide waste |

## Interpretation

All 6 cells completed the task correctly — the fixture repo is small enough
that even condition A (no OXIDE mentioned at all) solved the localization
task by reading files directly, in the least wall time of any localize
cell. This run cannot show a correctness gap; the real signal is
tool-selection behavior.

**On the discovery task**, full-surface knowledge (B) triggered two `oxide
search` calls (broad, then narrower) where compact knowledge (C) reached
the same correct answer in one targeted call, in less wall time. This is a
small but real instance of the "more surface invites more exploratory
calls, not better ones" concern the phase spec raises — it favors the
compact surface.

**On the trivial edit**, neither B nor C misused `oxide` despite B having
full knowledge of `index`/`status`/`stats`/`review`/`eval` — no evidence of
reflexive tool invocation for a task that plainly didn't need it. This is a
mild negative result against the "full surface causes waste on easy tasks"
half of the hypothesis, at least for this model.

**Preamble token cost** came out nearly identical between B (~170 tok) and
C (~169 tok) in this run because both preambles were written as similar-length
prose. This almost certainly **understates** the real gap a future MCP
transport would see: MCP tool schemas are JSON (name + description + typed
parameters) resident in the system prompt on every turn, and 7 tool schemas
scale worse than 2 as structured definitions than as one paragraph of
prose each. The real per-turn cost should be measured from actual MCP tool
schema JSON when Phase 2 builds it — see `docs/agent-context-overhead.md`
for the reasoning-based estimate used until then.

## Caveats

n=1 per cell (no repeats), one small repo, one task per shape — directional,
not statistically powered. The clearest, most repeatable finding is
call-count discipline on a successful outcome (1 vs 2), not a correctness
delta. All 6 `opencode` runs completed within timeout on the first attempt;
no harness reliability issues to report.

## Conclusion for section H's hypothesis

`context` alone is not enough by itself for a follow-up-question shape (the
localize task's correct call was a `search`, not a `context` call, in both
B and C) — so **`context` + `search`** is the surface this evaluation
supports, consistent with the classification already in
`docs/agent-surface.md`. The compact framing (C) did not cost correctness
and showed tighter call discipline than the full-CLI framing (B) on the one
task where extra tools were available to misuse.
