# OXIDE agent-level benchmark: Phase 0 pilot report

**Status: frozen.** Both studies below are complete and are not being rerun or tuned against. This report is the record of what was found before any further benchmark spend.

Agent: OpenAI Codex CLI (`codex-cli 0.154.0`), model `gpt-5.6-luna`, invoked with `--ignore-user-config --skip-git-repo-check --approve-for-me` (see "Harness" below). OXIDE at git SHA `680c2816a268931645fe7b5a0af9ecc5d356949b` for every run in both studies -- i.e. *before* the `path`-description fix in section 5, which that data is what motivated.

---

## Headline results

1. **Study 1 (natural adoption): OXIDE used 0/24 times when merely available.** Across two independent task sets (18 runs on small/famous repos, 6 runs on Deno/uv), the agent never once called the OXIDE MCP tool when it was simply present alongside normal tools -- not "considered and rejected," never surfaced in its reasoning at all.
2. **Study 2 (pilot, n=1/condition, directional only): a one-paragraph routing policy changes that from 0% to 83-100%.** With a realistic "use OXIDE for unfamiliar discovery" instruction (condition B), the agent used OXIDE on 5/6 held-out tasks. Forced to call it once (condition C, diagnostic only), it did so 6/6 times, and every one of those forced calls reduced tool calls, files touched, input tokens, and wall time versus the no-OXIDE baseline, with no quality drop visible at this sample size.
3. **Two harness bugs and one real OXIDE tool-usability defect were found and fixed along the way** (sections 4-5) -- none of them retrieval/ranking changes.

---

## 1. Study 1: natural adoption (frozen)

**Question:** does the agent spontaneously reach for OXIDE when it's simply available, with no framing about when to use it?

**Result:** across every "oxide"-condition run collected, `oxide_calls` is 0.

| Task set | Repos | Tasks x conditions x reps | oxide-condition runs | oxide_calls |
|---|---|---|---|---|
| Round 1 | Flask, seaborn, darkreader, tailwindcss (small, famous OSS) | 6 x 2 x 3 | 18 | 0 |
| Round 2 first pass | Deno, uv (large, real-world) | 6 x 2 x 1 | 6 | 0 |
| **Total** | | | **24** | **0** |

Raw data: `results_v1/results.jsonl` + `results_v1/judged.jsonl` (round 1, 36 total runs both conditions), `results/results.jsonl` (round 2's final 6-task Deno/uv set, 12 runs). Per-run trajectories: `results_v1/logs/`, `results/logs/`. Tasks: `tasks_v1.json` (round 1), `tasks.json` (round 2's final set).

Both sub-runs used the harness in `run_pilot.py`: two conditions, `baseline` (no MCP server) and `oxide` (OXIDE wired in via `-c mcp_servers.oxide.*`, real production install shape), identical prompt otherwise. Investigated directly against real transcripts (not just the counter): in every oxide-condition run inspected, the agent shows zero `mcp_tool_call` items and zero reasoning/agent-message text mentioning any tool besides the shell -- it isn't weighing OXIDE and picking bash, the tool is never surfaced as a candidate at all. This held on famous, small, likely-memorized code (Flask, tailwindcss) and on large, real, non-memorized code (Deno, uv) alike.

**Interpretation:** this is a tool-discoverability/adoption problem in the agent's default behavior, not evidence that OXIDE's retrieval is unhelpful -- the retrieval was never exercised. Framed as an adoption question, this motivated Study 2.

---

## 2. Study 2: does a routing policy change adoption, and does it help when it fires? (pilot, directional)

**This is a pilot at n=1 per condition on 6 held-out tasks. It is not final evidence -- it establishes direction and whether the harness/questions are sound enough to scale.**

### Conditions

- **A -- Native:** normal shell tools only, no OXIDE MCP server.
- **B -- OXIDE-guided** (the realistic product-integration condition): OXIDE available + one added paragraph: *"For unfamiliar multi-file code discovery, use OXIDE `query` before broad grep/read exploration. Use OXIDE `search` for focused symbol/concept lookup. Use normal grep/read for exact literals, known files, and verification."*
- **C -- OXIDE-first** (diagnostic only, not a natural-behavior claim): same as B, plus a hard instruction to call OXIDE `query` exactly once before anything else. This is prompt-level compliance, not code-enforced (Codex's CLI has no hook to force a specific first tool call) -- `first_call_is_oxide` in each result record shows whether the model actually complied, so non-compliance would be visible, not hidden.

All three conditions additionally carry the same neutral line used in Study 1: *"Use any available repository tools you find useful. Base your answer on the checked-out source rather than prior knowledge."*

### Tasks

6 fresh, held-out tasks against Deno and uv (already-indexed from Study 1, no new indexing) -- **not** the 12 tasks used in Study 1, and **now retired as development data**, not to be reused as evidence in a future run. Each requires genuine cross-file discovery (entry-point tracing, config-to-behavior, interface-to-implementors, blast radius, test-constrains-behavior) against real, recent code paths, verified by hand against the actual pinned source before the pilot ran. Full definitions with verified reference answers: `tasks_study2.json`.

### Results (n=1 per condition -- point estimates, not statistically powered)

| Condition | Activated (oxide_calls>0) | Succeeded (oxide_calls_ok>0) | Avg tool calls | Avg files inspected | Avg input tokens | Avg wall time | Avg quality (0-5) | Grounded |
|---|---|---|---|---|---|---|---|---|
| A -- Native | 0/6 | 0/6 | 6.5 | 5.5 | 392,000 | 69.4s | 4.50 | 6/6 |
| B -- OXIDE-guided | 5/6 (83%) | 4/6 | 7.5 | 4.5 | 353,131 | 73.5s | 4.33 | 5/6 |
| C -- OXIDE-first | 6/6 (100%) | 6/6 (100%) | 5.8 | 4.7 | 342,619 | 65.2s | 4.25 | 6/6 |

Full per-task, per-run detail (all 18 runs, not just averages): `results_study2/results.jsonl` + `results_study2/judged.jsonl`. Raw trajectories: `results_study2/logs/*.jsonl` (Codex `--json` event stream) and `*.answer.txt` (final answers). Quality = judge's (accuracy + completeness) / 2, condition-blind (see Study 1's `judge.py`/`test_redaction.py`, reused unchanged as `judge_study2.py`).

### Answering the three questions (directional, n=1)

1. **Does a tiny realistic routing policy cause Luna to use OXIDE? Yes, clearly.** 0% (Study 1, n=24) -> 83% (B, guided) -> 100% (C, forced). The gap between B's 83% activation and its 67% success rate (4/6 succeeded of the 5/6 that tried) is explained entirely by the tool-usability defect in section 3, not by the routing policy failing.
2. **When OXIDE is used, does it reduce repository wandering? Yes for C; mixed for B.** C (100% activation, 100% success) is cheaper on every measured axis: tool calls -11%, files inspected -15%, input tokens -13%, wall time -6%, all versus A. B is mixed: files (-18%) and tokens (-10%) go down, but total tool calls (+15%) and wall time (+6%) go *up* slightly -- because on the 2/6 tasks where B's OXIDE call added no value (one task never called it; one got two failed calls from the `path` defect below), the agent still paid full manual exploration on top of the wasted OXIDE-call overhead.
3. **Does that efficiency come without reducing answer quality? No sign of a cost, but not confirmed at this sample size.** Quality is flat within noise (4.50 / 4.33 / 4.25 on a 0-5 scale, n=6 per condition). One grounding miss appears in B (`uv-wheel-tag-compatibility`, invented a detail not in the gold source) -- worth specifically re-checking at scale, not dismissed here.

**No stop condition was triggered:** B activates OXIDE consistently (not "near-zero"), C shows a real, consistent efficiency reduction when activation is guaranteed, and quality does not visibly regress. This pilot is a green light to scale, once the fixes below are in place -- scaling was deliberately paused here per instruction, before spending further benchmark runs.

---

## 3. Harness bugs found and fixed (before the data above was collected)

Both were infrastructure defects in the Python harness (`run_pilot.py` / `run_pilot_study2.py`), not in OXIDE itself, and both were fixed before any of the numbers in section 2 were collected:

- **`/tmp` exhaustion.** `/tmp` on this machine is an 8GB tmpfs, and the harness copied a full repo (including OXIDE's index, 250MB+ for Deno) per run without cleaning up until the whole grid finished. Two runs' worth of large copies filled it and crashed a run mid-grid with `Disk quota exceeded`. Fixed: temp staging moved off `/tmp` to a disk-backed directory (`eval-agent/agentbench/.run_tmp/`), and each run now deletes its own repo copy immediately after finishing instead of leaving it for the whole grid to accumulate.
- **MCP subprocess not inheriting the embedder config.** Codex does not pass this process's environment into an MCP server's subprocess by default. `oxide mcp`, spawned without `OXIDE_EMBED_URL`/`OXIDE_EMBED_MODEL`, silently fell back to OXIDE's default native embedder -- a completely different embedding space than the Jina-hosted embedder the indexes were actually built with -- and every call failed with `provider_mismatch`. This was caught because Study 2's routing policy made the agent actually call OXIDE (Study 1 never exercised this path, since `oxide_calls` was always 0 there). Fixed: `run_condition` now passes `-c mcp_servers.oxide.env.OXIDE_EMBED_URL=...` and `.env.OXIDE_EMBED_MODEL=...` explicitly, verified against a live `codex exec` call before rerunning.

An earlier switch from a local llama.cpp embedder to a hosted Jina embedder (`jina-embeddings-v5-omni-nano`, proxied locally in `jina_embed_proxy.py` to add the `Authorization` header and `task`/`normalized` fields OXIDE's plain embedder client doesn't send) was a deliberate infrastructure decision, not a bug -- the local embedder's single-threaded throughput made indexing Deno/uv/Zed impractical (~43 hours projected for Deno alone at the observed rate).

---

## 4. OXIDE product fix: the `path` MCP parameter

**Investigated as a product usability question, not benchmark tuning, and applied to `src/mcp.rs` directly (git SHA after `680c2816a`, not part of the data collected above).**

**What was found:** every real, malformed OXIDE tool call observed across both studies failed the same way -- the model guessing at the undocumented `path` argument. First it passed `""` (empty string), rejected as an empty path. Later it passed a subdirectory like `"cli"`, expecting it to *scope* the search within an already-loaded index; OXIDE rejected it with `index_missing`, because `path` is actually a repository *root* to discover a whole separate index at, not a filter within one already open. Both are natural, reasonable guesses given zero schema description -- not model carelessness.

**Investigated whether `path` should be exposed to agents at all:** `RepositoryService::discover()` already walks upward from the current directory looking for `.git`/`.oxide`, so a normal agent invoked with its cwd inside the target repository never needs to pass `path` -- omitting it always finds the right root. That covers the common case completely. However, `path` is not vestigial: `tests/mcp_e2e.rs`'s entire test-isolation strategy depends on passing an explicit path to target isolated fixture repos rather than relying on the test binary's own cwd. Removing the parameter would require rewriting that test suite's isolation mechanism -- a materially larger, riskier change than a usability fix warrants, and out of scope here.

**Fix applied:** kept `path` in the schema, added a `description` explaining the real contract plainly: *"Repository root containing the OXIDE index. Omit this to use the current repository -- that's correct for almost every call. Only pass it to target a different repository than the one you're running in. Never pass a file, an empty string, or a subdirectory that isn't itself an indexed repository root."* No retrieval/ranking behavior, validation logic, or the `RepositoryService::discover` contract changed. Verified: `cargo build --release`, `cargo fmt`, `cargo clippy --all-targets` (clean), `cargo test --test mcp_e2e` (9/9 pass, including the schema-shape test), full `cargo test` run alongside this report (see repo for latest status).

---

## 5. What's next (not yet executed)

Per instruction, scaling is paused here. The next phase, when resumed:

1. Curate a **fresh held-out set** of 8-10 tasks from pinned recent commits of large/complex repos (Deno, uv, Zed, or similar) -- distinct from both this pilot's 6 tasks and Study 1's 12 tasks, all three sets now retired as development data.
2. Run **A (native) vs. B (OXIDE-guided)** as the primary comparison, 3 repetitions per task. Keep **C (OXIDE-first)** only as a smaller diagnostic condition alongside it, never as the headline.
3. Capture per run: quality/judge result, input/output/cached tokens, total tool calls, shell search/read calls, OXIDE calls, unique files inspected, wall time, OXIDE context tokens returned, full trajectory. Report indexing cost separately from agent time.
4. Do not optimize OXIDE during that run. Report at the end whether OXIDE-guided reduces repository exploration and context cost without materially reducing answer quality -- as a real comparison this time, not a pilot.
