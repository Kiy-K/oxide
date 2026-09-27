# Differential run summary

Reps per process: 10 x 3 processes per engine.

## Corpus

| group | c | cpp | go | java | javascript | markdown | php | python | ruby | rust | tsx | typescript | total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| conformance | 4 | 3 | 2 | 6 | 6 |  | 6 | 3 | 5 | 3 | 3 | 5 | 46 |
| fixtures |  |  |  |  |  | 1 |  | 8 |  |  | 2 | 6 | 17 |
| real:axios |  |  |  |  | 54 |  |  |  |  |  |  |  | 54 |
| real:darkreader |  |  |  |  |  |  |  |  |  |  | 46 | 38 | 84 |
| real:flask |  |  |  |  |  |  |  | 62 |  |  |  |  | 62 |
| real:fmt |  | 70 |  |  |  |  |  |  |  |  |  |  | 70 |
| real:gin |  |  | 99 |  |  |  |  |  |  |  |  |  | 99 |
| real:gson |  |  |  | 86 |  |  |  |  |  |  |  |  | 86 |
| real:tokio |  |  |  |  |  |  |  |  |  | 96 |  |  | 96 |
| real:zstd | 61 |  |  |  |  |  |  |  |  |  |  |  | 61 |
| relations |  |  |  |  |  |  |  | 4 |  |  | 4 | 6 | 14 |

## Determinism

- cgk: 1 distinct output hash(es) across 3 processes
- oxide: 1 distinct output hash(es) across 3 processes

## Performance (per-language sum of per-file warm medians)

| language | files | CG ok | KiB | OXIDE ms | CG ms | CG extract-only ms | OXIDE/CG | per-file OXIDE/CG p10 / median / p90 | OXIDE parse_file share | OXIDE seam ÷ 1 parse | CG extract ÷ 1 parse |
|---|--:|--:|--:|--:|--:|--:|--:|---|--:|--:|--:|
| c | 65 | 15 | 121 | 112.5 | 22.4 | 22.3 | 5.02x | 3.25 / 4.65 / 5.21 | 59% | 6.0 | 1.2 |
| cpp | 73 | 38 | 90 | 143.7 | 31.8 | 31.6 | 4.52x | 3.67 / 4.37 / 4.72 | 59% | 6.0 | 1.3 |
| go | 101 | 100 | 675 | 623.6 | 123.7 | 122.5 | 5.04x | 3.25 / 4.12 / 4.80 | 54% | 8.2 | 1.5 |
| java | 92 | 91 | 680 | 270.7 | 71.0 | 70.1 | 3.81x | 2.37 / 3.34 / 4.15 | 56% | 7.7 | 1.8 |
| javascript | 60 | 59 | 119 | 81.0 | 19.5 | 19.3 | 4.15x | 3.35 / 3.99 / 4.64 | 58% | 7.4 | 1.6 |
| markdown | 1 | 0 | | | | | | kernel extracted no file | |
| php | 6 | 5 | 1 | 1.4 | 0.4 | 0.4 | 3.27x | 2.29 / 3.53 / 3.64 | 61% | 8.3 | 2.1 |
| python | 77 | 76 | 529 | 329.1 | 74.2 | 73.3 | 4.44x | 3.06 / 4.11 / 4.66 | 58% | 7.6 | 1.5 |
| ruby | 5 | 4 | 1 | 1.5 | 0.5 | 0.5 | 2.88x | 2.40 / 3.06 / 3.50 | 61% | 7.4 | 2.2 |
| rust | 99 | 98 | 803 | 418.8 | 94.9 | 94.0 | 4.41x | 3.50 / 4.55 / 4.98 | 57% | 7.7 | 1.6 |
| tsx | 55 | 54 | 84 | 56.5 | 13.2 | 13.0 | 4.29x | 3.00 / 4.18 / 4.69 | 58% | 7.4 | 1.5 |
| typescript | 55 | 54 | 196 | 145.7 | 34.8 | 34.4 | 4.19x | 3.05 / 3.90 / 4.77 | 57% | 7.2 | 1.5 |
| **all (CG-ok files)** | 689 | 594 | 3299 | 2184.3 | 486.3 | 481.5 | 4.49x | 2.99 / 4.01 / 4.78 | 56% | 7.5 | 1.5 |

Only files the kernel extracted are timed on both sides (a deferred file returns early and would flatter the kernel). "÷ 1 parse" divides each engine's time by one bare tree-sitter parse of the same file with that engine's own grammar.

Files the kernel deferred (94): OXIDE seam 6641.6 ms total; kernel time-to-defer 840.4 ms total.

## Cold vs warm (whole corpus, per process)

| engine | cold rep ms (3 processes) | warm rep ms median | warm rep ms min–max |
|---|---|--:|---|
| OXIDE | 9223, 9163, 9165 | 8830 | 8822–8843 |
| CG kernel | 1331, 1332, 1332 | 1327 | 1326–1333 |

Rep totals are the sum of per-file engine time over every manifest file (the kernel's deferred files included, at their time-to-defer).

## Memory

| engine | VmHWM after corpus load (MiB) | VmHWM end of bench (MiB) | delta | /usr/bin/time max RSS, one dump (MiB) | wall, one dump (s) |
|---|--:|--:|--:|--:|--:|
| OXIDE | 10.7 | 37.8 | +27.1 | 39.5 | 10.32 |
| CG kernel | 10.8 | 35.2 | +24.4 | 34.7 | 1.41 |

## Output differences — raw input (what a Rust-only integration would see)

| language | files | compared | CG failed | OXIDE syms on CG-failed | OXIDE syms | matched | exact span | kind agree | qn exact / suffix / differs | OXIDE-only | CG-only (comparable kinds) | CG-only (schema-extra) | CG namespace |
|---|--:|--:|--:|--:|--:|--:|--:|--:|---|--:|--:|--:|--:|
| c | 65 | 15 | 50 | 3397 | 188 | 142 | 142 | 132 | 142 / 0 / 0 | 46 | 8 | 1 | 0 |
| cpp | 73 | 38 | 35 | 5786 | 306 | 245 | 245 | 192 | 236 / 0 / 9 | 61 | 18 | 20 | 0 |
| go | 101 | 100 | 1 | 1 | 1989 | 1724 | 1643 | 1630 | 1660 / 64 / 0 | 265 | 1 | 3 | 0 |
| java | 92 | 91 | 1 | 2 | 1021 | 966 | 966 | 965 | 0 / 923 / 43 | 55 | 218 | 235 | 90 |
| javascript | 60 | 59 | 1 | 0 | 273 | 230 | 230 | 230 | 221 / 3 / 6 | 43 | 54 | 0 | 0 |
| markdown | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 / 0 / 0 | 0 | 0 | 0 | 0 |
| php | 6 | 5 | 1 | 1 | 24 | 24 | 24 | 22 | 5 / 19 / 0 | 0 | 0 | 2 | 0 |
| python | 77 | 76 | 1 | 1 | 1646 | 1646 | 1072 | 1544 | 1646 / 0 / 0 | 0 | 21 | 6 | 0 |
| ruby | 5 | 4 | 1 | 0 | 19 | 17 | 17 | 14 | 14 / 1 / 2 | 2 | 1 | 0 | 0 |
| rust | 99 | 98 | 1 | 2 | 1262 | 1151 | 1151 | 1148 | 989 / 156 / 6 | 111 | 25 | 92 | 0 |
| tsx | 55 | 54 | 1 | 0 | 184 | 178 | 178 | 178 | 178 / 0 / 0 | 6 | 7 | 2 | 0 |
| typescript | 55 | 54 | 1 | 0 | 449 | 382 | 382 | 382 | 382 / 0 / 0 | 67 | 59 | 44 | 0 |
| **total** | 689 | 594 | 95 | 9190 | 7361 | 6705 | 6050 | 6437 | 5473 / 1166 / 66 | 656 | 412 | 405 | 90 |

| language | imports OXIDE | exact | OXIDE-only | CG-only | calls OXIDE | exact | OXIDE-only | CG-only (calls) | CG-only (instantiates) | callee names exact / OXIDE | bases OXIDE | exact | OXIDE-only | CG-only | CG non-identifier rel. names |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|--:|--:|--:|--:|--:|
| c | 43 | 43 | 0 | 0 | 272 | 269 | 3 | 24 | 0 | 181 / 181 | 1 | 0 | 1 | 4 | 0 |
| cpp | 144 | 144 | 0 | 0 | 761 | 752 | 9 | 79 | 36 | 349 / 352 | 4 | 4 | 0 | 4 | 0 |
| go | 520 | 520 | 0 | 0 | 5977 | 5971 | 6 | 47 | 464 | 2056 / 2060 | 29 | 10 | 19 | 0 | 12 |
| java | 694 | 694 | 0 | 0 | 2434 | 2165 | 269 | 14 | 4 | 1287 / 1439 | 72 | 68 | 4 | 34 | 3 |
| javascript | 138 | 137 | 1 | 0 | 680 | 529 | 151 | 103 | 59 | 469 / 499 | 4 | 0 | 4 | 0 | 13 |
| markdown | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 / 0 | 0 | 0 | 0 | 0 | 0 |
| php | 2 | 2 | 0 | 0 | 4 | 4 | 0 | 0 | 1 | 4 / 4 | 5 | 5 | 0 | 1 | 0 |
| python | 409 | 370 | 39 | 0 | 3074 | 2557 | 517 | 269 | 0 | 1303 / 1347 | 123 | 123 | 0 | 0 | 24 |
| ruby | 5 | 5 | 0 | 0 | 10 | 9 | 1 | 1 | 0 | 9 / 10 | 3 | 3 | 0 | 0 | 0 |
| rust | 777 | 718 | 59 | 0 | 2769 | 2491 | 278 | 16 | 159 | 1346 / 1522 | 247 | 205 | 42 | 22 | 26 |
| tsx | 217 | 217 | 0 | 0 | 410 | 290 | 120 | 6 | 8 | 238 / 348 | 2 | 2 | 0 | 0 | 2 |
| typescript | 143 | 137 | 6 | 0 | 1129 | 992 | 137 | 85 | 72 | 752 / 758 | 14 | 14 | 0 | 0 | 1 |
| **total** | 3092 | 2987 | 105 | 0 | 17520 | 16029 | 1491 | 644 | 803 | 7994 / 8520 | 504 | 434 | 70 | 65 | 81 |

## Output differences — C/C++ after CodeGraph's own TS preParse (sensitivity)

| language | files | compared | CG failed | OXIDE syms on CG-failed | OXIDE syms | matched | exact span | kind agree | qn exact / suffix / differs | OXIDE-only | CG-only (comparable kinds) | CG-only (schema-extra) | CG namespace |
|---|--:|--:|--:|--:|--:|--:|--:|--:|---|--:|--:|--:|--:|
| c | 65 | 40 | 25 | 2817 | 768 | 452 | 440 | 386 | 452 / 0 / 0 | 316 | 20 | 1 | 0 |
| cpp | 73 | 45 | 28 | 5536 | 556 | 466 | 449 | 354 | 404 / 46 / 16 | 90 | 52 | 186 | 0 |
| go | 101 | 100 | 1 | 1 | 1989 | 1724 | 1643 | 1630 | 1660 / 64 / 0 | 265 | 1 | 3 | 0 |
| java | 92 | 91 | 1 | 2 | 1021 | 966 | 966 | 965 | 0 / 923 / 43 | 55 | 218 | 235 | 90 |
| javascript | 60 | 59 | 1 | 0 | 273 | 230 | 230 | 230 | 221 / 3 / 6 | 43 | 54 | 0 | 0 |
| markdown | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 / 0 / 0 | 0 | 0 | 0 | 0 |
| php | 6 | 5 | 1 | 1 | 24 | 24 | 24 | 22 | 5 / 19 / 0 | 0 | 0 | 2 | 0 |
| python | 77 | 76 | 1 | 1 | 1646 | 1646 | 1072 | 1544 | 1646 / 0 / 0 | 0 | 21 | 6 | 0 |
| ruby | 5 | 4 | 1 | 0 | 19 | 17 | 17 | 14 | 14 / 1 / 2 | 2 | 1 | 0 | 0 |
| rust | 99 | 98 | 1 | 2 | 1262 | 1151 | 1151 | 1148 | 989 / 156 / 6 | 111 | 25 | 92 | 0 |
| tsx | 55 | 54 | 1 | 0 | 184 | 178 | 178 | 178 | 178 / 0 / 0 | 6 | 7 | 2 | 0 |
| typescript | 55 | 54 | 1 | 0 | 449 | 382 | 382 | 382 | 382 / 0 / 0 | 67 | 59 | 44 | 0 |
| **total** | 689 | 626 | 63 | 8360 | 8191 | 7236 | 6552 | 6853 | 5951 / 1212 / 73 | 955 | 458 | 571 | 90 |

| language | imports OXIDE | exact | OXIDE-only | CG-only | calls OXIDE | exact | OXIDE-only | CG-only (calls) | CG-only (instantiates) | callee names exact / OXIDE | bases OXIDE | exact | OXIDE-only | CG-only | CG non-identifier rel. names |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|--:|--:|--:|--:|--:|
| c | 112 | 112 | 0 | 0 | 1074 | 1046 | 28 | 67 | 0 | 537 / 542 | 1 | 0 | 1 | 20 | 2 |
| cpp | 175 | 175 | 0 | 0 | 1300 | 1269 | 31 | 231 | 50 | 521 / 529 | 8 | 7 | 1 | 13 | 0 |
| go | 520 | 520 | 0 | 0 | 5977 | 5971 | 6 | 47 | 464 | 2056 / 2060 | 29 | 10 | 19 | 0 | 12 |
| java | 694 | 694 | 0 | 0 | 2434 | 2165 | 269 | 14 | 4 | 1287 / 1439 | 72 | 68 | 4 | 34 | 3 |
| javascript | 138 | 137 | 1 | 0 | 680 | 529 | 151 | 103 | 59 | 469 / 499 | 4 | 0 | 4 | 0 | 13 |
| markdown | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 / 0 | 0 | 0 | 0 | 0 | 0 |
| php | 2 | 2 | 0 | 0 | 4 | 4 | 0 | 0 | 1 | 4 / 4 | 5 | 5 | 0 | 1 | 0 |
| python | 409 | 370 | 39 | 0 | 3074 | 2557 | 517 | 269 | 0 | 1303 / 1347 | 123 | 123 | 0 | 0 | 24 |
| ruby | 5 | 5 | 0 | 0 | 10 | 9 | 1 | 1 | 0 | 9 / 10 | 3 | 3 | 0 | 0 | 0 |
| rust | 777 | 718 | 59 | 0 | 2769 | 2491 | 278 | 16 | 159 | 1346 / 1522 | 247 | 205 | 42 | 22 | 26 |
| tsx | 217 | 217 | 0 | 0 | 410 | 290 | 120 | 6 | 8 | 238 / 348 | 2 | 2 | 0 | 0 | 2 |
| typescript | 143 | 137 | 6 | 0 | 1129 | 992 | 137 | 85 | 72 | 752 / 758 | 14 | 14 | 0 | 0 | 1 |
| **total** | 3192 | 3087 | 105 | 0 | 18861 | 17323 | 1538 | 839 | 817 | 8522 / 9058 | 508 | 437 | 71 | 90 | 83 |

