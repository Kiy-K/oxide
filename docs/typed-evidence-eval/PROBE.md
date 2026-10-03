# Typed semantic evidence probe: Julia-1 and Jev (preregistration)

Frozen before any probe score is computed. Its sha256 is in `PROBE.sha256`.
The two Julia smoke runs (§5) used one non-probe task
(`heldout/flask-a29f88ce`), and their scores were discarded unread. The Jev
API smoke used one synthetic state, not probe data.

## 1. Question

#31 asked Julia-1 one broad question ("would this evidence materially help?",
J1) per candidate, and it was at chance: AUROC 0.49–0.50, with gold,
same-file and other-file candidates all averaging ~0.75. Its `choice` form
(J2) was dominated by presentation position.

Here: **does decomposing the judgment into several independent, typed
questions separate useful from junk evidence better than #31's broad
judgment, and does that signal transfer?** The questions are relevance,
scope, primary evidence, implementation role, test role and concrete
reference, modelled on jevgrep's separation of relevance, scope, reference,
role and priority.

This is not Julia reranking and not a production change:
- OXIDE retrieval, fusion and the allocator stay frozen.
- Julia's probabilities are treated as semantic evidence only; fixed
  deterministic rules (§6) combine them.
- `choice` is not used. Every question is pointwise, one file per state, so
  there is no presentation order.

## 2. Models and runtime (fixed)

Two arms answer identical states and questions (`results/states.jsonl`):

**Julia (local)**

- `SupersonicLabs/Julia-1` @ `a85b127321d580d65176c89ced8273f305745d85`,
  weights sha256 `df853bf7…` (the same pin as #31). 144.3 M parameters, run
  above the 100 M local default because Julia was requested explicitly.
- Official `julia/` runtime: `strict_encoding=True`, `max_length=8192`,
  `head_length=512`; torch 2.14.1+cpu, transformers 5.0.0, Python 3.11, fp32
  CPU. transformers 5.18 is incompatible with the runtime's router (smoke 1).
- Every run has no network (`unshare -rn`, `HF_HUB_OFFLINE=1`).

**Jev (hosted)**

- TypeSafe `jev-1.13.0`, a pinned versioned ID rather than an alias. One
  request per file carries all seven questions, answered in parallel over the
  same state. Requests go to `POST https://api.typesafe.ai/v1/systemone`.
- Jev is the model jevgrep was designed around. It separates "the typed
  decomposition carries no signal" from "Julia-1 is too weak". It costs
  nothing locally.
- Data sent: task text and source excerpts from public open-source repos
  only, no private code. The API key is read from the gitignored `.env` and
  never logged.

## 3. Universe, labels, sample (fixed; `scripts/views.py`)

- The source universe is the committed, verified #39 universe
  `docs/joint-interaction-eval/results/cands.jsonl.gz`: fused top-50 symbols
  per task, with source at the indexed revision.
- **File view:** the first 12 distinct files by best fused rank. Each file
  shows its candidate symbols' spans in line order; nested spans are dropped,
  and a module span is used only if the file has no other candidate.
- **File label:** positive iff any of its candidates is positive (#39 labels:
  heldout gold key; cb non-module span overlapping a ContextBench gold line).
- **Sample:** file-eligible tasks (≥ 1 positive and ≥ 1 negative file), seed
  39, round-robin over repos. **cb 6** (leakage-free) and **heldout 4** (the
  post-commit text can favor any joint reader). That is 110 files and 18
  positives across 9 repos and 4 query classes. Labels are used only for
  eligibility.

## 4. Julia input and questions (fixed; `scripts/score.py`)

- State: `Coding task:` (query cut to 192 Julia tokens), `File:` path,
  `Declarations:` (≤ 20 qualified names), `Source:` (cut to 640 Julia
  tokens). Neither model sees OXIDE ranks, scores or labels.
- Seven independent `noul` questions per file, each answered as P(true).
  Their wording is in `score.py`.
  - `relevant`: directly implements, controls or tests the behavior.
  - `in_scope`: part of the targeted API or component.
  - `primary`: should be read first as primary evidence.
  - `implementation`: code that executes or controls the behavior.
  - `test`: tests that validate the behavior.
  - `concrete_reference`: defines or uses a name given in the task. jevgrep's
    `reference` needs previously selected evidence, which a one-pass probe
    does not have, so this is anchored to the task text instead.
  - `J1`: #31's question and options verbatim. This is the in-universe
    control.
- Nothing is tuned. No wording is changed after scoring.

## 5. Safety and cost (fixed; `scripts/run.sh`)

- One process: `systemd-run --user --scope` with `MemoryMax=2560M`,
  `MemorySwapMax=0`, `CPUQuota=200%`; 2 torch threads; `nice -n 10`; a
  timeout. A watchdog stops the scope if host `MemAvailable` drops below
  1.5 GB or swap grows by more than 200 MB. On any abort or cap trip the
  probe stops; caps are not raised.
- Julia smoke (one non-probe task, 12 files): load 6.3 s; 36 s per task;
  median 3.6 s per file (7 questions); cgroup peak 1.0 GB. This projects the
  probe at about 7 minutes, under the 20-minute limit.
- Jev smoke (one synthetic state, 7 questions): 683 ms per request.
- Scored artifacts and models live on disk (`~/.cache/oxide-typed-eval/`),
  not in tmpfs `/tmp`.

## 6. Scorers (fixed, no fitting)

Controls:
- `fused`: −(best fused rank of the file);
- `random`: 200 seeded draws;
- `J1`.

Descriptive: each of the six typed dimensions alone.

Gate candidates (deterministic, fixed):
- `T-gate` = P(relevant) × P(in_scope), i.e. jevgrep-style relevance gated
  by scope;
- `T-mean` = mean of P(relevant), P(in_scope), P(primary),
  P(implementation) and P(concrete_reference). `test` is a role descriptor
  and is reported alone.

With OXIDE order (descriptive unless §7 B is reached): `G+F` =
1/(60 + rank_G) + 1/(60 + best fused rank), with rank_G ties broken by fused
rank.

## 7. Metrics and decision rule (fixed)

Metrics:
- within-task file AUROC (macro), pairwise accuracy, R@3;
- paired task-bootstrap Δ, using #32's `common.py` (2000 resamples, seed
  32), pooled over the 10 tasks and reported per set.

A and B are evaluated separately for each arm (Julia, Jev), always
against that arm's own J1.

**A. TYPED SIGNAL** iff some G ∈ {T-gate, T-mean} meets all of:
1. Δ AUROC(G − J1) ≥ +0.10 with 95 % CI lower bound > 0 (pooled);
2. AUROC(G) 95 % CI lower bound > 0.5 (pooled);
3. transfer: Δ(G − J1) > 0 on cb and on heldout; > 0 in ≥ ⅔ of repos; and
   no query class with ≥ 3 tasks has a negative mean Δ(G − J1).

**B. LIFT over OXIDE** iff A holds and, for the same G, Δ AUROC(G+F − fused)
≥ +0.05 with CI lower bound > 0 (pooled) and > 0 on both sets.

Verdict, per arm; the direction closes only if neither arm reaches A:
- not A → **NO TYPED SIGNAL** for that model; if neither arm reaches A,
  **close the direction**;
- A but not B → **TYPED SIGNAL, NO LIFT**: report which dimensions carry
  signal; no production design;
- A and B → **LIFT**: report which dimensions carry signal; the only allowed
  next step is a larger preregistered probe. No production design follows
  from this probe.

Cost is recorded, not gated: per-file latency, per-task latency, and peak
RSS for Julia. Per #31, Julia on CPU is far over OXIDE's synchronous budget
(median ≤ 250 ms). Jev adds a network dependency, which OXIDE's local-first
design does not have. Any positive result therefore holds for offline
semantic evidence only.

n = 10 cannot confirm a lift. The rule asks only whether typed decomposition
shows a clear, transferable improvement over #31's broad judgment.
