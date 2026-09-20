tasks: 70 | loss partition @10: route 13, ordering 32, allocation 4, hit-in-pack 21

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| production RRF (K=60, 0.6/0.4) | 0.218 | 0.285 | 0.415 | 0.167 | 0.167 |
| lexical only | 0.335 | 0.384 | 0.472 | 0.275 | 0.295 |
| semantic only | 0.067 | 0.074 | 0.111 | 0.038 | 0.038 |
| RRF K=10 | 0.278 | 0.359 | 0.472 | 0.218 | 0.215 |
| RRF K=20 | 0.271 | 0.343 | 0.465 | 0.203 | 0.195 |
| RRF K=40 | 0.246 | 0.321 | 0.469 | 0.184 | 0.176 |
| RRF K=60 | 0.218 | 0.285 | 0.415 | 0.167 | 0.167 |
| RRF K=100 | 0.146 | 0.264 | 0.367 | 0.144 | 0.144 |
| RRF K=200 | 0.141 | 0.193 | 0.310 | 0.118 | 0.132 |
| RRF w_lex=0.3 | 0.099 | 0.127 | 0.180 | 0.072 | 0.080 |
| RRF w_lex=0.4 | 0.106 | 0.134 | 0.222 | 0.086 | 0.104 |
| RRF w_lex=0.5 | 0.127 | 0.246 | 0.373 | 0.131 | 0.133 |
| RRF w_lex=0.6 | 0.218 | 0.285 | 0.415 | 0.167 | 0.167 |
| RRF w_lex=0.7 | 0.246 | 0.334 | 0.472 | 0.190 | 0.178 |
| RRF w_lex=0.8 | 0.307 | 0.353 | 0.479 | 0.210 | 0.200 |
| minmax CombSUM 0.6/0.4 | 0.283 | 0.340 | 0.453 | 0.222 | 0.234 |
| minmax CombSUM 0.5/0.5 | 0.212 | 0.321 | 0.387 | 0.176 | 0.171 |
| minmax CombMNZ | 0.203 | 0.302 | 0.395 | 0.173 | 0.172 |
| zscore CombSUM 0.6/0.4 | 0.297 | 0.352 | 0.461 | 0.235 | 0.249 |
| zscore CombSUM 0.5/0.5 | 0.247 | 0.338 | 0.433 | 0.214 | 0.222 |
| RRF + evidence rerank top20 beta=0.25 | 0.218 | 0.301 | 0.415 | 0.168 | 0.160 |
| RRF + evidence rerank top20 beta=0.5 | 0.222 | 0.301 | 0.415 | 0.172 | 0.171 |
| RRF + evidence rerank top20 beta=1.0 | 0.222 | 0.301 | 0.415 | 0.169 | 0.167 |
| RRF + evidence rerank top10 beta=0.5 | 0.218 | 0.285 | 0.415 | 0.161 | 0.155 |
| production expanded (search default) | 0.218 | 0.285 | 0.415 | 0.167 | 0.167 |

production pack: gold-in-pack 0.300, mean used tokens 1338, relevant tokens per 1k pack tokens 71.2, relevant items/pack 0.33 of 7.1
