ContextBench pinned instances scored: 21 of 21 pinned
| variant | file.coverage | file.coverage@5 | file.precision | line.coverage | line.precision | span.coverage | span.precision | symbol.coverage | symbol.coverage@5 | symbol.precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RRF K=60 (production) | 0.685 | 0.591 | 0.237 | 0.713 | 0.044 | 0.784 | 0.048 | 0.771 | 0.670 | 0.051 |
| RRF K=10 | 0.647 | 0.516 | 0.220 | 0.597 | 0.042 | 0.661 | 0.044 | 0.689 | 0.563 | 0.045 |
| K=60 + rerank | 0.655 | 0.468 | 0.201 | 0.621 | 0.029 | 0.690 | 0.031 | 0.718 | 0.520 | 0.034 |
| K=10 + rerank | 0.631 | 0.468 | 0.195 | 0.604 | 0.033 | 0.674 | 0.035 | 0.699 | 0.520 | 0.041 |
| K=10 + rerank (no struct) | 0.631 | 0.444 | 0.193 | 0.604 | 0.033 | 0.674 | 0.034 | 0.699 | 0.479 | 0.041 |
| lexical only | 0.560 | 0.504 | 0.182 | 0.555 | 0.027 | 0.618 | 0.029 | 0.622 | 0.579 | 0.037 |
| semantic only | 0.619 | 0.548 | 0.217 | 0.424 | 0.077 | 0.483 | 0.081 | 0.495 | 0.397 | 0.078 |

paired deltas vs K=60 with 95% bootstrap CI (n=21):
  RRF K=10                     file.coverage      -0.039 [-0.118, +0.036]  wins/losses 1/4
  RRF K=10                     symbol.coverage    -0.081 [-0.202, +0.013]  wins/losses 1/4
  RRF K=10                     file.coverage@5    -0.075 [-0.194, +0.024]  wins/losses 1/5
  K=60 + rerank                file.coverage      -0.031 [-0.190, +0.119]  wins/losses 2/4
  K=60 + rerank                symbol.coverage    -0.053 [-0.183, +0.056]  wins/losses 2/4
  K=60 + rerank                file.coverage@5    -0.123 [-0.274, +0.000]  wins/losses 1/6
  K=10 + rerank                file.coverage      -0.054 [-0.214, +0.105]  wins/losses 2/6
  K=10 + rerank                symbol.coverage    -0.071 [-0.202, +0.040]  wins/losses 1/6
  K=10 + rerank                file.coverage@5    -0.123 [-0.274, +0.000]  wins/losses 1/6
  K=10 + rerank (no struct)    file.coverage      -0.054 [-0.214, +0.105]  wins/losses 2/6
  K=10 + rerank (no struct)    symbol.coverage    -0.071 [-0.202, +0.040]  wins/losses 1/6
  K=10 + rerank (no struct)    file.coverage@5    -0.147 [-0.306, -0.016]  wins/losses 1/7
  lexical only                 file.coverage      -0.126 [-0.276, -0.006]  wins/losses 1/6
  lexical only                 symbol.coverage    -0.148 [-0.333, +0.007]  wins/losses 2/6
  lexical only                 file.coverage@5    -0.087 [-0.206, +0.016]  wins/losses 1/6
  semantic only                file.coverage      -0.066 [-0.152, +0.008]  wins/losses 1/5
  semantic only                symbol.coverage    -0.276 [-0.479, -0.076]  wins/losses 2/11
  semantic only                file.coverage@5    -0.044 [-0.183, +0.083]  wins/losses 3/5
