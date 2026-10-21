# TOON as an agent-facing serialization: results (2026-10-21)

**Verdict: REJECT**, by the rule frozen in [preregistration.md](preregistration.md)
(sha256 `26fca544…7095`, recorded in `$DATA/prereg.sha256` before any case was built).
OXIDE keeps its current output. MCP `mode=search` stays compact JSON,
`mode=context` stays summary + raw payload, and CLI `--json` stays pretty JSON.
`toon-format` does not become a dependency, and no integration is proposed.

The deciding fact is simple and measured: **on every OXIDE output shape, TOON is
larger than the compact JSON that MCP already emits.** TOON's published savings are
measured against pretty JSON, and on flat, uniform tables. OXIDE's results are neither.

Everything below is measured unless marked *inferred*.

## Setup

- **Model-facing text today** (read in code, then measured): `mode=search` text is
  FastMCP's `pydantic` `dump_json` of `SearchResult`. It is **byte-identical** to
  `json.dumps(asdict(r), separators=(",", ":"), ensure_ascii=False)` on all 26 search
  outputs (arm C). The `mode=context` text is a summary line + raw `payload`, and the
  metadata goes in `structured_content` only. CLI `--json` is `indent=2` (arm P).
- **TOON:** `toon-format` 1.2.0 (spec 4.4), loaded in an ephemeral `uv run --with`
  overlay. `pyproject.toml` and `uv.lock` are unchanged.
- **Outputs:** 12 real dev tasks (requests ×6, seaborn, flask, code-server ×2,
  darkreader ×2; issue text as query; production retrieval). Each yields `search20`,
  `search100`, `files`, `relations` and a `bundle` at 2,048 units. A synthetic edge
  repository adds Unicode and space paths (`src/naïve café.py`, `src/données/模块.py`,
  `web/ünï/コード.ts`) and source with quotes, backslashes, tabs, `- `, `#` and
  template literals. It contributes a hinted search, an empty search, files, relations
  and a bundle. Two searches are cut textually mid-list. That makes 67 outputs, 344
  questions with gold computed from the structured value, and 12 navigation tasks.
- **Agent:** OpenCode 2.0.32 with `openai/gpt-6-luna`: no tools, isolated config, one
  turn, and the format never named. 2 repetitions, **500 graded calls**. Provider
  input tokens come from the session export.

## Size (deterministic; savings = 1 − X/C, so negative means larger than today)

| output (n) | C tokens (median, o200k) | **T vs C** | T-tab vs C | P vs C | T vs P | T vs C cl100k | T vs C units |
|---|---|---|---|---|---|---|---|
| search20 (12) | 3,190 | **−11.5 %** | −11.4 % | −32.2 % | +15.6 % | −13.3 % | +19.4 % |
| search100 (12) | 14,082 | **−12.5 %** | −12.2 % | −34.9 % | +16.5 % | −14.5 % | +20.8 % |
| files (12) | 5,701 | **−11.8 %** | −11.9 % | −30.1 % | +14.2 % | −10.3 % | +22.8 % |
| relations (12) | 2,173 | **−14.4 %** | −14.4 % | −37.6 % | +16.8 % | −13.8 % | +23.2 % |
| bundle, full (12) | 5,161 | **−2.3 %** | −3.7 % | −30.2 % | +21.8 % | −3.0 % | +19.2 % |
| bundle envelope only (13) | – | **−2.5 %** | −3.1 %¹ | – | – | – | – |
| edge search / empty | 1,444 / 192 | −11.6 % / −3.6 % | | | | | |
| truncated (2) | 3,873 | −11.8 % | | | | | |

¹ ETtab vs EC. Bytes follow the same sign for search, files and relations (−5 % to −13 %).

- **Why TOON is larger.** None of OXIDE's arrays qualify for TOON's tabular form
  (spec §9.3). Search entries carry a `seeds` array and a nullable nested `source`.
  `FileInfo.diagnostics` is an array. Relations have nullable `target`/`evidence`.
  So TOON falls back to list form, which costs indentation on every nested line plus
  quotes on every `sha256:…`, `symbol:…` and `file:…` value (they contain `:`).
  Compact JSON pays none of that. The only tabular rows produced were the per-item
  `entities[n]{key,name,complete,reason}` inside bundles. The tab delimiter changes
  nothing (no tables to delimit).
- **Against pretty JSON (CLI `--json`), TOON saves 14–22 %**, consistent with upstream's
  −32.7 % vs pretty on mixed data and its +1.6 % vs compact. Upstream's own semi-uniform
  logs were +19.9 % vs compact, the closest analogue to OXIDE here.
- **OXIDE's own units are misleading here.** `oxide-units-v1` scores TOON **19–24 %
  smaller**, because it charges punctuation that real tokenizers merge. Both real
  tokenizers, and the agent model's provider counts, say the opposite. Any future
  format study must use real tokenizers, not units.
- **Provider-reported prompt tokens** (the agent model's own tokenizer, paired by
  case and rep): T vs C has a median **+10.5 % more** (search +10.7 %), and HT vs HC
  +1.6 %. Median prompt tokens: C 4,170, T 4,539, P 4,764.

## Comprehension and task success (500 calls)

| accuracy | C | T | P | HC | HT |
|---|---|---|---|---|---|
| search20 | 0.950 | 0.942 | 0.942 | | |
| search100 | 0.817 | 0.775 | 0.783 | | |
| files | 0.917 | **0.983** | 0.867 | | |
| relations | 0.900 | 0.892 | 0.958 | | |
| bundle | 0.771 | 0.715 | | 0.625 | 0.569 |
| edge search / empty / files / relations / bundle | 0.70 / 1 / 1 / 1 / 1.00 | 0.90 / 1 / 1 / 1 / 0.92 | 1.00 / 1 / 1 / 0.80 / – | – / 0.92 | – / 0.83 |
| truncated | 0.667 | **1.000** | 1.000 | | |
| navigation hit / gold-file recall | 0.958 / 0.729 | 0.917 / 0.660 | 0.917 / 0.688 | | |

- **Gating pool** (searches T vs C + bundle envelope HT vs HC, 39 cases): T 0.762 vs
  C 0.793, a difference of **−0.031**, 95 % CI [−0.094, +0.029]. That misses
  non-inferiority (A1). The bundle kinds are worst (−0.056 and −0.083).
- **Copying source lines** in bundles, correct out of 26: C 18, T 11, HC 9, HT 6.
  Escaped source inside a JSON or TOON string is hard to read line by line in both.
  The hybrid arms, which have raw payload but metadata in a separate envelope, are
  worst at mapping line numbers.
- **Truncation is TOON's one clear win.** On the two cut searches, every arm counted
  the complete entries correctly (50 and 5), and **no reply repeated the stale declared
  `[N]`**. Asked whether the output was cut off, compact JSON said *no* in 4/4 calls,
  while TOON and pretty JSON said *yes* in 4/4. Strict `toon_format.decode` also
  rejects a cut text with a precise message (`Declared length 100 but found 50`),
  where `json.loads` only reports a position.
- **Files** improved (+0.067). This is the one kind where list form reads like a
  record list. It is not gating, and n = 12.
- **Validity:** 7 replies (bundle arms only: HC 2, HT 3, T 2) were JSON with an extra
  closing `}`. They score 0 as preregistered, so V fails for HC/HT. A lenient re-parse
  (not gating) gives bundles C 0.788, T 0.782, HC 0.718, HT 0.654. Full TOON then
  matches compact JSON, and the hybrid envelope stays worst. No call failed or used
  a tool.
- **Latency:** median seconds per call: C 9.26, T 9.24, P 9.30, HC 10.78, HT 10.02.
  There is no format effect beyond prompt size. Cost through the subscription was
  $0.

## Runtime and implementation

- **Conformance:** **564/564** official fixtures pass (`toon-format/spec` at
  `d09c3d33`). `decode(encode(v)) == v` holds for all 65 OXIDE outputs, the tab
  variant and the bundle envelopes, including Unicode paths and every special
  character.
- **Encode cost:** search20 p95 ≤ 1.49 ms (json 0.09 ms, about 10× slower).
  search100/files reach a 6.05 ms p95.
- **Package:** MIT, Python ≥ 3.10, no runtime dependencies (pydantic is an extra),
  8 files, 55 KB. Its import takes **55 ms** cold (`json` takes 5 ms), almost all of it
  `importlib.metadata` for the version string. That is negligible inside the MCP
  server, which already loads it (*inferred*).
- **Limitations:**
  - There is a single model. Upstream shows per-model swings of several points.
  - There are 12 real tasks.
  - Bundle questions asked 6 templates, not the preregistered "up to 5". The templates
    themselves were preregistered. Disclosed.
  - The format is unnamed in the prompt. An opt-in integration would likely name it in
    the tool description (*inferred*: that would add tokens).

## Criteria

| | measured | needed | |
|---|---|---|---|
| S1 savings | search20 **−11.5 %**, envelope **−2.5 %** | ≥ +10 % each | **fail** |
| S2 provider tokens | **−10.5 %** (T uses more) | ≥ +5 % | **fail** |
| A1 comprehension | −0.031, CI [−0.094, 0.029] | ≥ −0.02, CI lo ≥ −0.05 | fail |
| A2 no broken kind | bundle −0.056, edge bundle −0.083 | ≥ −0.05 | fail |
| A3 navigation | hit −0.042, recall −0.069 | ≥ −0.05 each | fail |
| A4 truncation | +0.333; stale `[N]` reported 0/4 | ≥ −0.10 | pass |
| R1 interoperability | 564/564, round trip 100 % | 100 % | pass |
| R2 runtime | p95 1.49 ms, no runtime deps | ≤ 5 ms | pass |
| V validity | HC 7.7 %, HT 11.5 % | ≤ 5 % | fail |

REJECT on S1 alone, and independently on A2 and A3.

## Recommendation

TOON does not belong in OXIDE's agent-facing output.

- **It would make the default MCP output more expensive,** because OXIDE already
  serializes compactly. TOON is only cheaper than pretty JSON, which no MCP client
  receives.
- **The one real benefit is truncation detection.** That comes from TOON's declared
  `[N]` counts, not its syntax. If that ever matters, it is cheaper to address
  directly in JSON, e.g. a count field (*inferred*, not tested). It is not proposed
  here, because MCP results are bounded by `limit`/`budget` and are not truncated by
  OXIDE.
- **Don't revisit** unless OXIDE grows genuinely flat, uniform, large tabular outputs
  (no nested or nullable objects, no arrays per row), and then only measured with real
  tokenizers against compact JSON.

## Reproduce

```bash
D=~/Projects/oxide-eval-data/oxide-toon-eval   # spec clone, edge repo, private store, runs
git clone https://github.com/toon-format/spec $D/spec && git -C $D/spec checkout d09c3d33
W="uv run --with toon-format==1.2.0 --with tiktoken python docs/phase-6/toon-serialization/scripts"
$W/build.py edge && $W/build.py cases          # 67 outputs + 12 navigation cases
$W/measure.py conformance && $W/measure.py package && $W/measure.py sizes
OPENCODE_BIN=$(command -v opencode) python3 docs/phase-6/toon-serialization/scripts/agent.py run r1 4   # then r2
python3 docs/phase-6/toon-serialization/scripts/agent.py grade && python3 docs/phase-6/toon-serialization/scripts/analyze.py
```

Run heavy steps under `systemd-run --user --scope -p MemoryMax=3G -p CPUQuota=200%`.
`results/` holds `sizes.json`, `agent.json`, `conformance.json`, `package.json` and
`report.json`. Raw replies are in `$D/runs/`.
