# Handoff 2026-10-04: #43 frozen run + TreeIndex/TreeRouter implementation

Two independent work streams. Read `AGENTS.md` (= `CLAUDE.md`) and the memory notes
(`local-model-limits`, `tree-direction`) first.

## Stream 1 — #43 cap-removal validation (running unattended; finish and report)

- **What:** arm B (the shipped selector without the 5-primary and 2-per-file caps, token-matched to
  A) vs A on fresh tasks. The protocol is frozen at `b400c13` (`docs/cap-removal-eval/PROTOCOL.md` =
  the #43 issue body). History and disclosures: `docs/cap-removal-eval/README.md` (two pauses, the
  clap/zod worktree cleanups, the phase split, the pilot result: 30 tasks, B vs A +0.068, 3/0,
  descriptive only).
- **Running now:** detached chain `bash …/scratchpad/phase23.sh`, PID **235332**
  (`ps -o pid= -p 235332`). Log:
  `/tmp/claude-1000/-home-khoi-Projects-oxide/6037cda2-95b0-4329-9466-d9899710274a/scratchpad/phase23.log`.
  A copy of the script is in `docs/cap-removal-eval/phases/phase23.sh`. Sequence:
  1. `docs/cap-removal-eval/scripts/gen_all.sh b400c13`: held-out raw (7/8 repos done at handoff;
     axios next), parent indexes and filters, `build_tasks.py heldout`, then the ContextBench draw
     (seed 43). This is hours of capped indexing.
  2. `run.py parity`, `run.py arms`, `run.py rss`, then `score.py`.
  3. The verdict lands in `docs/cap-removal-eval/results/result.txt` (+ `result.json`,
     `per_task.tsv`, `packs.jsonl`, `parity_p1.tsv`, `rss_o1.tsv`), in the **main** checkout.
- **If the chain dies:** every stage is idempotent. Rerun `bash docs/cap-removal-eval/scripts/gen_all.sh b400c13`,
  then `~/.cache/oxide-typed-eval/venv/bin/python docs/cap-removal-eval/scripts/run.py {parity,arms,rss}`
  and `…/score.py`. That venv has numpy and pyarrow; the system python3 has neither.
  - If a held-out repo stage was interrupted (raw file without `.done`), first remove that repo's
    partial worktrees under `~/.cache/oxide-capremoval-eval/wt/<repo>-*` with
    `git -C ~/.cache/oxide-jev-eval/repos/<repo> worktree remove --force …`. `make_tasks.py` reuses
    an existing worktree without re-indexing it. Disclose any interruption in the README.
- **When done, with no extra experiments:**
  1. Write the verdict and the R1 regression causes into `docs/cap-removal-eval/README.md`.
  2. Commit `docs/cap-removal-eval/results/`.
  3. Post the outcome as a comment on #43, preserving a negative result if it is one.
- **Disposition rules** (from the protocol): validity needs ≥ 50 held-out, ≥ 50 ContextBench and
  ≥ 6 held-out repos, and P1 must pass; otherwise INCONCLUSIVE. Q1, Q2 or E1 failing → REJECT. All
  gates passing → PASS. Q1, Q2 and E1 passing but R1 or O1 failing → NOT PASSED.

## Stream 2 — TreeIndex / TreeRouter (inline plan execution, Task 1 in progress)

- **Spec:** `docs/superpowers/specs/2026-10-04-treeindex-treerouter-design.md` (approved).
  **Plan:** `docs/superpowers/plans/2026-10-04-treeindex-treerouter.md` (approved; the user chose
  **Native** = inline execution with the `superpowers:executing-plans` skill, plus one fresh
  whole-branch review at the end).
- **Worktree:** `/home/khoi/Projects/oxide-treeindex`, branch `feat/treeindex` (base `e03fd2b`).
  Never implement on `main`.
- **Ledger:** `/home/khoi/Projects/oxide-treeindex/.superpowers/sdd/2026-10-04-treeindex-treerouter/progress.md`
  (pre-flight rows and one ruling). Task scripts:
  `~/.claude/plugins/cache/anthropic-plugin-directory/superpowers/6.4.2-8ca22dba9a94/skills/executing-plans/scripts/{task-start,task-done}`.
- **Task 1 state:** RED, verified.
  - `tests/tree_storage.rs` is new (3 tests) and `tests/index_generation.rs` gained 4 tree steps.
    Both are **uncommitted**.
  - They fail to compile on the missing `oxide::tree`, `write_tree_files`, `put_tree_vectors`,
    `clear_tree_vectors` and `write_tree_dirs`.
  - Next, implement per the plan:
    - `src/tree/mod.rs` (constants, `NodeKind`, `TreeNode`, `node_id` via `fnv1a64_iter`);
    - the tree tables appended to `SCHEMA_SQL` in `src/storage/schema.rs`;
    - `IndexRead` tree readers with empty default impls (`CountingStore` in `engine.rs` tests also
      implements `IndexRead`);
    - the `IndexWrite` tree writers in `src/storage/sqlite.rs`, each one IMMEDIATE transaction +
      `bump_generation`.
  - Implementation notes decided so far:
    - use an UPSERT (`ON CONFLICT(id) DO UPDATE`), **not** `INSERT OR REPLACE`, for file nodes, so a
      rewrite keeps the node's vector (test `rewriting_a_file_node_keeps_its_vector`);
    - `tree_postings` is `WITHOUT ROWID`, PK `(term, node_id)`, plus an index on `node_id` for the
      cascade (mirrors `lexical_postings`);
    - file postings are SQL aggregates of `lexical_postings`/`lexical_docs` joined via `symbols.file`.
- **Build/test:** `CARGO_TARGET_DIR=/home/khoi/Projects/oxide-treeindex/target systemd-run --user --scope -q -p MemoryMax=6G -p CPUQuota=200% nice -n 10 cargo test -j 2 --test tree_storage --test index_generation`.
- **Gates in the plan:**
  - Task 5 (Phase 0 dev): stop if no router beats flat recall@5.
  - Task 6 (fresh validation, seed 44, **not** #43's tasks): stop on FAIL.
  - Task 8 only preregisters Phase 1.
  - Tasks 5–6 run embedding models: never concurrently with #43's chain (one model process at a
    time). Wait for it to finish, or SIGSTOP its process group (`kill -STOP -- -235332`, later
    `-CONT`) and disclose.

## Gotchas learned this session

- The GateGuard hook asks for "facts" before the first Bash command and before each new file's first
  write or edit. Answer them briefly, then retry.
- `pkill -f <pattern>` / `pgrep -f` match your **own** shell's command line (exit 144 kills your
  shell). Kill by PID. Waiters whose command line contains a pattern block `pgrep`-based waits.
- `setsid nohup cmd &` forks: the real PID is `$!`+1.
- Git has no global identity. Commit with
  `git -c user.name="Khoi Truong" -c user.email="tannguyenkhoitruong@gmail.com" commit …` and end the
  message with the `Claude-Session:` trailer line.
- Local commits go to `main` (`docs/agents/workflow.md`). **Nothing has been pushed**, so don't push
  unless asked.
- Don't start shortlist-reranking experiments (#42 is closed; see memory `tree-direction`).
