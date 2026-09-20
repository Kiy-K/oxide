tasks: 73 | loss partition @10: route 13, ordering 33, allocation 5, hit-in-pack 22

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| production RRF (K=60, 0.6/0.4) | 0.222 | 0.301 | 0.426 | 0.178 | 0.177 |
| lexical only | 0.363 | 0.410 | 0.493 | 0.298 | 0.314 |
| semantic only | 0.064 | 0.071 | 0.107 | 0.037 | 0.036 |
| RRF K=10 | 0.294 | 0.386 | 0.493 | 0.236 | 0.229 |
| RRF K=20 | 0.287 | 0.356 | 0.487 | 0.215 | 0.206 |
| RRF K=40 | 0.263 | 0.335 | 0.491 | 0.195 | 0.185 |
| RRF K=60 | 0.222 | 0.301 | 0.426 | 0.178 | 0.177 |
| RRF K=100 | 0.154 | 0.267 | 0.379 | 0.146 | 0.147 |
| RRF K=200 | 0.149 | 0.198 | 0.324 | 0.120 | 0.132 |
| RRF w_lex=0.3 | 0.095 | 0.122 | 0.173 | 0.069 | 0.077 |
| RRF w_lex=0.4 | 0.101 | 0.129 | 0.213 | 0.083 | 0.101 |
| RRF w_lex=0.5 | 0.136 | 0.250 | 0.385 | 0.136 | 0.140 |
| RRF w_lex=0.6 | 0.222 | 0.301 | 0.426 | 0.178 | 0.177 |
| RRF w_lex=0.7 | 0.263 | 0.348 | 0.493 | 0.201 | 0.188 |
| RRF w_lex=0.8 | 0.322 | 0.366 | 0.500 | 0.221 | 0.210 |
| minmax CombSUM 0.6/0.4 | 0.299 | 0.367 | 0.475 | 0.245 | 0.253 |
| minmax CombSUM 0.5/0.5 | 0.230 | 0.335 | 0.412 | 0.214 | 0.218 |
| minmax CombMNZ | 0.209 | 0.317 | 0.406 | 0.185 | 0.181 |
| zscore CombSUM 0.6/0.4 | 0.326 | 0.379 | 0.483 | 0.259 | 0.269 |
| zscore CombSUM 0.5/0.5 | 0.265 | 0.365 | 0.456 | 0.237 | 0.242 |
| RRF + evidence rerank top20 beta=0.25 | 0.222 | 0.316 | 0.426 | 0.179 | 0.170 |
| RRF + evidence rerank top20 beta=0.5 | 0.227 | 0.316 | 0.426 | 0.183 | 0.180 |
| RRF + evidence rerank top20 beta=1.0 | 0.227 | 0.316 | 0.426 | 0.180 | 0.177 |
| RRF + evidence rerank top10 beta=0.5 | 0.222 | 0.301 | 0.426 | 0.172 | 0.164 |
| production expanded (search default) | 0.222 | 0.301 | 0.426 | 0.178 | 0.177 |

production pack: gold-in-pack 0.301, mean used tokens 1317, relevant tokens per 1k pack tokens 73.1, relevant items/pack 0.33 of 7.1
