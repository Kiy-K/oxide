# Java source support milestone evidence

Implemented semantics and limits: [../../java.md](../../java.md). Java adds a language
slice to the existing engine; retrieval, routing, packing and the public API are
unchanged. The implementation does not run a JDK, a build tool or repository code.

Status: **implemented and evaluated** (2026-10-29, HEAD `3492b38` plus the uncommitted Java
change, including the declaration-header scope fixes in [Fix and re-measurement](#fix-and-re-measurement), the
bounds/`permits` part on 2026-10-30), following `~/Projects/oxide-eval-data/oxide-java-eval/BRIEF-evals.md` steps 0–6. Every number below is
measured unless marked as inferred. The brief's evaluator rules held throughout:
- engine and tests unchanged during the runs;
- the shared checkouts were only read;
- every heavy command ran under the 200% CPU / 3 GB caps.

The brief's steps are all scripts (no agent tasks), so they were run directly rather than through an OpenCode
session. A backup was taken before the runs: a tracked patch plus a tarball of untracked files, in `oxide-java-eval/backup/`.

## Grammar and fixture conformance

Selected **tree-sitter-java 0.23.5**, the latest PyPI release, grammar ABI 14; the locked
**tree-sitter 0.26.0** accepts ABI 13–15. The grammar publishes `cp39-abi3` wheels for
Linux x86_64/aarch64 (glibc) and x86_64 (musl), macOS x86_64/arm64 and Windows amd64/arm64.

`tests/python/test_java.py` passes all 9 tests:
- identity, kinds and byte provenance;
- package, type, member and import resolution on `fixtures/java_repo`;
- conservative scoping: variables obscuring types, type parameters, lambdas, local and anonymous classes, external
  supertypes, implicit members, arity;
- type-lookup precedence, including declaration headers (annotations, type-parameter bounds, supertypes and
  `permits` resolve in the enclosing scope; record components do not);
- cache validation, malformed and deep input, and unsound cached facts;
- persisted incremental/full-rebuild parity.

`mise run verify`: **403 passed, 7 skipped**, lint and mypy `--strict` clean
([verification/mise-verify.log](verification/mise-verify.log)).

## Successor baselines

[`baseline-v15`](../baseline-v15/README.md) and [`typescript-v12`](../typescript-v12/README.md) are identity-only
successors for the derivation id change: `metrics.json` is byte-identical, and the only other differences are
derivation ids, `oxide_source` and the hand-set v15 labels. The field-normalized comparison found 0 other
differences ([verification/baselines.md](verification/baselines.md)). The TypeScript gate
(`OXIDE_CONTEXTBENCH_TS=1 pytest tests/python/test_contextbench_ts.py`) passed 17/17.

## Pinned real repositories

Local ContextBench checkouts: gson `854c8255b625cf1e13c701a83ea9ccb4caaa576a` and mockito
`116a4d6231dc35f90543060af41dcde00fe4e2ae`. Every Java file parsed completely. Edge counts are Java-derived.

| Repository | Java files | Symbols by kind | Calls resolved / ambiguous / unresolved | References resolved / unresolved | Imports resolved / ambiguous / unresolved | Cold / reused derive | Cold index / unchanged / edit | Peak RSS |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gson | 264/264 | 2,823 method, 647 class, 339 constructor, 27 interface, 26 enum, 13 record, 8 annotation | 7,879 / 2,078 / 7,924 | 5,710 / 11,851 | 1,085 / 0 / 1,589 | 0.618 / 0.313 s | 1.588 / 0.028 / 1.237 s | 445 MiB |
| mockito | 1,004/1,004 | 6,282 method, 1,536 class, 495 constructor, 271 interface, 18 enum, 18 annotation | 12,159 / 2,610 / 10,318 | 8,614 / 16,913 | 3,653 / 324 / 3,384 | 1.066 / 0.533 s | 2.828 / 0.103 / 2.359 s | 664 MiB |

On both repositories, derive parity (reused facts vs cold) and persisted edit-vs-full-rebuild parity hold
([real/gson.json](real/gson.json), [real/mockito.json](real/mockito.json)).

Three-iteration `bench.py` medians, gson / mockito (measured before the fix, not re-run; the fix changes one gson edge):
- cold index: 1.641 / 2.882 s;
- unchanged index: 0.027 / 0.092 s;
- warm search: 0.005 / 0.006 s;
- warm context: 0.075 / 0.112 s;
- peak RSS: 217 / 274 MiB.

The queries were `toJson`/`TypeAdapter` for gson and `mock`/`verify` for mockito. These laptop numbers are
diagnostic, not a gate.

Spot check ([real/spotcheck.md](real/spotcheck.md)): **40/40** SHA-sampled resolved cross-file calls and imports
are what javac would bind, with no contradiction. The samples are mostly local-typed receivers, static imports and
constructors, so this check only bounds gross errors.

A diagnostic derive-only run before the final anonymous-class field fix also parsed fastjson2 (`017e0888`): 4,316
files in 8.08 s at 406 MiB. Its counts predate the fix and are not used above.

## javac oracle (gson)

Full report: [oracle/report.md](oracle/report.md); our own `Oracle.java` and `score.py`. The oracle used
Temurin `javac 25.0.5`, parse + analyze only, with no classpath, so dependencies count as no-oracle. Each of the
7 Maven modules was compiled separately.

| Kind | Agree | Disagree | No oracle | Precision |
| --- | ---: | ---: | ---: | ---: |
| calls | 7,876 | 0 | 3 | **1.0000** |
| references | 5,710 | 0 | 0 | **1.0000** |
| imports | 1,085 | 0 | 0 | **1.0000** |
| overall | 14,671 | 0 | 3 | **1.0000** |

These are the counts after the fix. Before it, references were 5,709 / 1 and overall 14,670 / 1 (0.9999).

Every agreement also passes the strict check: the innermost OXIDE symbol containing javac's declaration is the
target. A mutation check confirms the scorer can tell targets apart: it rejects 300/300 resolved calls whose
target was swapped.

- **Ambiguous calls:** all 2,078 contain javac's declaration among their candidates.
- **Unresolved uses that javac binds in-repo:** 1,079. Of these, 874 are type-parameter references, which have no
  entity by design. The rest are mostly calls from anonymous or local classes and local records, which are
  conservative stops listed in `docs/java.md`.

**The one pre-fix disagreement was a real OXIDE error, now fixed.** The class annotation `@JsonAdapter` on
`class A` (which declares a member class `A.JsonAdapter`) resolved to the member class. javac resolves the
annotations of a class declaration in the enclosing scope, so the import wins. See
[Fix and re-measurement](#fix-and-re-measurement).

**First pass vs final.** The first scoring pass had one scorer error class: explicit type-argument calls
(`gson.<Car>fromJson`) were queried at the receiver, which made 8 ambiguous edges look like none-of-candidates.
After the fix, the precision numbers are unchanged and the ambiguous-in-none count drops from 8 to 0.

## ContextBench Java diagnostic (before/after)

Rows: [java-rows.json](java-rows.json), SHA-256 `4041983b1e94233a2425963fadd0abcd39f13c36a0401d03998309776bf65430`.
The fixed `select` rule picked 16 rows from 6 repositories:
- fastjson2 3, logstash 3, jackson-core 3, mockito 3;
- gson 2, jib 2.

All 16 were fetched (shallow `git archive` projections of `.java` and build files). Both arms used the same rows,
scorer and gold audit (`gold-audit.json` is byte-identical): 37 exact, 50 path and 10 unresolved spans. BEFORE is
the engine at `3492b38` in a worktree with its own venv (the worktree is now removed); AFTER is this checkout.
Macro metrics, verified gold (16 tasks, 87 spans):

| Arm | entry recall@20 | routed coverage | span recall@1,024 | span recall@4,096 | line recall@4,096 | file recall@4,096 | Median index | Peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| before | 0.2147 | 0.2147 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.35 s | 208 MiB |
| after | **0.3101** | **0.7592** | 0.0026 | 0.0260 | 0.0940 | 0.5469 | 1.79 s | 813 MiB |

The AFTER arm was re-run after the fix. Its `metrics.json`, `results.json`, `manifest.json` and gold audit are
byte-identical to the pre-fix run; the AFTER timings in the table are from the re-run.

Before Java support, `.java` files yield no symbols, so no context unit reaches the budget: every span and line
recall is 0. With Java, routing reaches 76% of gold, but complete-span recall stays low (2.6% at 4,096 tokens).

Inferred, not measured: the gold spans are large method or region edits in big files, so budget-limited packing
cuts them. The stage counts at 4,096 support this: 22 spans lost at budget, 20 at selection, 33 at entry, 9 at
routing, 3 complete.

This is a 16-task diagnostic with no tuning: the gain is in discovery and routing, not end-to-end span
completeness. Outputs are in [context-before/](context-before/) and [context-after/](context-after/); logs are in
[verification/](verification/).

## Distribution smoke (Linux)

Run before the fix and not repeated. The fix touches no packaging (inferred).

`uv build` produced `oxide-0.0.0-py3-none-any.whl` (SHA-256 `bbe0699a…`) and `oxide-0.0.0.tar.gz`. Each was
installed into its own fresh venv with the locked runtime requirements, then `oxide prepare`, then
`python -I scripts/smoke_dist.py fixtures`. That smoke includes the Java search (`Store.mustGet` first) and the
context check (`static Store open(Mode mode)`).

Both artifacts reported **smoke: OK**: 65 files, 319 entities, search/context, MCP stdio and the store lock
([distribution-wheel.log](verification/distribution-wheel.log),
[distribution-sdist.log](verification/distribution-sdist.log)). Afterwards the repo's `oxide` was still the
editable install.

## Independent re-run (OpenCode GPT-6 Luna)

The same brief, with outputs redirected, was executed end to end by OpenCode (`openai/gpt-6-luna`, `--auto`), which
wrote its own comparison script, oracle and scorer. Outputs are in [../java-v1-opencode/](../java-v1-opencode/).

The first attempt stopped at step 1 on a crash in its own comparison script. It was relaunched with notes on the
file shapes and on what counts as a failed check.

All of its deterministic outputs equal this run's:
- `real/*.json`: `graph_sha256` and resolution counts;
- the audit samples;
- `java-rows.json`;
- both arms' ContextBench `metrics.json` and `results.json`.

Its steps 1, 2, 3, 5 and 6 pass with the same numbers; its spot check is 40/40.

Before the fix, its independent javac oracle found the **same single disagreement**: the `@JsonAdapter` class annotation above.
It reported overall precision 14,477 / 14,478 (calls 7,830/7,830, references 5,605/5,606, imports 1,042/1,042).
Three things differ from this run, all in the oracle (measured):
- **Fewer scored edges:** it records fewer javac use positions (19,593), and its no-oracle count is 196.
- **One fewer module:** it skipped `test-graal-native-image`.
- **Scorer errors:** its first pass had 53 scorer errors in two classes (constructor elements and qualified/generic
  type identifiers), which it fixed.

It left the unresolved-cause classification as `other`.

**Its discrepancies are all in its oracle, not in OXIDE (measured).** Its own `score.py`, re-run with dumps of the
edges it could not score ([verification/opencode-score-dump.py](verification/opencode-score-dump.py),
[.log](verification/opencode-score-dump.log)), shows:
- **The 47 "ambiguous in none" are unscored, not wrong.** None of the 47 has a javac record at its position; its
  scorer counts a missing record as a miss. 8 are explicit type-argument calls (`gson.<Car>fromJson`): it cuts the
  evidence at the first `<` and queries the receiver `gson`, which is the error this run's first pass had. The other
  39 are calls in `test-graal-native-image`.
- **That module was never compiled.** Its `Oracle.java` takes as modules only the directories with both a `pom.xml`
  and a `src/main/java`, and `test-graal-native-image` has tests only.
- **Its 196 no-oracle resolved edges:**
  - 166 are in that module;
  - 24 are static-import members, which javac leaves unattributed (this run's oracle resolves them among the
    owner's members);
  - 3 are `GsonBuildConfig` imports, because its sourcepath omits `src/main/java-templates`;
  - 3 are `assertThrowsStackOverflow` calls, which javac cannot attribute in either run.
- These 193 extra unscored edges are exactly the agreement gap: 14,670 − 14,477 = 193.

On the fixed code, its scorer gives 14,478 / 14,478 with 0 disagreements.

## Fix and re-measurement

The declaration-header scope fix (`java.py` `_Facts._open`, `java_resolve.py` `_emit`) has two parts:
- A declaration's scope starts after its modifiers, so its annotations are looked up in the enclosing scope. The
  resolver still attributes them to the declaration.
- References inside a class's `extends`/`implements` types resolve in the enclosing scope, except the class's own
  type parameters.

Neither changes the facts encoding or the component versions. The Java slice is unreleased, so `oxide-java-v1`,
the derivation id and the successor baselines stand. Regression coverage is in `test_type_lookup_precedence`:
`@Tag class A { @Tag void m() {} @interface Tag {} }`, and `class C<T> extends B<T> { static class B<U> {} }`.
`mise run verify`: 403 passed, 7 skipped ([verification/mise-verify.log](verification/mise-verify.log)).

Measured effect ([verification/annotation-fix-diff.py](verification/annotation-fix-diff.py) derives each checkout
with this code and again with the pre-fix slice from the backup;
[log](verification/annotation-fix-diff.log)):
- **gson:** exactly one edge changes, the `@JsonAdapter` reference, which now targets the imported annotation and
  keeps its source. The resolution counts are identical. `graph_sha256` changes from `ce0b1861…` to `dbfd83d3…`.
- **mockito:** 0 of 78,231 edges change.
- **ContextBench corpora:** 0 edges change on 14 of the 16; the 2 gson corpora each change by that one edge.
- **Re-run outputs:**
  - `real/*.json` and both audits were re-run. The audit samples are byte-identical, so the 40/40 spot check
    stands.
  - The javac oracle was re-scored against the same `gson-uses.json`.
  - The ContextBench AFTER arm was re-run.
- **Not re-run:** `bench-*.json`, the distribution smoke and the ContextBench BEFORE arm (engine at `3492b38`,
  without the fix).

### Type-parameter bounds and `permits` (2026-10-30)

The fix above left one header gap: a class's type-parameter bounds and `permits` clause still saw its member types.
javac's rule was measured directly ([verification/header-scope-javac.log](verification/header-scope-javac.log),
[probe](verification/header-scope-javac.java); system OpenJDK `25.0.4.1` `jdk.compiler`, since the Temurin JDK
from the oracle run is gone and network fetches were not permitted):
- `class Bound<T extends Inner> { static class Inner {} }`: `cannot find symbol Inner`. With a top-level `Inner`
  it compiles, so the bound names the outer type.
- `sealed interface P permits Leaf { final class Leaf … }`: `cannot find symbol Leaf`. With a top-level `Leaf2`,
  `permits Leaf2` names it, and the member `Leaf2` is then "not listed in its 'permits' clause".
- `class SelfBound<T extends Comparable<T>>` with a top-level `T` compiles: the class's own type parameters are
  visible in its header.
- `record Record(Inner i) { record Inner() {} }` compiles: record components see member types, so they are not
  header.

The fix moves the rule to the parse: `java._Facts.type` records a type's header clause spans (`type_parameters`,
`superclass`, `super_interfaces`, `extends_interfaces`, `permits`), and `java.parse` files each reference inside
them in the enclosing scope unless it names one of the class's type parameters. The resolver's supertype-only
special case in `_emit` is removed; the declaration still owns those edges through the existing modifier-owner
lookup. The facts encoding and component versions are unchanged (Java is unreleased). Regression coverage in
`test_type_lookup_precedence`: `sealed interface D<T extends B<T>> permits L { class B<U> {} class L {} }` must
reference the top-level `e.B` and `e.L`, keep `T` unresolved, and `record R(B<?> b) { record B<V>() {} }` must
still reference `R.B`. With the parse-time move disabled, the test fails. To keep `java.py` within the 500-line
source limit, the facts' cache codec (`encode`, `decode`, `_sound`) moved unchanged to `java_facts.py`, as Rust's
is in `rust_facts.py`. `mise run verify`: 403 passed, 7 skipped
([verification/mise-verify-header-scope.log](verification/mise-verify-header-scope.log)).

Measured effect ([verification/header-scope-diff.log](verification/header-scope-diff.log), the same
`annotation-fix-diff.py` against the previous slice rebuilt by reversing this change; on a three-file fixture of the
case above it reports exactly the 2 expected edge changes):
- **gson, mockito:** 0 of 48,243 and 0 of 78,231 edges change.
- **ContextBench corpora:** 0 edges change on 13 of the 16, with the previous slice's edge counts identical to
  [annotation-fix-diff.log](verification/annotation-fix-diff.log). The 13 were rebuilt from the local
  `cb-repos` clones with the `eval_java.py` file filter. The 3 `fasterxml/jackson` corpora (jackson-core) were not
  re-measured: the corpora directory had been cleaned and no local clone holds those commits.
- **javac oracle:** `oracle/score.py` against the same `gson-uses.json` (SHA-256 `c9dd576a…`) gives output
  byte-identical to [oracle/gson-oracle.json](oracle/gson-oracle.json): 14,671 agree, 0 disagree.
- **OpenCode's independent oracle:** its `score.py` gives 14,478 / 14,478, 0 disagreements, unchanged.
- Since no gson, mockito or measured corpus edge changes, `real/*.json`, the audits, the spot check and the
  ContextBench arms stand without a re-run (inferred from the zero diff, not re-run).

Neither gson nor mockito exercises this case, so the oracles confirm only that nothing regressed; the javac probe
and the regression test are the evidence for the fix itself.

## Not run

- Windows and macOS runtime checks (hosted CI).
- A javac oracle on mockito (the brief scopes the oracle to gson).
