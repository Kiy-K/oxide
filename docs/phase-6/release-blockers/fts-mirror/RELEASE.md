LadybugDB FTS extension 0.21.0, mirrored unmodified for `oxide prepare`.

These are LadybugDB's own release builds, not OXIDE builds. Each
`libfts-0.21.0-<platform>.lbug_extension` is byte-identical to
`fts/libfts.lbug_extension` in the artifacts of LadybugDB CI run
[36488319988](https://github.com/LadybugDB/ladybug/actions/runs/36488319988). That run
was a manual dispatch on 2026-09-28 at
[`d7225cea54`](https://github.com/LadybugDB/ladybug/commit/d7225cea54c37d5300a591cd704e24ebd892e822)
(tag `v0.21.0`, the core of `ladybug==0.21.0` on PyPI). It used the extensions submodule
at [`bf74ab654a`](https://github.com/LadybugDB/extensions/tree/bf74ab654af6bd40a0e3208ac4e8bcf5cb45a7a0/fts).

| Asset | SHA-256 of the file | Source artifact and its GitHub-recorded zip digest |
| --- | --- | --- |
| `libfts-0.21.0-linux_amd64.lbug_extension` | `742f2756c2f80bcd1886b6ee0446483038cff0ecf6cf47f698bc6fe855cbaef6` | `lbug-extensions-linux-x86_64`, `sha256:bf8ea964e95d3ab31a4cbd3ceeefc681d203a5e9f015c3b1dca1ef3315ec1b64` |
| `libfts-0.21.0-osx_amd64.lbug_extension` | `c86f427503555a3ccdc55eb66a8d82793a206aac599a6092f7d1d4eb50c21403` | `lbug-extensions-osx-x86_64`, `sha256:4cba15c75114122881eeeb1735e36fa088bb6e0952c81731a8b20b51ebe6f834` |
| `libfts-0.21.0-osx_arm64.lbug_extension` | `248332de781a7781f612a55b5397e88f58f1f1ba2a973836ada199eab0f6bf10` | `lbug-extensions-osx-arm64`, `sha256:19da9ab75b3153a4b28494f89b4ef313614f34f37f443debc0af4c34bebc1158` |
| `libfts-0.21.0-win_amd64.lbug_extension` | `7b6132ae6a554e3eeb3c4b0da7e2eb5c483b258b31e848a0e3d6000ac29885ee` | `lbug-extensions-win-x86_64`, `sha256:24f0a09ac6049b86fc0ace6f13ccc2a776d371b3d283d7dad40c2a9adf8fce95` |

These SHA-256 values are the ones OXIDE has pinned in `oxide.storage.ladybug.FTS_BUILDS`
since 2026-10-17. `oxide prepare` refuses any file that does not match, and the engine
checks the file again before every load.

**Why a mirror:** since 2026-10-20, `extension.ladybugdb.com/v0.21.0/` has served a build
of a 0.22.0-development core for linux_amd64, osx_amd64 and win_amd64. That build does
not load into core 0.21.0 (`undefined symbol:
_ZN4lbug6common11ValueVector10setAllNullEv`). The details are in
`docs/phase-6/release-blockers/` in this repository.

**License:** LadybugDB and its extensions are MIT-licensed. The FTS extension also
contains Snowball (BSD-3-Clause), RE2 (BSD-3-Clause), cppjieba and limonp (MIT). Their
notices are in `NOTICE.txt`, attached to this release. The files are redistributed
unmodified, and this release is not affiliated with or endorsed by LadybugDB.
