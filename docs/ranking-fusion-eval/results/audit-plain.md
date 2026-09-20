candidate rows: 24693 (106 gold, base rate 0.0043)

| signal | AUC for gold | mean gold | mean non-gold |
| --- | ---: | ---: | ---: |
| lex_rank | 0.932 | 21.255 | 143.674 |
| sem_rank | 0.700 | 82.915 | 143.279 |
| in_both | 0.770 | 0.670 | 0.128 |
| lex_score | 0.839 | 67.958 | 18.271 |
| sem_score | 0.613 | 0.487 | 0.374 |
| rrf60 | 0.920 | 0.012 | 0.004 |
| rrf10 | 0.925 | 0.049 | 0.008 |
| name_in_query | 0.680 | 0.566 | 0.206 |
| name_cov | 0.709 | 0.317 | 0.069 |
| is_module | 0.640 | 0.000 | 0.280 |
| is_test | 0.672 | 0.302 | 0.646 |
| is_method | 0.425 | 0.425 | 0.575 |
| struct_any | 0.662 | 0.349 | 0.025 |
| rel_uses | 0.553 | 0.132 | 0.009 |
| rel_imported-definition | 0.500 | 0.000 | 0.000 |
| rel_child | 0.530 | 0.066 | 0.006 |
| rel_parent | 0.518 | 0.038 | 0.002 |
| rel_sibling | 0.575 | 0.198 | 0.011 |
| rel_test | 0.498 | 0.000 | 0.003 |

correlation matrix (double-counting check):
| | lex_rank | sem_rank | in_both | name_in_query | name_cov | struct_any | rrf60 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| lex_rank | 1.00 | -0.23 | -0.40 | -0.12 | -0.12 | -0.12 | -0.78 |
| sem_rank | -0.23 | 1.00 | -0.41 | 0.00 | -0.03 | -0.10 | -0.37 |
| in_both | -0.40 | -0.41 | 1.00 | 0.11 | 0.13 | 0.18 | 0.70 |
| name_in_query | -0.12 | 0.00 | 0.11 | 1.00 | 0.85 | 0.07 | 0.13 |
| name_cov | -0.12 | -0.03 | 0.13 | 0.85 | 1.00 | 0.09 | 0.15 |
| struct_any | -0.12 | -0.10 | 0.18 | 0.07 | 0.09 | 1.00 | 0.20 |
| rrf60 | -0.78 | -0.37 | 0.70 | 0.13 | 0.15 | 0.20 | 1.00 |

structural neighbor precision by relation (top-3 K=60 seeds):
| relation | neighbors | gold | precision |
| --- | ---: | ---: | ---: |
| uses | 3298 | 14 | 0.004 |
| imported-definition | 1 | 0 | 0.000 |
| child | 204 | 7 | 0.034 |
| parent | 52 | 4 | 0.077 |
| sibling | 547 | 21 | 0.038 |
| test | 292 | 0 | 0.000 |

seed correctness by confidence: both: 35/206 gold (0.17), lex: 0/4 gold (0.00)

neighbor precision by (relation, seed confidence, seed correctness):
  ('child', 'both', 'seed-gold'): 2/13 = 0.154
  ('child', 'both', 'seed-nongold'): 5/191 = 0.026
  ('parent', 'both', 'seed-gold'): 1/15 = 0.067
  ('parent', 'both', 'seed-nongold'): 3/35 = 0.086
  ('sibling', 'both', 'seed-gold'): 18/174 = 0.103
  ('sibling', 'both', 'seed-nongold'): 3/361 = 0.008
  ('sibling', 'lex', 'seed-nongold'): 0/12 = 0.000
  ('test', 'both', 'seed-gold'): 0/49 = 0.000
  ('test', 'both', 'seed-nongold'): 0/237 = 0.000
  ('uses', 'both', 'seed-gold'): 1/441 = 0.002
  ('uses', 'both', 'seed-nongold'): 13/2790 = 0.005
  ('uses', 'lex', 'seed-nongold'): 0/67 = 0.000

flat bonus autopsy (β=0.5, top-20): tasks 70, candidates promoted into top-10 53 (gold: 5), gold displaced out of top-10 2
