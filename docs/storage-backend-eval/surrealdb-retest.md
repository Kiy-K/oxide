# SurrealDB retest, under the model its own docs describe

The first round rejected SurrealDB on three grounds. **Three of them were my
methodology errors, not properties of the database.** This document records the
retest, what changed, and what survives.

## What the first round got wrong

| First-round claim | What was actually wrong | Corrected result |
| --- | --- | --- |
| **G0** "+221 crates, 22 m 32 s cold build, compiles RocksDB from C++" | Measured `kv-rocksdb`. SurrealDB's deployment-models doc recommends RocksDB for on-disk **server** deployments and names **SurrealKV** the preferred choice for **embedded** ones. | `kv-surrealkv`: **5 m 25 s**, 446 crates in the lockfile, **zero** RocksDB crates and no C++ compile at all. |
| **G1** "FAIL — a second process cannot open the store" | Conflated two different things. Multiple processes opening one embedded store is not a model SurrealDB offers — that is true of the engine by construction, not a defect. The model the Rust SDK documents is *in-process*: one embedded instance, `Surreal` handles cloned into concurrent Tokio tasks. | Split into **G1a** (in-process, documented) and **G1b** (multi-process). SurrealDB **passes G1a** on both engines, and beats SQLite on concurrent writes. G1b is now reported as the engine's model. |
| **40k "cannot build its vector index"** and **G4d "HNSW 7–8/10 recall"** | Both were artifacts of `DEFINE INDEX … HNSW` *after* a bulk load. | With the index defined **before** ingest so it is maintained incrementally, 40k completes on both engines and recall is **10/10 at the default `ef=64`**. |

A fourth error was mine on the SQLite side: the first G1a attempt showed SQLite
losing 15 of 16 concurrent writers to "database is locked". That was a
`DEFERRED` transaction in the harness — `replace_file` reads before it writes, so
it hit `SQLITE_BUSY_SNAPSHOT`, which `busy_timeout` does not retry. With
`BEGIN IMMEDIATE` SQLite lands 64/64.

## G1a — the documented concurrency model

One store, handles cloned across concurrent Tokio tasks on a multi-thread
runtime. SQLite's honest counterpart is OS threads with one connection each,
opened *before* the timed section (SurrealDB's cloned handle shares an
already-open store, so timing SQLite's connection setup would compare two
different things). Raw: [`raw/concurrency-split.txt`](raw/concurrency-split.txt).

| | Enhanced SQLite | SurrealDB rocksdb | SurrealDB surrealkv |
| --- | --- | --- | --- |
| 40 concurrent reads, all exact | PASS | PASS | PASS |
| 40 reads: sequential → concurrent | 1 ms → 20 ms | 335 ms → 43 ms | 219 ms → 23 ms |
| 16 concurrent writers, disjoint files | PASS, 64/64 | PASS, 64/64 | PASS, 64/64 |
| concurrent write wall time | 744 ms | **107 ms** | **95 ms** |

**SurrealDB genuinely wins concurrent writes** — roughly 7–8× — because SQLite
serialises writers by design. That is a real advantage the first round missed
entirely. It is worth noting that OXIDE's indexer is currently a single
sequential writer, so the advantage is latent rather than realised.

The read row cuts the other way and is easy to misread: SurrealDB's ~10×
concurrency speedup is real, but it is recovering from a much slower starting
point. SQLite answers all 40 queries sequentially in 1 ms — faster than either
engine manages with 40 tasks in flight.

## G1b — two processes, one store

Both embedded engines take an exclusive lock:

- rocksdb: `IO error: While lock file: …/LOCK: Resource temporarily unavailable`
- surrealkv: `Database at …/LOCK is already locked by another process`

This is the engine's model and is reported as such, not as a failure. SQLite's
equivalent probe (a reader opening while a writer holds an open, uncommitted
write transaction) returns a consistent snapshot.

The consequence for OXIDE is architectural rather than a scorecard entry, and is
assessed below.

## The findings that survive

### Cold-process cost — G1c

One query from a cold process, 10,000 symbols, best of three:

| Enhanced SQLite | SurrealDB rocksdb | SurrealDB surrealkv |
| --- | --- | --- |
| **< 10 ms** | 0.29–0.48 s | 0.54–0.62 s |

Seven of OXIDE's nine subcommands are one-shot processes that open the store and
exit. Every one of them would pay this.

### Warm per-query cost

A daemon buys back the cold open. It does not buy back this — a context-shaped
composite (1 BM25 + 1 vector top-10 + 3 relation lookups) in an already-warm
process, best of three. Raw: [`raw/context-shaped.txt`](raw/context-shaped.txt).

| symbols | Enhanced SQLite | SurrealDB rocksdb | SurrealDB surrealkv |
| --- | --- | --- | --- |
| 10,000 | **8 ms** | 54 ms | 89 ms |
| 40,000 | **32 ms** | 278 ms | 375 ms |

The gap is 7–12× and widens with corpus size. Two components drive it: literal
substring search has no index in SurrealDB at all (182 ms at 10k, ~750 ms at 40k,
against 0.5 ms and 1.3 ms), and at 40k on rocksdb the HNSW query itself degrades
to **3.2–4.4 seconds**.

### SurrealKV silently loses a committed transaction

The sharpest new finding, and it is on the engine the docs recommend for
embedded use. At 40,000 symbols with the HNSW index maintained incrementally,
**exactly one contiguous 1,000-row chunk — one whole transaction — vanishes**,
at a different offset each run, with every write having reported success.
Raw: [`raw/surrealkv-lost-writes.txt`](raw/surrealkv-lost-writes.txt).

```
run 1: rows=40000 with_vec=39000 missing_vec=1000   ids 36000..36999
run 2: rows=40000 with_vec=39000 missing_vec=1000   ids 34000..34999
```

The same harness and the same chunking lose nothing on RocksDB, nothing at 10k,
and nothing on SurrealKV when the index is built after the load — so this is not
the chunking code. It surfaced only because an exact-cosine control failed with
`Expected array<number> but found NONE`; nothing in the write path ever
reported an error. SurrealKV is documented as beta.

## Can OXIDE reasonably adapt watch + CLI?

This is the question the retest was really for, and it deserves a straight
answer rather than a scorecard.

**The shape of the problem.** OXIDE has nine subcommands. Two are long-lived
(`watch`, `mcp`); seven are one-shot processes that open the store and exit
(`index`, `status`, `search`, `review`, `stats`, `context`, `eval`). Since no
embedded engine allows two processes on one store, adopting SurrealDB means
exactly one of:

1. **No daemon.** Each one-shot command opens the store exclusively and exits.
   This works — but it breaks the moment `oxide watch` is running, which is a
   shipped feature whose entire point is to be running. And it costs 0.29–0.62 s
   per invocation.
2. **A daemon owns the store**, and the seven one-shot commands become IPC
   clients.

**Option 2 is not absurd.** Plenty of good tools work this way — `rust-analyzer`,
`gopls`, `sccache` — and OXIDE already ships a long-lived server in `oxide mcp`.
The `std::thread::scope` invariant in `AGENTS.md` exists precisely because the
CLI has no tokio runtime; a socket client arguably *simplifies* that rather than
complicating it.

**But the cost lands in the wrong places.** A daemon requirement converts OXIDE
from "a binary you can run anywhere" into "a service that must be running
first". That lands directly on the things OXIDE is for: `scripts/agent_eval`
spawns the binary headlessly per task, `oxide eval` runs it repeatedly, the
bundled Skill tells a coding agent to just run `oxide context`, and CI or a
fresh container has no daemon. Each of those now needs lifecycle handling —
auto-spawn, staleness, version skew between a running daemon and a rebuilt
binary.

**And the payoff is negative.** After paying for all that, the warm per-query
cost is still 7–12× worse and widening, on a tool whose entire purpose is
answering retrieval queries fast. The one clear win — concurrent write
throughput — is for a workload OXIDE does not currently have, because its
indexer is a single sequential writer.

So: **adaptable, but not worth adapting.** The compromise is not architecturally
ugly; it is simply a large amount of new machinery bought at a latency loss.

## Verdict after the retest

**Still do not adopt — but the first round's stated reasons were mostly wrong,
and the real reason is narrower and more honest.**

SurrealDB is not disqualified on capability. Under its documented model it
passes every correctness gate on both engines: persistence, atomic replacement,
freshness, graph parity over ten probes, exact BM25 over five terms, exact
literal matching over five substrings, 10/10 vector recall, determinism. It
builds in five and a half minutes with no C++ compile, and it beats SQLite on
concurrent writes.

It is disqualified on **fit**: it costs a daemon that OXIDE's one-shot CLI and
headless-eval workflows would have to be rebuilt around, and after that it is
still 7–12× slower per query than the SQLite path that needs no changes at all.
The SurrealKV lost-write finding is a separate, serious reason not to use the
engine the docs recommend for embedded deployments, at least at this scale and
until that is understood.

Scope: SurrealDB 3.2.4, `kv-rocksdb` and `kv-surrealkv`, on this machine and
this synthetic corpus. Nothing here is a claim about SurrealDB in server mode,
where the process model is completely different and the per-query costs would be
paid over a connection rather than a process start.
