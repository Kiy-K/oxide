tasks: 70 | repos: ['flask', 'pylint', 'pytest', 'requests', 'zod']

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR | ΔnDCG@10 vs K=60 [95% CI] | macro R@10 (repo-balanced) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RRF K=60 (production) | 0.418 | 0.584 | 0.719 | 0.405 | 0.380 | +0.000 [+0.000, +0.000] | 0.415 |
| RRF K=10 | 0.522 | 0.663 | 0.747 | 0.461 | 0.420 | +0.055 [+0.024, +0.088] | 0.465 |
| K=60 + rerank | 0.483 | 0.661 | 0.719 | 0.466 | 0.451 | +0.061 [-0.023, +0.145] | 0.511 |
| K=10 + rerank | 0.497 | 0.680 | 0.747 | 0.475 | 0.457 | +0.070 [-0.012, +0.155] | 0.518 |
| K=10 + rerank (no struct) | 0.493 | 0.637 | 0.747 | 0.453 | 0.447 | +0.047 [-0.038, +0.134] | 0.481 |
| K=10 + rerank (no in_both) | 0.445 | 0.628 | 0.747 | 0.427 | 0.427 | +0.021 [-0.075, +0.118] | 0.682 |
| K=10 + rerank (no sem) | 0.555 | 0.677 | 0.747 | 0.480 | 0.451 | +0.075 [+0.019, +0.130] | 0.518 |
| K=10 + rerank top10 | 0.540 | 0.663 | 0.747 | 0.466 | 0.450 | +0.060 [-0.016, +0.139] | 0.465 |
| lexical only | 0.535 | 0.690 | 0.733 | 0.466 | 0.441 | +0.061 [-0.008, +0.134] | 0.708 |

hard negatives (a): judged-not-relevant lexical top-5 items — mean rank (capped 50; higher = better demotion), fraction still in top-5
  RRF K=60 (production): n=41 mean rank 10.4, in top-5 0.24
  RRF K=10: n=41 mean rank 4.8, in top-5 0.66
  K=60 + rerank: n=41 mean rank 6.3, in top-5 0.61
  K=10 + rerank: n=41 mean rank 4.4, in top-5 0.66
  K=10 + rerank (no struct): n=41 mean rank 4.3, in top-5 0.66
  K=10 + rerank (no in_both): n=41 mean rank 3.6, in top-5 0.90
  K=10 + rerank (no sem): n=41 mean rank 5.5, in top-5 0.61
  K=10 + rerank top10: n=41 mean rank 3.8, in top-5 0.76
  lexical only: n=41 mean rank 3.5, in top-5 1.00

hard negatives (b): same-parent non-gold siblings in top-50 — fraction ranked above the gold symbol
  RRF K=60 (production): n=37 above-gold 0.11
  RRF K=10: n=41 above-gold 0.07
  K=60 + rerank: n=37 above-gold 0.03
  K=10 + rerank: n=41 above-gold 0.02
  K=10 + rerank (no struct): n=41 above-gold 0.02
  K=10 + rerank (no in_both): n=41 above-gold 0.05
  K=10 + rerank (no sem): n=41 above-gold 0.02
  K=10 + rerank top10: n=41 above-gold 0.02
  lexical only: n=49 above-gold 0.04
