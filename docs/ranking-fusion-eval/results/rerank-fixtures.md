tasks: 11 | repos: ['py', 'ts']

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR | ΔnDCG@10 vs K=60 [95% CI] | macro R@10 (repo-balanced) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RRF K=60 (production) | 0.909 | 1.000 | 1.000 | 0.893 | 0.886 | +0.000 [+0.000, +0.000] | 1.000 |
| RRF K=10 | 0.909 | 1.000 | 1.000 | 0.899 | 0.894 | +0.005 [-0.002, +0.019] | 1.000 |
| K=60 + rerank | 0.909 | 0.955 | 1.000 | 0.868 | 0.894 | -0.025 [-0.099, +0.019] | 0.950 |
| K=10 + rerank | 0.909 | 0.955 | 1.000 | 0.862 | 0.886 | -0.032 [-0.101, +0.006] | 0.950 |
| K=10 + rerank (no struct) | 0.818 | 0.909 | 1.000 | 0.801 | 0.778 | -0.093 [-0.189, -0.005] | 0.900 |
| K=10 + rerank (no in_both) | 0.909 | 0.955 | 1.000 | 0.862 | 0.886 | -0.032 [-0.101, +0.006] | 0.950 |
| K=10 + rerank (no sem) | 0.909 | 0.955 | 1.000 | 0.868 | 0.894 | -0.025 [-0.099, +0.019] | 0.950 |
| K=10 + rerank top10 | 0.909 | 1.000 | 1.000 | 0.880 | 0.886 | -0.014 [-0.101, +0.059] | 1.000 |
| lexical only | 0.818 | 1.000 | 1.000 | 0.851 | 0.833 | -0.042 [-0.114, +0.000] | 1.000 |

hard negatives (b): same-parent non-gold siblings in top-50 — fraction ranked above the gold symbol
  RRF K=60 (production): n=8 above-gold 0.00
  RRF K=10: n=8 above-gold 0.00
  K=60 + rerank: n=8 above-gold 0.00
  K=10 + rerank: n=8 above-gold 0.00
  K=10 + rerank (no struct): n=8 above-gold 0.00
  K=10 + rerank (no in_both): n=8 above-gold 0.00
  K=10 + rerank (no sem): n=8 above-gold 0.00
  K=10 + rerank top10: n=8 above-gold 0.00
  lexical only: n=5 above-gold 0.00
