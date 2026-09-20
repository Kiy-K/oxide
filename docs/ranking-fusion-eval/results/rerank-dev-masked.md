tasks: 70 | repos: ['flask', 'pylint', 'pytest', 'requests', 'zod']

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR | ΔnDCG@10 vs K=60 [95% CI] | macro R@10 (repo-balanced) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RRF K=60 (production) | 0.218 | 0.285 | 0.415 | 0.167 | 0.167 | +0.000 [+0.000, +0.000] | 0.217 |
| RRF K=10 | 0.278 | 0.359 | 0.472 | 0.218 | 0.215 | +0.051 [+0.006, +0.092] | 0.279 |
| K=60 + rerank | 0.293 | 0.365 | 0.415 | 0.255 | 0.272 | +0.088 [+0.033, +0.146] | 0.334 |
| K=10 + rerank | 0.297 | 0.399 | 0.472 | 0.267 | 0.278 | +0.100 [+0.043, +0.157] | 0.350 |
| K=10 + rerank (no struct) | 0.297 | 0.401 | 0.472 | 0.267 | 0.266 | +0.100 [+0.041, +0.158] | 0.352 |
| K=10 + rerank (no in_both) | 0.324 | 0.380 | 0.472 | 0.287 | 0.314 | +0.120 [+0.046, +0.192] | 0.538 |
| K=10 + rerank (no sem) | 0.326 | 0.368 | 0.472 | 0.243 | 0.251 | +0.076 [+0.031, +0.122] | 0.334 |
| K=10 + rerank top10 | 0.305 | 0.359 | 0.472 | 0.258 | 0.280 | +0.091 [+0.034, +0.148] | 0.279 |
| lexical only | 0.335 | 0.384 | 0.472 | 0.275 | 0.295 | +0.108 [+0.044, +0.167] | 0.532 |

hard negatives (b): same-parent non-gold siblings in top-50 — fraction ranked above the gold symbol
  RRF K=60 (production): n=13 above-gold 0.31
  RRF K=10: n=11 above-gold 0.18
  K=60 + rerank: n=13 above-gold 0.23
  K=10 + rerank: n=11 above-gold 0.18
  K=10 + rerank (no struct): n=11 above-gold 0.18
  K=10 + rerank (no in_both): n=11 above-gold 0.18
  K=10 + rerank (no sem): n=11 above-gold 0.18
  K=10 + rerank top10: n=11 above-gold 0.18
  lexical only: n=12 above-gold 0.08
