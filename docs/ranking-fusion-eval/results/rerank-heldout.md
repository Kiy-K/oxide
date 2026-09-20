tasks: 65 | repos: ['flask', 'httpx', 'pylint', 'pytest', 'requests', 'ripgrep', 'zod']

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR | ΔnDCG@10 vs K=60 [95% CI] | macro R@10 (repo-balanced) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RRF K=60 (production) | 0.404 | 0.508 | 0.666 | 0.397 | 0.433 | +0.000 [+0.000, +0.000] | 0.502 |
| RRF K=10 | 0.382 | 0.544 | 0.631 | 0.421 | 0.455 | +0.023 [-0.005, +0.056] | 0.523 |
| K=60 + rerank | 0.377 | 0.494 | 0.666 | 0.343 | 0.362 | -0.054 [-0.113, +0.005] | 0.481 |
| K=10 + rerank | 0.363 | 0.488 | 0.631 | 0.322 | 0.336 | -0.076 [-0.137, -0.013] | 0.477 |
| K=10 + rerank (no struct) | 0.314 | 0.457 | 0.631 | 0.287 | 0.297 | -0.110 [-0.182, -0.040] | 0.444 |
| K=10 + rerank (no in_both) | 0.336 | 0.454 | 0.631 | 0.281 | 0.295 | -0.116 [-0.184, -0.047] | 0.451 |
| K=10 + rerank (no sem) | 0.404 | 0.511 | 0.631 | 0.380 | 0.415 | -0.018 [-0.064, +0.029] | 0.501 |
| K=10 + rerank top10 | 0.355 | 0.544 | 0.631 | 0.345 | 0.339 | -0.052 [-0.106, +0.002] | 0.523 |
| lexical only | 0.348 | 0.485 | 0.603 | 0.343 | 0.368 | -0.054 [-0.110, -0.001] | 0.475 |

hard negatives (b): same-parent non-gold siblings in top-50 — fraction ranked above the gold symbol
  RRF K=60 (production): n=175 above-gold 0.27
  RRF K=10: n=163 above-gold 0.28
  K=60 + rerank: n=175 above-gold 0.26
  K=10 + rerank: n=163 above-gold 0.31
  K=10 + rerank (no struct): n=163 above-gold 0.31
  K=10 + rerank (no in_both): n=163 above-gold 0.31
  K=10 + rerank (no sem): n=163 above-gold 0.27
  K=10 + rerank top10: n=163 above-gold 0.29
  lexical only: n=134 above-gold 0.36
