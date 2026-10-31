# Release blockers after ADR-0009 M3 (2026-10-24)

This is a follow-up on M3's two blockers. No feature work was done. The judge is still
off by default, and its behaviour and ranking policy are unchanged. Numbers are measured
unless marked *inferred*.

| | Verdict |
| --- | --- |
| FTS extension integrity | **Provenance established, and the new builds are rejected.** They are genuine upstream CI builds, but of a different core, and they do not load into the core OXIDE ships. The pins are kept, CI fetches the extension cold, and an OXIDE mirror of the release builds is **prepared but not published** |
| TypeSafe data handling | **Written clarification needed.** The draft inquiry is [typesafe-inquiry.md](typesafe-inquiry.md) (not sent) |
| Release independence | Core OXIDE does not depend on the judge, but it is **still blocked on FTS** for 3 of 4 platforms. The opt-in judge is blocked on FTS (it ships inside core) **and** on TypeSafe's answer |

## 1. LadybugDB FTS extension `v0.21.0`

### What changed, and who built it

Upstream publishes extensions through the GitHub Actions workflow `Build and Deploy`
(`build-and-deploy-extension.yml` → `deploy-extension.yml`). It builds every extension
for `releases/v$LBUG_EXTENSION_VERSION`, taking that version from `CMakeLists.txt` at
the commit being built. It copies the older releases from the previous image
(`get-extensions-from-ghcr.yml`) and pushes an nginx image,
`ghcr.io/ladybugdb/extension-repo:latest`, which `extension.ladybugdb.com` serves. It
deletes and rebuilds that release directory on every run, so `v0.21.0` is **mutable**.
Main kept extension version `0.21.0` until PR #1334 (2026-10-20), long after its core
moved on.

Matched by SHA-256 of the extension file inside each GitHub artifact. The artifact zip
digests are recorded by GitHub, and the local copies match them:

| Platform | Pinned (OXIDE) | Upstream build of the pin | Served since 2026-10-20 | Upstream build of it |
| --- | --- | --- | --- | --- |
| linux_amd64 | `742f2756…` | run 36488319988 | `eacf7d03…` | run 38963529396 |
| osx_amd64 | `c86f4275…` | run 36488319988 | `c289ed36…` | run 38963529396 |
| win_amd64 | `7b6132ae…` | run 36488319988 | `0638ff50…` | run 38963529396 |
| osx_arm64 | `248332de…` | run 36488319988 | unchanged | (still the CDN's 2026-09-29 copy) |

- **Run 36488319988** (2026-09-28): manual dispatch by `adsharma` at `d7225cea54`, the
  commit tagged **`v0.21.0`**. `project(Lbug VERSION 0.21.0)`. This is the core that
  `ladybug==0.21.0` (PyPI, uploaded 2026-09-28T23:26Z) is built from.
- **Run 38963529396** (2026-10-16 20:37): manual dispatch by `adsharma` at `225bc28088`
  ("chore(submodules): sync extension to latest main"). `project(Lbug VERSION 0.22.0)`.
  It is 178 commits past `v0.21.2` and changes 98 files under `src/include/`. Its log
  shows `RELEASE_VERSION: v0.21.0`, and it deployed at 21:57.
- Image snapshots (`ghcr-extensions` artifacts, read by range requests): the image
  pulled on 2026-10-18 and 2026-10-20 already held these files under `v0.21.0`. The CDN
  changed its copies on 2026-10-20 at 20:40, during the next deploy. nginx sets
  `expires 15552000s`, and osx_arm64 still serves the 09-29 copy while the image holds
  another file. *Inferred* from that: the CDN serves cached copies and refreshes them
  per file, not on every deploy.

So the new files are authentic, but they are builds of a **0.22.0-development core**
published under `v0.21.0`. This is the hazard upstream issues #971 and #1249 describe:
an extension built against changed headers crashes or fails to load into another core.

### Compatibility, measured

The suite was run in a scratch worktree with the new Linux file substituted for the pin
(`ladybug==0.21.0`, Linux x86-64): **64 failed, 104 errors**. The extension does not
load:

```
undefined symbol: _ZN4lbug6common11ValueVector10setAllNullEv
```

That symbol is `lbug::common::ValueVector::setAllNull()`. With the pinned file, the same
suite passes (381 passed). The macOS and Windows files were not run, because there is no
such machine here. They come from the same commit, so the same mismatch is *inferred*.

### Decision

- **The pins are unchanged.** Re-pinning would turn a loud `oxide prepare` failure into
  an extension that downloads and then fails to load. The integrity check did its job.
- **CI can no longer hide this.**
  - `platforms.yml` no longer caches the extension, so every platform fetches it cold.
  - `ci.yml` keeps its corpus cache but deletes the cached extension before bootstrap.
  - Until the mirror is published, a push would leave every Platforms job red: the code
    now points at the mirror. Without the mirror change, linux-x86_64, macos-x86_64 and
    windows-x86_64 were red. That is the true state of a fresh install. Not yet run:
    nothing has been pushed.
- **A verified copy of the pinned files exists.** All four upstream artifacts from run
  36488319988 (GitHub digest-verified) are in
  `~/Projects/oxide-eval-data/fts-provenance/art/`, outside the repository. Upstream's
  copies expire on **2026-12-27**. LadybugDB is MIT-licensed, so it may be
  redistributed with its notice.
- Until then, users on the three platforms can unblock themselves by placing a verified
  file and setting `OXIDE_FTS_EXTENSION`. The engine still checks the digest.

### Correction: an OXIDE mirror of the release builds (prepared, not published)

The maintainer chose the mirror on 2026-10-24. Upstream restoring its files would still
help other users. The issue is drafted in [ladybug-issue.md](ladybug-issue.md), not posted.

- **What it is.** One release, `ladybug-fts-0.21.0` on `Kiy-K/oxide`. It holds the four
  files from run 36488319988, unmodified and renamed
  `libfts-0.21.0-<platform>.lbug_extension`. It also carries `SHA256SUMS` and
  `NOTICE.txt`, and its body is [fts-mirror/RELEASE.md](fts-mirror/RELEASE.md). The
  notices are LadybugDB core and extensions (MIT), plus what the FTS extension links:
  Snowball (BSD-3-Clause), RE2 (BSD-3-Clause), cppjieba and limonp (MIT). Each was read
  at the pinned commits `d7225cea54` and `bf74ab654a`.
- **What changed in OXIDE.** Only the URL in `ladybug._fts_build`. `FTS_BUILDS`, the
  digest checks, `OXIDE_FTS_EXTENSION` and the error messages are unchanged. There is no
  fallback host, and no path skips the digest.
- **Immutability.** The pins are the guarantee: a replaced or tampered asset fails
  `oxide prepare`, as upstream's did. GitHub immutable releases add to that. Assets
  can't be changed after publishing, and the tag can't be moved or reused. The repo
  setting is off today (`{"enabled":false}`), and turning it on affects every later
  release of this repo.
- **Checked locally (Linux x86-64).**
  - The staged files match all four pins, and the four artifact zips match
    GitHub's recorded digests.
  - `prepare_fts` was run cold against a local server holding the staged mirror. It
    accepted the file, and the full suite passed with it (381 passed, 7 skipped).
  - Served under the same name, the incompatible upstream build was refused ("does not
    match"), and no file was written.
  - `LOAD EXTENSION` loads the mirrored file into `ladybug==0.21.0`. The upstream
    replacement fails with the undefined symbol.
- **Not yet checked:** the real download from GitHub, and cold installs on macOS and
  Windows. Both need the release to be published and pushed (below).

#### Publication steps (each needs the maintainer's authorization)

Run them in this order. Pushing first would make `oxide prepare` 404 on every platform.

```sh
M=~/Projects/oxide-eval-data/fts-provenance/mirror   # the staged, verified files
cp docs/phase-6/release-blockers/fts-mirror/{NOTICE.txt,SHA256SUMS} "$M"/
(cd "$M" && sha256sum -c SHA256SUMS)
# 1. Optional, repo-wide: make new releases immutable.
gh api -X PUT -H "X-GitHub-Api-Version: 2026-03-10" repos/Kiy-K/oxide/immutable-releases
# 2. Create it as a draft with every asset attached, then publish it. The tag targets a pushed commit.
gh release create ladybug-fts-0.21.0 -R Kiy-K/oxide --draft --latest=false --target 0121d80453644f4542f66178d67598c8c15c80a8 \
  --title "LadybugDB FTS extension 0.21.0 (mirror)" \
  --notes-file docs/phase-6/release-blockers/fts-mirror/RELEASE.md \
  "$M"/libfts-0.21.0-*.lbug_extension "$M"/SHA256SUMS "$M"/NOTICE.txt
gh release edit ladybug-fts-0.21.0 -R Kiy-K/oxide --draft=false
# 3. Check what GitHub serves, then fetch it cold through the real code path.
D=$(mktemp -d ~/Projects/oxide-eval-data/fts-provenance/served.XXXX)
gh release download ladybug-fts-0.21.0 -R Kiy-K/oxide -D "$D" && (cd "$D" && sha256sum -c SHA256SUMS)
env -u OXIDE_FTS_EXTENSION XDG_CACHE_HOME="$D/cache" uv run oxide prepare
# 4. Push rewrite/v2. CI and Platforms then fetch the asset cold on all four platforms.
git push origin rewrite/v2
```

Done when every Platforms job is green, with `oxide prepare` downloading in each one:
linux-x86_64 (and its py3.12 and py3.14 jobs), macos-arm64, macos-x86_64 and
windows-x86_64.

## 2. TypeSafe data handling

The documents were read verbatim on 2026-10-24 (M3 README § Provider policies). These
are the points that remain open after reading the MCA itself:

- **Self-serve coverage: likely the MCA, not confirmed.** The MCA's "Order" includes
  "the checkout page on TypeSafe's website, or the confirmation email generated by
  TypeSafe referencing this Agreement", and "Acceptance of this Agreement is a condition
  to accessing and using the Services". Whether this account's sign-up or top-up
  referenced the MCA is not known here: the console needs a login. The Terms of Use
  still say "Do not submit any information or other materials that you consider
  confidential or proprietary through the Site", and nothing says whether "the Site"
  includes the API.
- **Request-body retention: unspecified.** No training (MCA § 4.1) is not zero
  retention. The MCA gives TypeSafe the right to "store" Customer Data during the Term
  (§ 4.1), and "no obligation to store or retain" it (§ 10.3). The DPA and the Privacy
  Policy cover personal data, "as long as necessary". No period is stated for request
  bodies, logs or backups.
- **Confidentiality: plausible, not explicit.** Under § 14.1, Confidential Information
  is anything designated, or "reasonably understood to be proprietary or confidential
  due to its nature". Customer Data is not named, whereas TypeSafe's own information is.
- **Telemetry: broad.** § 4.1(c) licenses "in perpetuity, any Customer Data … to derive
  and generate Telemetry". § 4.3 defines Telemetry to include "classifications, metrics,
  and learnings related to Customer's use", which TypeSafe "may Process … without
  restriction, including to improve the Services". Whether Telemetry can carry request
  content, and how "learnings" fit with the no-training clause, is not stated.
- **Deletion: no customer right stated.** TypeSafe "may delete Customer Data at any time
  in its sole discretion" (§ 10.3), and "Customer Confidential Information may be
  retained in TypeSafe's standard backups". The DPA's deletion obligations are tied to
  personal data. ZDR is offered "for enterprise customers".

The inquiry asks exactly these seven questions. Until it is answered, the judge's
disclosure (`JudgeStatus.discloses`, `oxide judge`) already states what is verified: no
training, no stated retention period, ZDR for enterprise only. It claims nothing more.

## 3. Can core OXIDE and the optional judge be released independently?

- **Core OXIDE without the judge: yes in principle, but blocked now.** The judge is off
  by default, adds no dependency, and its consent check is one `stat`. Nothing in core
  depends on it. Core is blocked by § 1 until the mirror is published: before it, a fresh `oxide
  prepare` fails on Linux x86-64, macOS x86-64 and Windows. Search still runs there, but the lexical channel is
  `unavailable`, and every quality result was measured with it on. Once the mirror is published and
  cold CI passes, core has no other known blocker from this work.
- **The judge ships inside the core package** (`Repository.judge`, `oxide judge`). It
  cannot be released ahead of core, and releasing core releases its opt-in surface too.
  The maintainer can either:
  - (a) ship that surface with today's disclosure, telling users that retention is
    unstated; or
  - (b) wait for TypeSafe's answer before releasing a version that contains it.

  Withdrawing or hiding the surface would be a public-API change. This milestone did not
  make one.
- **Concrete remaining blockers:**
  1. Publish the prepared `ladybug-fts-0.21.0` mirror (§ 1, publication steps).
  2. Push, and get a green cold Platforms run on all four platforms.
  3. For the judge only: TypeSafe's written answer, or the maintainer's decision to
     ship under (a).
