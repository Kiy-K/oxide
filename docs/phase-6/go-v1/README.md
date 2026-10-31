# Go source support milestone evidence

Implemented semantics and limits: [../../go.md](../../go.md). Go adds a language
slice to the existing engine; retrieval, routing, packing and the public API are
unchanged. The implementation does not run the Go toolchain or repository code.

## Grammar and fixture conformance

Selected **tree-sitter-go 0.25.0**, the latest release, with grammar ABI 15. The
locked **tree-sitter 0.26.0** accepts ABI 13–15. The grammar publishes `cp310-abi3`
wheels for Linux x86_64/aarch64, macOS x86_64/arm64 and Windows amd64/arm64. The
fixture and adversarial checks cover declarations, package and import resolution,
tests, build-tag ambiguity, conservative local binding, cache validation, malformed
and deep input, and incremental/full-rebuild parity. `tests/python/test_go.py` passes
all 15 tests, including persisted incremental parity and deterministic import order.

## Pinned real repositories

Both checkouts were evaluated without running their code. All captured Go files parsed
completely. Symbol counts below are Go-only; entities/relations include all captured
files. Resolution counts are Go-derived edges.

| Repository (commit) | Go files | Symbols by kind | Import edges: resolved / ambiguous / unresolved | Calls: resolved / ambiguous / unresolved | Other ambiguous edges | Cold derive / reused derive | Cold index / unchanged / edit | Peak RSS |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gin (`0f09c3a9b462`) | 99/99 | 912 function, 483 method, 125 struct, 20 interface, 101 constant, 99 variable, 35 type | 28 / 0 / 493 | 1,821 / 51 / 6,578 | 2 references | 0.185 / 0.043 s | 0.578 / 0.010 / 0.380 s | 256 MiB |
| Prometheus (`df0d3cc5bde0`, 5 go.mod files) | 746/746 | 5,000 function, 5,604 method, 1,309 struct, 173 interface, 1,696 constant, 600 variable, 191 type | 2,739 / 9 / 6,742 | 22,894 / 4,275 / 91,607 | 294 references | 3.254 / 1.013 s | 6.869 / 0.119 / 4.313 s | 963 MiB |

Import ambiguity is 0/521 edges for gin and 9/9,490 (0.095%) for Prometheus. The
two ambiguous gin references and 294 Prometheus references represent competing
declarations, including files not separated by build constraints. `real/*.json`
retains derivation and persisted edit/full-rebuild equality, counts, and timings.
The separate three-iteration `bench.py` reports cold-index medians of 0.592 s (gin)
and 6.578 s (Prometheus), unchanged-index medians of 0.010 s and 0.123 s, and peak
RSS medians of 154.7 MiB and 393.4 MiB. Warm context medians were 0.045 s and
0.086 s for the requested queries (`ServeHTTP`/`Bind`, `Appender`/`QueryRange`).
These laptop measurements are diagnostic, not a performance gate.

Ten resolved cross-file calls and ten resolved imports per repository were inspected
against the pinned sources. All 40 samples' source expressions and target signatures
(calls) or import paths/modules matched; no contradiction was found. This bounded
spot check is not a precision estimate or compiler oracle. Import ambiguity was rare
in this sample, but call ambiguity is substantial in Prometheus; build constraints,
platform variants, type information, and external module source remain unmodeled.

## go/types oracle (gin)

After the milestone commit, OXIDE's gin edges were scored against `go/types`
(`golang.org/x/tools/go/packages`, `Tests: true`, default `GOOS=linux GOARCH=amd64`
build, Go 1.27.3). The oracle only type-checks gin; it does not run gin's code. Each
Go-derived `calls`/`references` edge is matched to the oracle's `Uses` entry at its
named identifier: the last selector of qualified evidence (`pkg.F`, `x.M`, `&pkg.T{`)
or the receiver type of a receiver reference. An edge agrees when the oracle's
definition lies inside a target symbol.

| Measured (gin `0f09c3a9b462`) | Count |
| --- | ---: |
| Resolved edges: agree / disagree | 3,019 / 0 (precision 1.0) |
| Resolved edges agreeing on a qualified (`pkg.F`, `x.M`) form | 391 |
| Resolved edges with no oracle use: files outside the default build (build tags, `testdata/`) | 63 |
| Ambiguous edges whose oracle definition is one of the candidates | 52 |
| Unresolved calls whose oracle definition is a gin symbol's name | 1,618 |

The 1,618 recall gaps are 1,568 method calls on something other than the receiver
(including promoted methods and interface dispatch) and 50 calls of function values
held in package-level variables. Both are listed under "Not modeled" in
[go.md](../../go.md), and none falls outside that list. Precision covers only
resolved edges in the default linux/amd64 build. Build-tag variants, other platforms
and Prometheus were not scored. A first scoring pass reported 0.904. Its 252
disagreements were all scorer errors: it looked up the qualifier (`pkg` in
`pkg.F`), not the selected name. Program, scorer and report are in
[oracle/](oracle/report.md).

## ContextBench diagnostic

Both arms used the same 18 selected rows (`go-rows.json`, SHA-256
`3fdaeb33f815c3da07cd1100762fcb1e1c48dbbc3f1e35f1db3187cd650ecafd`), the same
corpora, gold, and scorer. Before ran the installed previous engine in isolation;
after ran this checkout. Verified gold spans only are scored; the audit classes were
246 exact, 5 contained, 74 path-only, 17 range, 7 drift, and 4 unresolved (the last
three classes are unverified and excluded from verified metrics).

| Verified macro metric (18 tasks, 325 spans, 9,077 lines) | Before | After |
| --- | ---: | ---: |
| Entry recall@20 | 0.187655 | 0.128309 |
| Routed coverage | 0.187655 | 0.357206 |
| Complete span recall@1,024 | 0 | 0.020373 |
| Complete span recall@4,096 | 0 | 0.047976 |
| Line recall@4,096 | 0 | 0.076386 |

The lower entry recall@20 is retained as observed; no parameters were tuned. Median
index time across the 18 tasks was 0.132 s before and 1.471 s after; peak process RSS
was 162.6 MiB and 385.8 MiB. This is a small diagnostic (n=18), not a gate, broad
quality claim, or reason to tune retrieval. Raw scores, audits, corpus identities,
timings and integrity records are in `context-before/` and `context-after/`.

## Successor baselines, limitations and verification

`baseline-v10` and `typescript-v7` are identity-only successors for the derivation
identity change. Results otherwise match their predecessors, metrics are byte-identical,
and each manifest corpus record changed only in its derivation hash. The Go limitations
are documented in [../../go.md](../../go.md). The `go/types` oracle covers gin only (above). Windows
and macOS Go runtime and artifact checks remain pending hosted CI; wheel availability
does not establish runtime support. The platform matrix and existing LadybugDB limits
still apply.

Reproduce from a locked checkout, under the resource caps recorded in the evaluation
logs:

```sh
mise run verify
uv run --locked python scripts/eval_go.py real /path/to/gin docs/phase-6/go-v1/real/gin.json
uv run --locked python scripts/eval_go.py audit /path/to/gin docs/phase-6/go-v1/real/gin-audit.json
uv run --locked python scripts/bench.py /path/to/gin --iterations 3 --warm 3 --query ServeHTTP --query Bind
uv run --locked python scripts/eval_go.py context /path/to/go-rows.json /path/to/corpora docs/phase-6/go-v1/context-after
# go/types oracle: needs a Go 1.27 toolchain; writes gin-uses.json, then gin-oracle.json and report.md
cp docs/phase-6/go-v1/oracle/* /path/to/work/ && (cd /path/to/work && go run . /path/to/gin gin-uses.json)
uv run --locked python docs/phase-6/go-v1/oracle/score.py /path/to/work /path/to/gin
```

The previous-engine ContextBench arm requires its recorded isolated environment and
the same rows and corpora. Distribution smoke follows
[platforms.md](../../platforms.md). Next language priority: Rust, then reassess Java
or C# against a scoped fixture and resolution oracle.
