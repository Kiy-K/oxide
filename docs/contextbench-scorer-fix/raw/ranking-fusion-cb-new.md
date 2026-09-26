ContextBench pinned instances scored: 21 of 21 pinned
| variant | file.coverage | file.coverage@5 | file.precision | line.coverage | line.precision | span.coverage | span.precision | symbol.coverage | symbol.coverage@5 | symbol.precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RRF K=60 (production) | 0.685 | 0.591 | 0.237 | 0.731 | 0.051 | 0.757 | 0.055 | 0.748 | 0.631 | 0.068 |
| RRF K=10 | 0.647 | 0.516 | 0.220 | 0.615 | 0.054 | 0.634 | 0.056 | 0.667 | 0.523 | 0.062 |
| K=60 + rerank | 0.655 | 0.468 | 0.201 | 0.631 | 0.035 | 0.655 | 0.037 | 0.678 | 0.481 | 0.042 |
| K=10 + rerank | 0.631 | 0.468 | 0.195 | 0.614 | 0.039 | 0.639 | 0.041 | 0.660 | 0.481 | 0.050 |
| K=10 + rerank (no struct) | 0.631 | 0.444 | 0.193 | 0.617 | 0.040 | 0.641 | 0.042 | 0.663 | 0.439 | 0.053 |
| lexical only | 0.560 | 0.504 | 0.182 | 0.568 | 0.035 | 0.585 | 0.037 | 0.586 | 0.539 | 0.048 |
| semantic only | 0.619 | 0.548 | 0.217 | 0.424 | 0.077 | 0.435 | 0.081 | 0.447 | 0.349 | 0.078 |

paired deltas vs K=60 with 95% bootstrap CI (n=21):
  RRF K=10                     file.coverage      -0.039 [-0.118, +0.036]  wins/losses 1/4
  RRF K=10                     symbol.coverage    -0.081 [-0.202, +0.013]  wins/losses 1/4
  RRF K=10                     file.coverage@5    -0.075 [-0.194, +0.024]  wins/losses 1/5
  K=60 + rerank                file.coverage      -0.031 [-0.190, +0.119]  wins/losses 2/4
  K=60 + rerank                symbol.coverage    -0.070 [-0.201, +0.045]  wins/losses 2/5
  K=60 + rerank                file.coverage@5    -0.123 [-0.274, +0.000]  wins/losses 1/6
  K=10 + rerank                file.coverage      -0.054 [-0.214, +0.105]  wins/losses 2/6
  K=10 + rerank                symbol.coverage    -0.088 [-0.219, +0.027]  wins/losses 1/7
  K=10 + rerank                file.coverage@5    -0.123 [-0.274, +0.000]  wins/losses 1/6
  K=10 + rerank (no struct)    file.coverage      -0.054 [-0.214, +0.105]  wins/losses 2/6
  K=10 + rerank (no struct)    symbol.coverage    -0.085 [-0.214, +0.029]  wins/losses 1/7
  K=10 + rerank (no struct)    file.coverage@5    -0.147 [-0.306, -0.016]  wins/losses 1/7
  lexical only                 file.coverage      -0.126 [-0.276, -0.006]  wins/losses 1/6
  lexical only                 symbol.coverage    -0.162 [-0.341, -0.005]  wins/losses 2/7
  lexical only                 file.coverage@5    -0.087 [-0.206, +0.016]  wins/losses 1/6
  semantic only                file.coverage      -0.066 [-0.152, +0.008]  wins/losses 1/5
  semantic only                symbol.coverage    -0.301 [-0.496, -0.101]  wins/losses 2/12
  semantic only                file.coverage@5    -0.044 [-0.183, +0.083]  wins/losses 3/5
