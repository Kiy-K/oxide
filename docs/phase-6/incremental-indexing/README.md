# Incremental indexing + indexing UX (2026-10-17)

`Repository.index()` reuses the parse facts of every Python file whose bytes the
current generation already parsed, and reports what changed. Resolution and
publication still cover the whole snapshot, so an incremental index is the same
generation a full rebuild produces, by construction and by test.

## Baseline: where a build spends its time (measured)

Large repo: 3,539 `.py` files (46 MB, site-packages), cold build 22.1 s, peak RSS
585 MB, generation 152 MB on disk; an unchanged re-index takes 0.43 s.

| Stage | Time | Share |
| --- | --- | --- |
| parse (tree-sitter + scope facts, per file) | 9.3 s | 42% |
| store load (`COPY` 3.3 s, `CREATE_FTS_INDEX` 3.0 s, CSV 1.3 s, FTS terms 1.0 s, commit 0.4 s) | 9.7 s | 44% |
| resolve (global, cross-file) | 2.5 s | 11% |
| capture / seal | 0.2 / 0.14 s | 1% |

Only parsing is a pure per-file function of the bytes. Everything else depends on
the whole snapshot.

## Design

- Each generation seals `facts.json`: `python.encode(parse(bytes))` per file
  digest, with the format (`oxide-python-facts-v1`) and its sha256 recorded in
  `MANIFEST.json`.
- `index()` opens the previous generation (only if it is compatible: same
  derivation components), reads its facts, and `derive(snapshot, repo_id, cached)`
  decodes cached files instead of parsing them. Resolution, validation and
  publication are unchanged.
- Facts are a cache, never knowledge. A missing, digest-mismatched, malformed or
  older-format `facts.json` yields no reuse and the next index parses everything
  again. `SCHEMA_VERSION` is unchanged, so older generations still open.
- The UX changes are in `IndexReport` (`previous`, `added`/`modified`/`deleted`,
  `parsed`/`reused`), an `index(progress=...)` callback, and `oxide index` stage
  lines on stderr plus a change/reuse/skip summary on stdout.

## Reliability: cached facts are untrusted input (review, 2026-10-17)

The seal (sha256 in the manifest) only catches accidental damage. A `facts.json`
whose digest matches but whose contents are wrong could come from an encoder bug
under an unchanged `FACTS_VERSION`, or from someone editing both files. As first
committed (`473c15e`), `decode` trusted such entries. Tests that rewrote one entry
and resealed it (`/tmp` reproduction, fixture repo) showed:

| Outcome | Cases |
| --- | --- |
| `index()` raised | wrong arity or container type (`ValueError`), parent index out of range (`IndexError`), non-string name, nested list in a name set (`TypeError`) |
| `index()` never returned | a scope as its own parent: a parent cycle, so resolution walks forever |
| `validate` failed the whole index | a name containing `.` or `~` collides with a generated key |
| silently wrong graph | string offsets, offsets beyond the file, a string spread into a set of characters |

A reused entry is resealed verbatim into the next generation, so a bad entry would
persist until its file changed, and every index in between would fail the same way.

The fix: `python.decode(value, size)` validates at the trust boundary. It accepts only
what `parse` can produce:
- JSON lists in the encoded shape, with exact `int`/`bool`/`str` types;
- the module scope as `(None, None, (0, size))`;
- every other scope's parent before the scope itself, which makes the scopes a tree
  and every walk finite;
- every offset inside the file;
- declaration names that are identifiers, so keys stay unique.

Anything else raises `ValueError`. `derive` then parses that file, so the bad entry is
replaced rather than carried over. `index()` reports the real `reused` count and
prints "N cached parse facts were unusable and parsed again". Every case in the table
now indexes successfully and equals a full rebuild.

- Coverage:
  - `test_unsound_cached_facts_are_parsed_never_trusted_or_fatal` replaces every
    position of a real entry, containers included, with each of nine bad values. A
    rejected entry must derive exactly what a full derivation does. An accepted one
    must still derive and validate.
  - `test_sealed_but_unsound_facts_are_parsed_again` plants a resealed cycle in a
    stored generation and checks the report, the progress line, rebuild equality and
    that the bad entry did not carry over.
  - With the check disabled, the fuzz test fails.
- Cost (measured, 3,539 site-packages files): no false rejections; decoding goes
  from 0.18 s to 0.41 s, about 2% of an incremental index.
- Limit (inherent): an entry that is well-typed, in bounds and tree-shaped but not
  what this file parses to cannot be detected without parsing it, which is the cost
  the cache exists to avoid. One example is a renamed or duplicated declaration.
  Producing one takes deliberate edits to both `facts.json` and the manifest inside
  the private data directory. That is the same trust `graph.lbug` already relies
  on. Recovery is to delete the store.

## Rejected: unsafe or unmeasurable optimizations

- **In-place or delta store updates.** A relation's `site` is a global
  derivation ordinal, so inserting one edge renumbers most later rows. A delta
  would rewrite most of the graph anyway, and it would give up the immutable,
  atomically published generations that crash recovery depends on.
- **Incremental FTS maintenance.** LadybugDB's FTS update behaviour is unknown
  (BOOTSTRAP: a blocked capability). Rebuilding is the only verified-correct
  option.
- **Caching per-file resolution.** Resolution reads other files' module and scope
  facts (imports, rebinding, `global`), so it isn't a function of the file
  alone. The parity steps below show the cases where it changes.

## Equivalence evidence

- `tests/python/test_incremental.py` runs nine steps, applied in sequence. Most of
  them change what unchanged files resolve to:
  - rename an imported symbol;
  - add the module an import names;
  - rebind names (a `global` rebind, and a def shadowed by an assignment);
  - import the rebound names and subclass;
  - delete a resolved module;
  - move a file without changing its bytes;
  - turn a test file into a non-test;
  - add partial and undecodable sources;
  - return to the first tree.
- After each step the incremental store is compared with a fresh full rebuild,
  over every entity, file, relation, `snapshot_id` and `derivation_id`. Core
  `derive` with cached facts is compared with `derive` without them in the same way.
- Mutation check: dropping `rebound`, `bound` or `bases` from the encoding fails
  these tests.
- Damaged, missing and unsealed `facts.json` reuse nothing and still equal a fresh
  rebuild.
- The frozen ContextBench baselines and every other gate are unchanged:
  `mise run verify` passes (149 tests after the review fix).

## Results (measured)

Method: `bench.py`. Each `index()` runs in a fresh interpreter under
`systemd-run` caps (3 GiB, 200% CPU). "Before" is HEAD `f579a34`.

Medium repo (pytest corpus, 245 files), median of 3:

| Scenario | Before | After |
| --- | --- | --- |
| cold build | 2.20 s (172 MB) | 2.46 s (179 MB) |
| unchanged | 0.08 s | 0.08 s |
| edit 1 file | 2.25 s | 1.56 s (203 MB) |
| edit the largest file | 2.11 s | 1.61 s |
| edit 10% of files | 2.08 s | 1.74 s |
| add 1 + delete 1 | 2.11 s | 1.70 s |

Large repo (3,539 files): two rounds, before and after interleaved, showing round
1 / round 2. A first sequential run is discarded: machine load shifted between
its halves, so even identical cold parses differed by 40%.

| Scenario | Before | After | After: major stages |
| --- | --- | --- | --- |
| cold build | 14.0 / 14.5 s, 585 MB | 15.3 / 16.1 s, 655 MB | + encode and write `facts.json` (17 MB) |
| unchanged | 0.24 / 0.25 s | 0.24 / 0.25 s | |
| edit 1 file | 14.6 / 14.5 s | 9.8 / 9.9 s, 679–709 MB | parse 0.0, decode 0.7, facts read 0.2, resolve 1.8, store 6.3 |
| edit the largest file | 14.1 / 14.5 s | 9.7 / 9.6 s | parse 0.07 |
| edit 10% of files | 14.3 / 15.1 s | 10.3 / 10.5 s, 719 MB | parse 0.55 |
| add 1 + delete 1 | 14.4 / 15.1 s | 9.6 / 9.8 s | |

In short, an incremental build is about 33% faster (about 30% on the medium repo)
and an unchanged index is as fast as before. The costs:
- a cold build is about 9% slower (encoding plus the facts file);
- peak RSS is 70–130 MB higher, from holding the decoded facts next to the derived graph;
- each generation carries a 17 MB `facts.json`, about 11% of its size.

## Do the cold-build and memory costs need action now? No (assessed 2026-10-17)

- **Cold build +9% (+1.2–1.5 s on 3,539 files).** This is encoding (0.77 s measured)
  plus writing and digesting a 17 MB file. That work is the price of reuse: a build
  without it can't be reused. It is paid in full only on the first index and after a
  version or component change, and every later edit index saves about 4.7 s.
- **Peak RSS +70–130 MB (585 → 655–720 MB).** That is 12–22%, and it comes from the
  encoded facts (and on an incremental index the previous generation's facts) held
  during derivation and publication. It stays well within the 3 GiB cap used for
  heavy runs, and the bound on what `index()` captures (1 GiB in scope) is unchanged.
  How much each holder contributes was not measured. Releasing the previous facts
  before publication is a possible saving that would need a measurement first.
- **Not adopted without measurements**, as scoped: compression (zlib-1 shrinks the
  file to 5.4 MB, but its time cost was not measured), any further caching layer, and
  incremental database mutation (unsafe, see below).
- **Revisit when** cold builds dominate real use (CI on fresh clones), or a
  repository's peak RSS approaches its memory budget.

## Limitations and follow-up

- Publication is still a full rebuild, and on the large repo it is about 65% of an
  incremental index (`COPY`, FTS index, CSV). Making it incremental needs stable
  per-file relation ordinals (a derivation-format change) and a verified FTS
  update path. Both are out of scope here.
- Resolution stays global (1.8 s on the large repo).
- `facts.json` is plain JSON. zlib level 1 would cut it to 5.4 MB, but the
  compression time was not measured on the critical path, so it wasn't adopted.
- MCP exposes no indexing tool (its calls are read-only by design), so the UX
  work covers the CLI and the Python API.
- Parallel parsing (BOOTSTRAP Remaining 2) now affects only cold builds and
  changed files.
