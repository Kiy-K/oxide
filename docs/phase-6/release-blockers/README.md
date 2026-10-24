# Release blockers after ADR-0009 M3 (2026-10-24)

This is a follow-up on M3's two blockers. No feature work was done. The judge is still
off by default, and its behaviour and ranking policy are unchanged. Numbers are measured
unless marked *inferred*.

| | Verdict |
| --- | --- |
| FTS extension integrity | **Provenance established, and the new builds are rejected.** They are genuine upstream CI builds, but of a different core, and they do not load into the core OXIDE ships. The pins are kept. CI now fetches the extension cold |
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
  - Until the files are restored, Platforms will be red on linux-x86_64, macos-x86_64
    and windows-x86_64. That is the true state of a fresh install there. Not yet run:
    nothing has been pushed.
- **A verified copy of the pinned files exists.** All four upstream artifacts from run
  36488319988 (GitHub digest-verified) are in
  `~/Projects/oxide-eval-data/fts-provenance/art/`, outside the repository. Upstream's
  copies expire on **2026-12-27**. LadybugDB is MIT-licensed, so it may be
  redistributed with its notice.
- Until then, users on the three platforms can unblock themselves by placing a verified
  file and setting `OXIDE_FTS_EXTENSION`. The engine still checks the digest.

### Smallest corrections, in order of preference

1. **Upstream restores** `v0.21.0/{linux_amd64,osx_amd64,win_amd64}/fts` to the run
   36488319988 builds, and stops rebuilding a released directory from main. No OXIDE
   change is needed, and cold CI turns green. Draft issue: [ladybug-issue.md](ladybug-issue.md)
   (not posted).
2. **OXIDE mirrors the four verified files**, for example as assets on an OXIDE GitHub
   release, with the LadybugDB license. `prepare_fts` then fetches from that URL, and
   the **hashes stay the same**. That is about a one-line URL change, but it needs the
   maintainer to authorize publishing, and the change only makes sense once the assets
   exist.
3. Move to a core whose extensions are built from its own tag. None exists today:
   0.21.1 and 0.21.2 share the `v0.21.0` directory, and 0.22.0 is unreleased.

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
  depends on it. Core is blocked by § 1: a fresh `oxide prepare` fails on Linux x86-64,
  macOS x86-64 and Windows. Search still runs there, but the lexical channel is
  `unavailable`, and every quality result was measured with it on. Once § 1 is fixed by
  correction 1 or 2, core has no other known blocker from this work.
- **The judge ships inside the core package** (`Repository.judge`, `oxide judge`). It
  cannot be released ahead of core, and releasing core releases its opt-in surface too.
  The maintainer can either:
  - (a) ship that surface with today's disclosure, telling users that retention is
    unstated; or
  - (b) wait for TypeSafe's answer before releasing a version that contains it.

  Withdrawing or hiding the surface would be a public-API change. This milestone did not
  make one.
- **Concrete remaining blockers:**
  1. Restored or mirrored FTS builds (an upstream action, or maintainer-authorized
     hosting).
  2. Push and a green cold Platforms run on all four platforms.
  3. For the judge only: TypeSafe's written answer, or the maintainer's decision to
     ship under (a).
