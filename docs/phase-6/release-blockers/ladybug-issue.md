# Draft issue for LadybugDB/ladybug (not posted)

**Title:** `v0.21.0` FTS extension for linux_amd64, osx_amd64 and win_amd64 replaced by a
0.22.0-dev build that core 0.21.0 cannot load

**Body:**

### What changed

Since 2026-10-20, these URLs serve different bytes from the ones built for the 0.21.0
release:

```
https://extension.ladybugdb.com/v0.21.0/{linux_amd64,osx_amd64,win_amd64}/fts/libfts.lbug_extension
```

`osx_arm64` still serves the release build.

| Platform | Release build (run [36488319988], `d7225cea54` = tag `v0.21.0`) | Served now (run [38963529396], `225bc28088`) |
| --- | --- | --- |
| linux_amd64 | `742f2756c2f80bcd1886b6ee0446483038cff0ecf6cf47f698bc6fe855cbaef6` | `eacf7d0352b1a1f04abcde4c88855937246242bffbfc170518a0fdb467a1f054` |
| osx_amd64 | `c86f427503555a3ccdc55eb66a8d82793a206aac599a6092f7d1d4eb50c21403` | `c289ed3643925836f052ab308a66e6b828341296a93728bf9b014807e5968c50` |
| win_amd64 | `7b6132ae6a554e3eeb3c4b0da7e2eb5c483b258b31e848a0e3d6000ac29885ee` | `0638ff50e1fac03f735331ff7f59491a9ff2b7d5d627b078b5d5064601cf0a85` |

These are SHA-256 digests of `fts/libfts.lbug_extension`. Each file served now is
byte-identical to the corresponding file in run 38963529396's
`lbug-extensions-<platform>` artifact. Each release build is byte-identical to the
corresponding file in run 36488319988's artifact, and those artifact zips match the
digests GitHub recorded for them.

### How a 0.22.0-dev build ended up under `v0.21.0`

1. At `225bc28088` ("chore(submodules): sync extension to latest main"),
   `CMakeLists.txt` says `project(Lbug VERSION 0.22.0 …)` (line 3), but still
   `add_definitions(-DLBUG_EXTENSION_VERSION="0.21.0")` (line 556).
2. `build-and-deploy-extension.yml` derives the release directory from that define:
   `echo "RELEASE_VERSION=v$(cat CMakeLists.txt | grep "DLBUG_EXTENSION_VERSION" | cut -d '"' -f 2 )"`.
   The `package-built-extensions` job of run 38963529396 (workflow_dispatch,
   2026-10-16 20:37) logs `RELEASE_VERSION: v0.21.0`.
3. The same job runs `rm -rf releases/$RELEASE_VERSION/` and then
   `rsync -av extension-artifacts/ releases/$RELEASE_VERSION/`. This replaces the released
   directory with the new build. `deploy-extensions` pushes the result as
   `ghcr.io/ladybugdb/extension-repo:latest`.
4. The CDN picked the new files up on 2026-10-20 (`Last-Modified` 20:40 GMT on the three
   platforms). `osx_arm64` still serves the 2026-09-29 copy, so the CDN probably refreshes
   files one at a time.

So the `releases/vX.Y.Z` directory is rebuilt from whatever commit is dispatched, and the
extension version define can lag behind the core version. PR #1334 (2026-10-20) bumped the
define, but the `v0.21.0` files had already been replaced.

### Why it matters: the new build doesn't load into 0.21.0

With the `ladybug==0.21.0` wheel from PyPI on Linux x86-64:

```python
import ladybug
conn = ladybug.Connection(ladybug.Database("db"))
conn.execute("LOAD EXTENSION '/path/to/libfts.lbug_extension'")
```

The release build loads. The file served now fails with:

```
IO exception: Failed to load library: …/libfts.lbug_extension … undefined symbol: _ZN4lbug6common11ValueVector10setAllNullEv
```

That symbol is `lbug::common::ValueVector::setAllNull()`, which core 0.21.0 doesn't export.
The new build was compiled against the 0.22.0-dev headers (98 files under `src/include/`
differ between `v0.21.2` and `225bc28088`). This is the same class of break as #971 and
#1249. macOS and Windows were not run, but their files come from the same commit.

So anyone installing the FTS extension for 0.21.x on these three platforms now gets a
file that fails to load. Projects that pin the digest (we do) get a download that fails
verification instead. We now mirror the release builds unmodified, with your license and
notices, and pin them by the same digests.

### Request

1. Restore `v0.21.0/{linux_amd64,osx_amd64,win_amd64}/fts/libfts.lbug_extension` (and any
   other extension rebuilt in run 38963529396) from run 36488319988's artifacts. Those
   artifacts expire on **2026-12-27**.
2. Make released directories immutable. For example, the deploy could refuse to write
   `releases/vX.Y.Z` when `project(Lbug VERSION …)` at the built commit isn't X.Y.Z, and
   copy existing release directories from the previous image instead of rebuilding them.
3. Optionally, publish SHA-256 digests for each released extension, so downstream projects
   can tell a release build from a replacement.

[36488319988]: https://github.com/LadybugDB/ladybug/actions/runs/36488319988
[38963529396]: https://github.com/LadybugDB/ladybug/actions/runs/38963529396
