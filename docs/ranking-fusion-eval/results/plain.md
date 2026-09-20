tasks: 73 | loss partition @10: route 6, ordering 18, allocation 6, hit-in-pack 43

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| production RRF (K=60, 0.6/0.4) | 0.442 | 0.601 | 0.731 | 0.420 | 0.392 |
| lexical only | 0.555 | 0.703 | 0.744 | 0.483 | 0.457 |
| semantic only | 0.303 | 0.376 | 0.438 | 0.226 | 0.201 |
| RRF K=10 | 0.541 | 0.676 | 0.757 | 0.473 | 0.430 |
| RRF K=20 | 0.527 | 0.647 | 0.757 | 0.463 | 0.429 |
| RRF K=40 | 0.480 | 0.619 | 0.750 | 0.432 | 0.399 |
| RRF K=60 | 0.442 | 0.601 | 0.731 | 0.420 | 0.392 |
| RRF K=100 | 0.428 | 0.578 | 0.707 | 0.409 | 0.388 |
| RRF K=200 | 0.428 | 0.551 | 0.636 | 0.400 | 0.384 |
| RRF w_lex=0.3 | 0.401 | 0.474 | 0.566 | 0.352 | 0.344 |
| RRF w_lex=0.4 | 0.417 | 0.516 | 0.608 | 0.379 | 0.367 |
| RRF w_lex=0.5 | 0.444 | 0.554 | 0.667 | 0.397 | 0.382 |
| RRF w_lex=0.6 | 0.442 | 0.601 | 0.731 | 0.420 | 0.392 |
| RRF w_lex=0.7 | 0.501 | 0.630 | 0.753 | 0.454 | 0.427 |
| RRF w_lex=0.8 | 0.529 | 0.664 | 0.767 | 0.479 | 0.449 |
| minmax CombSUM 0.6/0.4 | 0.537 | 0.647 | 0.739 | 0.465 | 0.434 |
| minmax CombSUM 0.5/0.5 | 0.521 | 0.608 | 0.696 | 0.429 | 0.396 |
| minmax CombMNZ | 0.511 | 0.606 | 0.696 | 0.437 | 0.410 |
| zscore CombSUM 0.6/0.4 | 0.541 | 0.674 | 0.754 | 0.481 | 0.446 |
| zscore CombSUM 0.5/0.5 | 0.525 | 0.615 | 0.746 | 0.445 | 0.413 |
| RRF + evidence rerank top20 beta=0.25 | 0.478 | 0.632 | 0.731 | 0.435 | 0.403 |
| RRF + evidence rerank top20 beta=0.5 | 0.496 | 0.636 | 0.731 | 0.412 | 0.369 |
| RRF + evidence rerank top20 beta=1.0 | 0.473 | 0.636 | 0.731 | 0.394 | 0.345 |
| RRF + evidence rerank top10 beta=0.5 | 0.464 | 0.601 | 0.731 | 0.393 | 0.351 |
| production expanded (search default) | 0.442 | 0.601 | 0.731 | 0.420 | 0.392 |

production pack: gold-in-pack 0.603, mean used tokens 1206, relevant tokens per 1k pack tokens 148.2, relevant items/pack 0.71 of 6.9
