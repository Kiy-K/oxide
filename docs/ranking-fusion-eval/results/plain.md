tasks: 70 | loss partition @10: route 6, ordering 18, allocation 6, hit-in-pack 40

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| production RRF (K=60, 0.6/0.4) | 0.418 | 0.584 | 0.719 | 0.405 | 0.380 |
| lexical only | 0.535 | 0.690 | 0.733 | 0.466 | 0.441 |
| semantic only | 0.288 | 0.350 | 0.414 | 0.213 | 0.193 |
| RRF K=10 | 0.522 | 0.663 | 0.747 | 0.461 | 0.420 |
| RRF K=20 | 0.507 | 0.632 | 0.747 | 0.450 | 0.418 |
| RRF K=40 | 0.458 | 0.603 | 0.739 | 0.418 | 0.388 |
| RRF K=60 | 0.418 | 0.584 | 0.719 | 0.405 | 0.380 |
| RRF K=100 | 0.403 | 0.560 | 0.694 | 0.394 | 0.376 |
| RRF K=200 | 0.403 | 0.532 | 0.620 | 0.385 | 0.372 |
| RRF w_lex=0.3 | 0.375 | 0.452 | 0.548 | 0.336 | 0.333 |
| RRF w_lex=0.4 | 0.392 | 0.495 | 0.592 | 0.363 | 0.355 |
| RRF w_lex=0.5 | 0.420 | 0.535 | 0.667 | 0.382 | 0.370 |
| RRF w_lex=0.6 | 0.418 | 0.584 | 0.719 | 0.405 | 0.380 |
| RRF w_lex=0.7 | 0.480 | 0.614 | 0.742 | 0.436 | 0.410 |
| RRF w_lex=0.8 | 0.509 | 0.650 | 0.757 | 0.462 | 0.432 |
| minmax CombSUM 0.6/0.4 | 0.517 | 0.632 | 0.728 | 0.453 | 0.424 |
| minmax CombSUM 0.5/0.5 | 0.500 | 0.591 | 0.683 | 0.415 | 0.384 |
| minmax CombMNZ | 0.490 | 0.589 | 0.683 | 0.423 | 0.399 |
| zscore CombSUM 0.6/0.4 | 0.522 | 0.660 | 0.744 | 0.469 | 0.437 |
| zscore CombSUM 0.5/0.5 | 0.505 | 0.598 | 0.735 | 0.432 | 0.402 |
| RRF + evidence rerank top20 beta=0.25 | 0.456 | 0.616 | 0.719 | 0.426 | 0.397 |
| RRF + evidence rerank top20 beta=0.5 | 0.474 | 0.620 | 0.719 | 0.403 | 0.364 |
| RRF + evidence rerank top20 beta=1.0 | 0.464 | 0.620 | 0.719 | 0.386 | 0.341 |
| RRF + evidence rerank top10 beta=0.5 | 0.441 | 0.584 | 0.719 | 0.383 | 0.345 |
| production expanded (search default) | 0.418 | 0.584 | 0.719 | 0.405 | 0.380 |

production pack: gold-in-pack 0.586, mean used tokens 1225, relevant tokens per 1k pack tokens 143.6, relevant items/pack 0.70 of 7.0
