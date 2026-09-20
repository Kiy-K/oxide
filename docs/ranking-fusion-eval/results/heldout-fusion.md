tasks: 65 | loss partition @10: route 2, ordering 19, allocation 10, hit-in-pack 34

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| production RRF (K=60, 0.6/0.4) | 0.404 | 0.508 | 0.666 | 0.397 | 0.433 |
| lexical only | 0.348 | 0.485 | 0.603 | 0.343 | 0.368 |
| semantic only | 0.327 | 0.418 | 0.467 | 0.320 | 0.364 |
| RRF K=10 | 0.382 | 0.544 | 0.631 | 0.421 | 0.455 |
| RRF K=20 | 0.415 | 0.538 | 0.668 | 0.409 | 0.443 |
| RRF K=40 | 0.420 | 0.522 | 0.666 | 0.406 | 0.447 |
| RRF K=60 | 0.404 | 0.508 | 0.666 | 0.397 | 0.433 |
| RRF K=100 | 0.397 | 0.496 | 0.668 | 0.396 | 0.437 |
| RRF K=200 | 0.385 | 0.479 | 0.658 | 0.388 | 0.427 |
| RRF w_lex=0.3 | 0.388 | 0.463 | 0.568 | 0.367 | 0.406 |
| RRF w_lex=0.4 | 0.376 | 0.512 | 0.596 | 0.385 | 0.411 |
| RRF w_lex=0.5 | 0.407 | 0.496 | 0.665 | 0.386 | 0.417 |
| RRF w_lex=0.6 | 0.404 | 0.508 | 0.666 | 0.397 | 0.433 |
| RRF w_lex=0.7 | 0.414 | 0.527 | 0.642 | 0.404 | 0.449 |
| RRF w_lex=0.8 | 0.395 | 0.532 | 0.660 | 0.396 | 0.433 |
| minmax CombSUM 0.6/0.4 | 0.419 | 0.543 | 0.646 | 0.426 | 0.472 |
| minmax CombSUM 0.5/0.5 | 0.440 | 0.505 | 0.650 | 0.419 | 0.481 |
| minmax CombMNZ | 0.434 | 0.556 | 0.665 | 0.433 | 0.476 |
| zscore CombSUM 0.6/0.4 | 0.424 | 0.535 | 0.642 | 0.415 | 0.467 |
| zscore CombSUM 0.5/0.5 | 0.428 | 0.491 | 0.630 | 0.405 | 0.475 |
| RRF + evidence rerank top20 beta=0.25 | 0.419 | 0.519 | 0.666 | 0.385 | 0.417 |
| RRF + evidence rerank top20 beta=0.5 | 0.397 | 0.519 | 0.666 | 0.360 | 0.384 |
| RRF + evidence rerank top20 beta=1.0 | 0.384 | 0.512 | 0.666 | 0.343 | 0.366 |
| RRF + evidence rerank top10 beta=0.5 | 0.439 | 0.508 | 0.666 | 0.369 | 0.402 |
| production expanded (search default) | 0.404 | 0.508 | 0.666 | 0.397 | 0.433 |

production pack: gold-in-pack 0.538, mean used tokens 1399, relevant tokens per 1k pack tokens 109.4, relevant items/pack 0.68 of 6.5
