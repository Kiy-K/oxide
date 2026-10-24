# Draft issue for LadybugDB/ladybug (not posted)

**Title:** CDN `v0.21.0` fts builds for linux_amd64, osx_amd64 and win_amd64 were
replaced by a 0.22.0-dev build that core 0.21.0 cannot load

**Body:**

Since 2026-10-20, `https://extension.ladybugdb.com/v0.21.0/{linux_amd64,osx_amd64,win_amd64}/fts/libfts.lbug_extension`
serves different bytes from the ones published with the 0.21.0 release. Only
`osx_arm64` is unchanged.

| Platform | 0.21.0 release (run 36488319988, `d7225cea54` = tag v0.21.0) | Served now |
| --- | --- | --- |
| linux_amd64 | `742f2756c2f80bcd1886b6ee0446483038cff0ecf6cf47f698bc6fe855cbaef6` | `eacf7d0352b1a1f04abcde4c88855937246242bffbfc170518a0fdb467a1f054` |
| osx_amd64 | `c86f427503555a3ccdc55eb66a8d82793a206aac599a6092f7d1d4eb50c21403` | `c289ed3643925836f052ab308a66e6b828341296a93728bf9b014807e5968c50` |
| win_amd64 | `7b6132ae6a554e3eeb3c4b0da7e2eb5c483b258b31e848a0e3d6000ac29885ee` | `0638ff50e1fac03f735331ff7f59491a9ff2b7d5d627b078b5d5064601cf0a85` |

The new files are byte-identical to the artifacts of run 38963529396
(`225bc28088`, 2026-10-16). That commit already declares `project(Lbug VERSION 0.22.0)`
but still `LBUG_EXTENSION_VERSION="0.21.0"`, so the run packaged and deployed
`releases/v0.21.0`.

With the `ladybug==0.21.0` Python wheel on Linux x86-64, loading the new file fails:

```
IO exception: Failed to load library: …/libfts.lbug_extension … undefined symbol: _ZN4lbug6common11ValueVector10setAllNullEv
```

The release-built file loads fine. This is the class of break in #971 and #1249.
Downstream projects that pin the digest (we do) now fail to install on three platforms.
Projects that don't pin get an extension that does not load.

**Request:** please restore the three `v0.21.0` files from run 36488319988's artifacts
(they expire 2026-12-27). Please also consider making a released extension directory
immutable, for example by refusing to deploy `releases/vX.Y.Z` from a commit whose
`project()` version differs from X.Y.Z.
