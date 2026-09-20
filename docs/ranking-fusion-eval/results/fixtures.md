tasks: 11 | loss partition @10: route 0, ordering 0, allocation 1, hit-in-pack 10

| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| production RRF (K=60, 0.6/0.4) | 0.909 | 1.000 | 1.000 | 0.893 | 0.886 |
| lexical only | 0.818 | 1.000 | 1.000 | 0.851 | 0.833 |
| semantic only | 0.818 | 0.955 | 1.000 | 0.818 | 0.806 |
| RRF K=10 | 0.909 | 1.000 | 1.000 | 0.899 | 0.894 |
| RRF K=20 | 0.909 | 1.000 | 1.000 | 0.893 | 0.886 |
| RRF K=40 | 0.909 | 1.000 | 1.000 | 0.893 | 0.886 |
| RRF K=60 | 0.909 | 1.000 | 1.000 | 0.893 | 0.886 |
| RRF K=100 | 0.909 | 1.000 | 1.000 | 0.893 | 0.886 |
| RRF K=200 | 0.909 | 1.000 | 1.000 | 0.893 | 0.886 |
| RRF w_lex=0.3 | 0.909 | 1.000 | 1.000 | 0.865 | 0.841 |
| RRF w_lex=0.4 | 0.909 | 1.000 | 1.000 | 0.865 | 0.841 |
| RRF w_lex=0.5 | 0.909 | 1.000 | 1.000 | 0.910 | 0.909 |
| RRF w_lex=0.6 | 0.909 | 1.000 | 1.000 | 0.893 | 0.886 |
| RRF w_lex=0.7 | 0.818 | 1.000 | 1.000 | 0.887 | 0.879 |
| RRF w_lex=0.8 | 0.818 | 1.000 | 1.000 | 0.887 | 0.879 |
| minmax CombSUM 0.6/0.4 | 0.909 | 1.000 | 1.000 | 0.878 | 0.864 |
| minmax CombSUM 0.5/0.5 | 0.909 | 1.000 | 1.000 | 0.904 | 0.894 |
| minmax CombMNZ | 0.909 | 1.000 | 1.000 | 0.878 | 0.864 |
| zscore CombSUM 0.6/0.4 | 0.818 | 1.000 | 1.000 | 0.815 | 0.783 |
| zscore CombSUM 0.5/0.5 | 0.818 | 1.000 | 1.000 | 0.874 | 0.859 |
| RRF + evidence rerank top20 beta=0.25 | 0.909 | 1.000 | 1.000 | 0.648 | 0.552 |
| RRF + evidence rerank top20 beta=0.5 | 0.818 | 1.000 | 1.000 | 0.642 | 0.544 |
| RRF + evidence rerank top20 beta=1.0 | 0.818 | 1.000 | 1.000 | 0.635 | 0.536 |
| RRF + evidence rerank top10 beta=0.5 | 0.955 | 1.000 | 1.000 | 0.684 | 0.591 |
| production expanded (search default) | 0.909 | 1.000 | 1.000 | 0.893 | 0.886 |

production pack: gold-in-pack 0.909, mean used tokens 579, relevant tokens per 1k pack tokens 210.7, relevant items/pack 1.09 of 6.6
