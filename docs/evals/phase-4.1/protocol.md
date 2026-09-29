# Phase 4.1 protocol — hook-based agent activation (roadmap #9)

Governing question: does an opt-in, local-first hook that suggests OXIDE for
unfamiliar multi-file discovery add real value over the existing skill-only
activation path (phase-2.3's E1 CLI+Skill wording, unchanged), without
forcing calls, blocking normal tools, injecting unnecessary context, or
touching the frozen retrieval/CLI/MCP contracts?

## 0. Frozen baseline

- Zero `src/` diff this phase. `mise run verify` is expected to be
  byte-identical to `docs/canonical-baseline.md`'s numbers; any diff is a bug
  in this phase's work, not an expected side effect (same rule phase-3.1
  used for its own zero-product-code phase).
- No roadmap document exists in this repository (`find -iname "*roadmap*"`
  and a full-text grep both came back empty) — the user's task instruction
  is the spec for "roadmap #9," not a restatement of a tracked item.
- No prior hook-related work existed anywhere in the repo before this phase
  (confirmed by `find . -iname "*hook*"` returning nothing outside this
  phase's own new files).

## 1. Primary-source research (not reused from training data)

Fetched/verified 2026-09-20, all via direct doc fetch or repo source read,
not summarized from memory:

- Claude Code: `code.claude.com/docs/en/hooks` (event reference),
  `code.claude.com/docs/en/hooks-guide` (quickstart layer),
  `code.claude.com/docs/en/headless`, `code.claude.com/docs/en/cli-reference`
  (headless `-p` mode, `--bare`, `--settings`, `--output-format`).
- Codex: `learn.chatgpt.com/docs/hooks`,
  `learn.chatgpt.com/docs/config-file/config-reference`,
  `learn.chatgpt.com/codex/build-skills`,
  `developers.openai.com/codex/mcp` — see `codex-feasibility.md`.
- OpenCode: `opencode.ai/docs/plugins/`, `opencode.ai/v2/docs/build/plugins`,
  `opencode.ai/docs/mcp-servers/`, `opencode.ai/docs/skills/`, plus the
  actual TypeScript source on `github.com/anomalyco/opencode` (the richest
  context-injection hooks are undocumented on the prose page) — see
  `opencode-feasibility.md`.

Key facts that shaped the design (see `contrib/agent-hooks/claude-code/README.md`
for the full contract citation):

- `UserPromptSubmit` is the one event that can add model-visible context
  without being able to replace the prompt, block it, or touch a tool call —
  the mechanical reason the hook was built on this event and no other.
- Plain stdout and `hookSpecificOutput.additionalContext` are both folded
  into a system-reminder Claude reads; neither produces a visible transcript
  entry. Exit 2 is the only blocking signal, and this hook never emits it.
- A project-committed hook runs under the same one-time folder-level
  workspace-trust dialog as a user's own `~/.claude/settings.json` hooks in
  an interactive session, but runs unconditionally, with no dialog, under
  `-p`/SDK sessions — documented by Claude Code itself as a real risk to
  flag before scripting `-p` over an unfamiliar repository.
- Phase 3.1's `client-compatibility.md` claim "Codex has no Skill mechanism"
  is now stale: Codex shipped both hooks and Skills since that phase. This
  phase's `codex-feasibility.md` records and corrects it.

## 2. Design decisions

- **Additive, not a replacement.** The hook is evaluated as an addition to
  the existing skill (mirrors phase-3.1's condition E design intent, but for
  a hook instead of a second transport) — nobody ships a hook by deleting
  the skill, so "hook vs skill-only" here means "hook present, skill absent"
  vs. "skill present, hook absent," isolating each mechanism's own marginal
  effect rather than testing a scenario no real install would produce.
- **`UserPromptSubmit`, not `PreToolUse`.** A `PreToolUse` gate can allow/
  deny/rewrite a tool call — worth ruling out explicitly, since it would
  violate the roadmap's own "without forcing calls, blocking normal tools"
  constraint. `UserPromptSubmit` cannot block or rewrite anything; the
  hook's own module docstring and README restate why.
- **Suggestion text is restated, not authored.** `additionalContext` is the
  exact phase-2.3-evidenced E1 wording already in
  `docs/agent-usage-policy.md`'s "Recommended AGENTS.md snippet," per this
  repo's rule that every OXIDE-facing surface restates that document rather
  than forking it.
- **Gate on index presence, not on calling `oxide status`.** The hook checks
  for `.oxide/index.db` directly (a plain filesystem check, walking up to 4
  parent directories and stopping at a `.git` boundary) rather than shelling
  out to `oxide status`, keeping the hook's own runtime path free of any
  process spawn, network call, or daemon — the "no LSP, daemon, or remote
  dependency" constraint applies to the hook's own implementation, not just
  to what it suggests calling.
- **One suggestion per session.** A long session doing several distinct
  discovery tasks is only nudged once, via a per-session flag file under
  `$XDG_CACHE_HOME/oxide/hook-seen/`. This trades missed-suggestion recall
  on a second, unrelated task later in the same session for materially
  lower noise — judged the right side of that trade for a hook whose entire
  brief is "don't be naggy."
- **Kept out of `src/`.** The prototype and its eval harness live under
  `contrib/agent-hooks/claude-code/` and `docs/evals/phase-4.1/raw/`. This
  makes "preserve existing CLI/MCP contracts and frozen retrieval behavior"
  true by construction rather than something to verify after the fact.

## 3. Measurement split (two tiers, not one)

Following the same logic phase-3.1's own recommendation.md used against its
own thin buckets ("n=3 per condition is too thin to act on"): the hook's
*trigger* is a pure function of prompt text plus index-file presence, so it
can be measured deterministically, at high n, with zero agent runs, before
spending any real-agent budget on the *downstream* question.

- **Tier 1 (deterministic, n=34, zero cost):** `raw/tier1_offline_eval.py`
  invokes `oxide_suggest.py` directly, exactly as Claude Code would (JSON on
  stdin, JSON-or-silence on stdout), over `raw/labeled_prompts.jsonl` — 14
  Bucket-A, 6 Bucket-B, 14 Bucket-C prompts, all newly authored for this
  phase (not reused verbatim from phase-3.1's `tasks.md`, per the roadmap's
  explicit "fresh held-out tasks," though the taxonomy is reused — the same
  choice phase-3.1 made in reverse, reusing tasks verbatim from phase-2.2).
  Also runs every prompt a second time against an unindexed directory as a
  control (must be 0 fires) and records subprocess wall-clock latency.
- **Tier 2 (real agent, n=6, small and directional):** `raw/tier2_agent_run.py`
  runs the actual `claude` CLI headlessly (`-p --output-format stream-json`)
  over 3 newly authored tasks (2 Bucket-A, 1 Bucket-C) grounded in fixture
  subsystems phase-3.1's own task set never touched
  (`notifiers.py`'s final-failure notification path;
  `service.ts`'s JWT payload decoding — chosen specifically so a model
  cannot be recalling this exact task text from a prior phase's public eval
  logs), x 2 conditions (hook, skill-only), 1 rep each. Explicitly
  small-n and not statistically powered — the same caveat phase-3.1 gave its
  own n=3 Bucket B. Scope and paid-run budget were confirmed with the user
  before running (a `--bare` isolated run needs a separate
  `ANTHROPIC_API_KEY`, unavailable here, so runs use the session's own
  subscription auth and incur real cost per invocation).

## 4. Tier 2 isolation and a harness bug found and fixed mid-run

- Each condition copies the target fixture (`fixtures/py_repo` or
  `fixtures/ts_repo`) into a fresh temp directory, `git init`s it (OXIDE's
  root discovery requires a git repository; the fixtures are plain
  subdirectories of this repo, not their own checkouts), and indexes it with
  `OXIDE_EMBED_NATIVE=hashed` for a deterministic, offline, reproducible
  index — matching the benchmark gate's own embedder choice.
- `hook` condition: `.claude/settings.json` wires in the real
  `oxide_suggest.py` by absolute path; no Skill file is present.
- `skill` condition: `skills/oxide-code-context/SKILL.md` is copied in
  unmodified; no hook is configured.
- **Bug found and fixed live**: the first full run's `--allowedTools` list
  (`Bash,Read,Grep,Glob`) omitted `Skill` and `Edit`. Loading a Skill is
  itself a tool call requiring permission approval; in headless `-p` mode
  with no one to approve it, the skill-condition runs on both Bucket-A tasks
  stalled to the full 180s timeout, and the Bucket-C run's `Edit` call
  stalled similarly (178s wall time, recovered only because Bucket-C doesn't
  need the edit to actually land to be scored). `results.md` reports the
  corrected re-run, not the timed-out first attempt, and calls out the
  asymmetry this exposed: **loading a Skill is a tool call an agent can be
  denied permission to make; receiving hook-injected context is not** — a
  real mechanical difference between the two activation paths, not only a
  harness artifact, worth stating plainly rather than letting a fixed
  `allowedTools` list quietly launder it away.

## 5. Verification gate (repeated at end of phase)

`mise run verify` (fmt, clippy, test, canonical benchmark) — expected
byte-identical to the pre-phase baseline, since this phase touches no
`src/` file. See `results.md` for the actual run.
