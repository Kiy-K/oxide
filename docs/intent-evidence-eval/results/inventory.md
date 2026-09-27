## Counts by evidence type (all records / kept after filtering)

| repo | lang | docstring | leading_comment | inline_comment | module_doc | readme | doc | changelog | todo-tagged | kept tokens | code tokens | kept/code |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| flask | python | 304/304 | 33/29 | 340/317 | 2/1 | 15/15 | 652/622 | 81/81 | 0 | 169k | 140k | 1.20 |
| requests | python | 292/288 | 96/51 | 438/341 | 96/29 | 6/6 | 123/116 | 84/84 | 9 | 43k | 188k | 0.23 |
| pytest | python | 1126/1079 | 104/69 | 1189/1056 | 62/58 | 15/15 | 953/942 | 766/761 | 64 | 333k | 592k | 0.56 |
| pylint | python | 3145/2706 | 739/424 | 2643/2002 | 1459/895 | 9/9 | 2126/1776 | 6/6 | 93 | 345k | 788k | 0.44 |
| seaborn | python | 521/520 | 130/122 | 1620/1524 | 80/70 | 6/6 | 246/246 | 0/0 | 201 | 99k | 453k | 0.22 |
| httpx | python | 296/287 | 18/14 | 298/274 | 9/9 | 7/7 | 237/231 | 200/199 | 1 | 65k | 190k | 0.34 |
| ripgrep | rust | 0/0 | 1058/1057 | 894/812 | 8/8 | 61/61 | 121/121 | 57/55 | 10 | 130k | 368k | 0.35 |
| tokio | rust | 0/0 | 1927/1712 | 2998/2708 | 212/182 | 38/38 | 67/63 | 329/328 | 45 | 295k | 885k | 0.33 |
| nushell | rust | 5/5 | 1754/1698 | 4497/4106 | 66/59 | 102/102 | 88/87 | 57/57 | 234 | 173k | 2289k | 0.08 |
| clap | rust | 0/0 | 714/583 | 689/559 | 214/105 | 22/22 | 90/90 | 858/854 | 11 | 138k | 555k | 0.25 |
| darkreader | typescript | 0/0 | 77/61 | 330/241 | 11/1 | 11/11 | 62/59 | 0/0 | 19 | 14k | 218k | 0.06 |
| code-server | typescript | 0/0 | 144/140 | 301/251 | 4/3 | 15/15 | 218/209 | 35/35 | 13 | 39k | 99k | 0.39 |
| zod | typescript | 0/0 | 1108/652 | 3379/2351 | 105/32 | 250/246 | 345/330 | 39/39 | 7 | 167k | 928k | 0.18 |
| **total** | | 5689/5189 | 7902/6612 | 19616/16542 | 2328/1452 | 557/553 | 5328/4892 | 2512/2499 | 707 | | | |

## Size per record (kept, chars): mean / median / p90

| type | n | mean | median | p90 | share symbol-associated |
|---|---:|---:|---:|---:|---:|
| docstring | 5189 | 139 | 66 | 302 | 98% |
| leading_comment | 6612 | 206 | 88 | 536 | 100% |
| inline_comment | 16542 | 92 | 56 | 178 | 80% |
| module_doc | 1452 | 200 | 62 | 512 | 0% |
| readme | 553 | 509 | 358 | 1297 | 0% |
| doc | 4892 | 588 | 390 | 1456 | 0% |
| changelog | 2499 | 396 | 198 | 1308 | 0% |

## Symbol coverage: share of non-module symbols with attached intent

| repo | lang | symbols | docstring/leading | any attached (incl. inline) | attached symbols in test files |
|---|---|---:|---:|---:|---:|
| flask | python | 1674 | 19% | 23% | 23% |
| requests | python | 949 | 35% | 42% | 4% |
| pytest | python | 5138 | 22% | 30% | 42% |
| pylint | python | 9731 | 31% | 34% | 59% |
| seaborn | python | 2902 | 21% | 29% | 18% |
| httpx | python | 1323 | 23% | 29% | 34% |
| ripgrep | rust | 2021 | 52% | 56% | 4% |
| tokio | rust | 5505 | 31% | 41% | 13% |
| nushell | rust | 14607 | 12% | 20% | 13% |
| clap | rust | 3474 | 16% | 21% | 12% |
| darkreader | typescript | 1317 | 5% | 11% | 16% |
| code-server | typescript | 325 | 43% | 53% | 28% |
| zod | typescript | 4194 | 16% | 23% | 6% |

## Duplication (kept records)

| repo | exact-dup texts | normalized-dup texts | doc chunks containing a >60-char code comment/docstring verbatim |
|---|---:|---:|---:|
| flask | 2% | 2% | 0/718 |
| requests | 4% | 4% | 0/206 |
| pytest | 3% | 3% | 5/1718 |
| pylint | 13% | 14% | 6/1791 |
| seaborn | 7% | 8% | 0/252 |
| httpx | 6% | 6% | 0/437 |
| ripgrep | 7% | 7% | 1/237 |
| tokio | 11% | 11% | 2/429 |
| nushell | 13% | 13% | 0/246 |
| clap | 7% | 7% | 2/966 |
| darkreader | 9% | 9% | 0/70 |
| code-server | 3% | 3% | 0/259 |
| zod | 15% | 15% | 1/615 |

## Flags (all records)

records: 43932

| flag | count | share | by type |
|---|---:|---:|---|
| directive_or_trivial | 4187 | 10% | inline_comment:2410, leading_comment:863, docstring:470, module_doc:444 |
| too_short | 1611 | 4% | inline_comment:953, docstring:321, leading_comment:246, module_doc:85 |
| commented_code | 929 | 2% | inline_comment:487, leading_comment:369, module_doc:58, docstring:15 |
| generated_file | 506 | 1% | doc:364, inline_comment:86, leading_comment:52, module_doc:2 |
| license | 397 | 1% | module_doc:371, inline_comment:16, leading_comment:10 |
| not_indexed | 102 | 0% | inline_comment:95, module_doc:7 |
| email | 98 | 0% | module_doc:40, doc:38, leading_comment:6, inline_comment:5 |
| generated_marker | 59 | 0% | doc:23, leading_comment:14, inline_comment:8, changelog:7 |
| secret:high_entropy | 26 | 0% | doc:8, inline_comment:6, changelog:5, leading_comment:4 |
| secret:assigned_secret | 8 | 0% | doc:6, leading_comment:2 |
| secret:url_credentials | 5 | 0% | doc:3, docstring:2 |

## Secret-like hits (location and class only)

- flask `docs/patterns/wtforms.rst:24` doc secret:assigned_secret
- requests `docs/user/advanced.rst:324` doc secret:url_credentials
- requests `docs/user/advanced.rst:464` doc secret:assigned_secret
- requests `docs/user/authentication.rst:13` doc secret:assigned_secret
- requests `docs/user/authentication.rst:46` doc secret:assigned_secret
- requests `requests/packages/urllib3/util.py:53` docstring secret:high_entropy
- pytest `TIDELIFT.rst:39` doc secret:high_entropy
- pytest `doc/en/announce/release-2.0.3.rst:21` changelog secret:high_entropy
- pytest `doc/en/changelog.rst:7081` changelog secret:high_entropy
- pytest `doc/en/flaky.rst:101` doc secret:high_entropy
- pytest `testing/python/fixtures.py:1429` inline_comment secret:high_entropy
- pytest `testing/test_conftest.py:423` inline_comment secret:high_entropy
- seaborn `seaborn/external/appdirs.py:8` module_doc secret:high_entropy
- httpx `docs/advanced/authentication.md:207` doc secret:assigned_secret
- httpx `docs/advanced/proxies.md:1` doc secret:high_entropy
- httpx `docs/advanced/proxies.md:38` doc secret:url_credentials
- httpx `docs/advanced/proxies.md:68` doc secret:url_credentials
- httpx `docs/advanced/ssl.md:72` doc secret:high_entropy
- httpx `httpx/_urls.py:17` docstring secret:url_credentials
- httpx `httpx/_urls.py:329` docstring secret:url_credentials
- ripgrep `CHANGELOG.md:485` changelog secret:high_entropy
- ripgrep `CHANGELOG.md:525` changelog secret:high_entropy
- tokio `examples/tinyhttp.rs:236` leading_comment secret:high_entropy
- tokio `tokio-util/src/sync/cancellation_token.rs:17` leading_comment secret:assigned_secret
- tokio `tokio-util/src/sync/cancellation_token.rs:167` leading_comment secret:assigned_secret
- tokio `tokio/src/util/pad.rs:5` leading_comment secret:high_entropy
- nushell `crates/nu-protocol/src/lev_distance.rs:1` module_doc secret:high_entropy
- nushell `crates/nu-protocol/src/lev_distance.rs:198` inline_comment secret:high_entropy
- nushell `crates/nu_plugin_gstat/src/gstat.rs:5` inline_comment secret:high_entropy
- nushell `crates/nu_plugin_query/src/web_tables.rs:504` leading_comment secret:high_entropy
- nushell `crates/nu_plugin_query/src/web_tables.rs:534` leading_comment secret:high_entropy
- clap `CHANGELOG.md:260` changelog secret:high_entropy
- code-server `docs/FAQ.md:88` doc secret:high_entropy
- code-server `docs/FAQ.md:317` doc secret:high_entropy
- code-server `docs/guide.md:50` doc secret:assigned_secret
- code-server `docs/guide.md:425` doc secret:high_entropy
- code-server `docs/termux.md:114` doc secret:high_entropy
- code-server `src/node/cli.ts:266` inline_comment secret:high_entropy
- zod `packages/zod/src/v4/classic/tests/record.test.ts:683` inline_comment secret:high_entropy
