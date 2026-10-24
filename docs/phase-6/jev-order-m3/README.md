# ADR-0009 milestone M3: release-readiness validation (2026-10-24)

[ADR-0009](../../adr/0009-jev-assisted-context-ordering.md) § M3. This milestone covers
operational risk only. There is no new ranking experiment, no consumed corpus or task,
no quality claim, and no change to the policy, manifest or default. The quality evidence
is still Decision Studies v2 and v3. Numbers are measured unless marked *inferred*.

**Verdict: NOT READY to release yet.** The judge itself passed every preregistered live
criterion and the offline soak. Three integration defects were found and fixed. Two
blockers remain, and neither is in the judge's code:
1. **Fresh installs fail on three of four platforms.** The pinned FTS extension builds
   were replaced upstream.
2. **The provider's terms and retention are unsettled.**

Details are in § Remaining blockers.

## Defects found and fixed

| Defect | Evidence | Fix |
| --- | --- | --- |
| The transport child dropped `HTTP(S)_PROXY`, `NO_PROXY` and `SSL_CERT_FILE`/`SSL_CERT_DIR`, so on a network that needs a proxy every judged request fell back as `transport` | *Measured*: with `HTTP_PROXY` set, the child went direct and failed, and the proxy saw nothing | `jev.call` passes `*_proxy` variables and the two CA variables to the child. The key still travels on stdin only. Test: `test_the_request_goes_through_a_configured_proxy` |
| An MCP client could not tell from the text result that source had been sent. Fallbacks showed as `# degraded:`, a judged request showed nothing. The CLI already prints `judged by …` | Read from `mcp_server.py` | The text header adds `# judged by <identity>`. Test: `test_context_text_names_the_judge_when_source_was_sent` |
| The Windows judge test failed: Platforms CI on `0121d80`, `test_unreachable_is_a_transport_failure` got `deadline` instead of `transport` | *Measured* in CI. Windows reports a refused localhost connection only after about 2 s of retries, past the test's 1.0 s deadline. The request still fell back correctly | The test now drops an accepted connection, which is a transport failure at once on every OS: `test_a_dropped_connection_is_a_transport_failure`. The product is unchanged: an unreachable endpoint on Windows is reported as `deadline` |
| Windows: a per-request `python.exe` child under a windowless MCP host (a GUI app) can open a console window each time | *Inferred* (standard Windows behaviour). Not observed: there is no Windows desktop here | `creationflags=CREATE_NO_WINDOW` on Windows, `0` elsewhere |

The request, the manifest (still `sha256:d9ae934c…7b1d`), the policy and the default are
unchanged. `JudgeStatus.discloses` now states the verified policy facts below instead of
"not verified".

## Long-running MCP session, offline ([results/soak.json](results/soak.json))

`scripts/session.py soak` runs one real `oxide mcp` stdio process (`scripts/server.py`:
the CLI plus a request ledger) behind a FastMCP client. A localhost provider is switched
between OK and the eight failures it can show: 529, 429, a hang (5 s), a late answer
(2.5 s), garbage, the wrong model, a partial answer and a connection reset. The fault
cycle ran 5 times, each fault 5 calls followed by 5 OK calls. With warm-up and the consent
checks, that made **429 calls and 423 provider requests**.

| Check | Result |
| --- | --- |
| Outcome named correctly on every call | 429/429, **0 failures** |
| Every fallback equals the default bundle (spans and units) | 200/200 faults, plus every off and unsent call |
| Recovery | The first request after each fault block was judged (40/40 blocks). There is no state between requests |
| Hang / late provider, client wall time | p50 2.035 / 2.045 s, max 2.063 s |
| Judged call through MCP (local work, the fixture) | p50 0.074 s, max 0.298 s |
| Server RSS | 92.8 MB at start → 151.6 after 20 calls → 157.1 after 428. That is +5.5 MB over 408 calls, of it +0.3 MB over the last 320 |
| Threads / fds / child processes | 3 / 8 / 0 at every sample, including right after the 50 killed requests |

The session was offline and lasted minutes, not hours. *Inferred* from the flat samples:
there is no per-request growth, because each request's child process and thread end
with it.

**Consent while the session runs** (each check also counts provider requests):

| Change during the session | Result |
| --- | --- |
| A served repository that was never authorized | off, 0 sent |
| `oxide judge --disable` from another process | the next call is off, 0 sent |
| `--enable` again | the next call is judged |
| Record rewritten for another manifest | `not_authorized`, 0 sent |
| Record made unreadable | `not_authorized`, 0 sent |
| Source edited and `oxide index` re-run | consent kept, judged on the new snapshot |
| `--disable` while a judged request is in flight | refused with `ownership_conflict` (exit 2), because the request holds the store for at most about 2 s. A retry right after succeeds and the next call is off |
| Enabled under another data location only | off, 0 sent. Consent belongs to a data location |
| Server started without `TYPESAFE_API_KEY` | `no_credential`, 0 sent. The key comes from the server's own environment, so rotating it means restarting the server |

## Larger repositories, live ([preregistration.md](preregistration.md), [results/live.json](results/live.json))

The criteria and the budget were written down before any request. The maintainer then
authorized the corpus and spend. The run used one `oxide mcp` session over three public
open-source code bases, none of them in any OXIDE benchmark: the CPython 3.13 stdlib
(1,039 files), pip (406) and fastmcp (260). There were 10 fresh queries each, run twice
at budget 4,096: **60 requests**, with a hard cap of 70. Linux only.

| Id | Criterion | Result | |
| --- | --- | --- | --- |
| L1 | Every 200 is judged | 60/60 HTTP 200, all judged by `jev-1.14.0` | PASS |
| L2 | Deadline ≤ 3/60 | 0 | PASS |
| L3 | Other fallbacks ≤ 6/60 | 0 | PASS |
| L4 | Judged `build_context` via MCP p50 ≤ 1.5 s, p95 ≤ 2.3 s | p50 **1.00 s**, p95 1.17 s, max 1.66 s | PASS |
| L5 | Non-judged bundles equal the default | none occurred. Offline it held on 200/200 | PASS (vacuous live) |
| L6 | Median billed tokens 15k–35k | **21.5k** (p95 23.4k, max 24.5k). 63.5 candidates (cap 64), 86 KB | comparable to v3's 24.3k |
| L7 | No child, threads and fds back to start | 0 children, 3 threads, 8 fds before and after | PASS |
| L8 | ≤ 70 requests, ≤ $0.10 | 60 requests, 1,289,562 tokens, **$0.054** | PASS |

The adapter call alone (child process to parsed reply) was p50 0.85 s and max 1.27 s.
Offline, the same requests took 0.18 s p50 with a provider that answers at once
([results/sizes.json](results/sizes.json)). At this size a judged request therefore
costs about $0.0009 and adds about 1 s to `build_context`.

## Provider policies (fetched 2026-10-24 from typesafe.ai/legal, quoted verbatim)

Verified:
- **No training.** Privacy Policy (last updated Nov 19, 2025): "We will not train or fine
  tune any artificial intelligence or machine learning models on your prompts or other
  Input." Master Customer Agreement (MCA, Sep 23, 2026) § 4.1: "TypeSafe will not,
  include Customer Data in a dataset used to train (i.e., to modify the model weights of)
  any artificial intelligence or machine learning models without Customer's prior
  consent."
- **No stated retention period.** Privacy Policy: "We retain personal data about you for
  as long as reasonably necessary to provide you with the Services, or otherwise in
  support of our business or commercial purposes." Data Processing Addendum (DPA, Apr 24,
  2026) Sch. I § 8: "retained for as long as necessary taking into account the purpose
  of the Processing". MCA § 10.3: TypeSafe "will be under no obligation to store or
  retain Customer Data and may delete Customer Data at any time in its sole discretion."
- **Zero data retention is enterprise only.** docs.typesafe.ai/legal: "We also offer zero
  data retention (ZDR) for enterprise customers."
- **Derived data.** MCA § 4.1(c) licenses "in perpetuity, any Customer Data (i) to derive
  and generate Telemetry". § 4.3 defines Telemetry as "information generated in
  connection with the Services, such as technical logs, hashes, summary statistics and
  classifications".
- **Disclosure and hosting.** The Privacy Policy says TypeSafe "will not disclose any
  Input to a third party other than our service providers" and that "The Services are
  hosted in the United States". The subprocessor list is on trust.typesafe.ai, which
  needs JavaScript to read and was not read here.
- **No SLA.** MCA § 9.3: "TYPESAFE DOES NOT WARRANT THAT CUSTOMER'S USE OF THE SERVICES
  WILL BE UNINTERRUPTED OR ERROR-FREE." For 429 and 529 the docs advise retrying "with
  exponential backoff instead of retrying immediately". OXIDE falls back instead.

Unknown:
- **Which terms govern a self-serve API key.** The MCA covers "the TypeSafe-hosted
  application programming interface" but is between TypeSafe and "the entity identified
  as Customer in the order". The website Terms of Use (Sep 19, 2026) say: "Do not submit
  any information or other materials that you consider confidential or proprietary
  through the Site." Whether that sentence reaches API requests is not stated.
- How long request bodies, as opposed to "personal data", are actually kept, and whether
  logs hold request bodies.
- The subprocessors and their locations.

## Cross-platform and installation

- The judge adds no dependency. The child is the interpreter running OXIDE
  (`sys.executable -I`, stdlib `urllib`).
- Platforms CI on `0121d80` (M2) *measured*:
  - linux-x86_64, linux-py3.14 and macos-arm64 passed.
  - windows-x86_64 failed the judge test fixed above.
  - **macos-x86_64 and linux-py3.12 failed `oxide prepare`**: "downloaded FTS extension
    does not match".
- Checked here by downloading the pinned URLs: upstream re-published the `v0.21.0`
  builds for `linux_amd64`, `osx_amd64` and `win_amd64` on 2026-10-20 (Last-Modified
  20:40 GMT). Their SHA-256 no longer match `ladybug.FTS_BUILDS`. Only `osx_arm64`
  (2026-09-29) still matches.
- So a fresh `oxide prepare` fails on Linux x86-64, macOS x86-64 and Windows. CI passes
  only where the FTS cache was warm. macos-x86_64 has failed since 2026-10-22 (`b128a90`).
  This predates M3, does not involve the judge, and was **not** changed here: re-pinning
  binaries whose provenance has not been checked is a maintainer decision.
- None of these fixes has run on Platforms CI yet, because nothing is pushed.
- Not covered anywhere: a Windows desktop MCP host (the console-window fix is inferred),
  corporate TLS interception (the CA variables are now passed but not tested), and live
  calls from Windows or macOS.

## Remaining blockers and limits

1. **Blocker (installation, all of OXIDE): replaced FTS builds.** Someone has to confirm
   with LadybugDB why the `v0.21.0` artifacts changed and that the new ones are genuine.
   Then either re-pin the three hashes or move to a release whose builds are stable. Until
   then, three platforms cannot install from scratch.
2. **Blocker (judge release): provider terms and retention.** Engineering cannot settle
   this. Releasing the opt-in means users send proprietary source under terms whose scope
   and retention are unstated. The maintainer can accept and document that, or get
   TypeSafe's written answer (privacy@typesafe.ai) on API terms and body retention.
3. **Not yet run:** Platforms CI on this change (the Windows test fix, the proxy test,
   the MCP header). It runs on push.
4. **Limit: availability.** The evidence comes from clean windows: M2 had 10 requests,
   the reliability study 408 and M3 60, with 0 fallbacks live. v2's 529 episode stays
   unsampled. Fallback correctness under every fault class is measured offline. Fallback
   costs at most the 2.0 s deadline.
5. **Limit: MCP calls are serialized.** During a provider hang each `context` call takes
   up to about 2.06 s, and calls queue behind it. A consent change from another process
   during a judged request is refused until it ends.
6. **Limit: scope.** As ADR-0009 states: Python, budgets 256 / 1,024 / 4,096, and
   repositories under 3,000 files. Live calls were measured from Linux only.

**Smallest justified follow-up:** a maintainer decision on item 1, verifying and
re-pinning the three FTS hashes (a three-line change once the provenance is confirmed),
then a push so Platforms CI covers items 1 and 3. Item 2 needs no code. Changing the
default stays out of scope.

## Reproduce

```sh
S=docs/phase-6/jev-order-m3/scripts W=~/Projects/oxide-eval-data/oxide-jev-m3
uv run python $S/session.py soak  $W/soak  results/soak.json   # offline
uv run python $S/session.py sizes $W/sizes results/sizes.json  # offline
TYPESAFE_API_KEY=... uv run python $S/session.py live $W/live results/live.json  # sends; ≤70 requests
```

The runs used `systemd-run --user --scope -p MemoryMax=3G -p CPUQuota=200%`. The ledger
and the data location stay in `WORK` and are deleted at exit. Results hold only counts
and timings: no source, no key, no reply text.
