candidate rows: 25417 (86 gold, base rate 0.0034)

| signal | AUC for gold | mean gold | mean non-gold |
| --- | ---: | ---: | ---: |
| lex_rank | 0.823 | 53.791 | 145.077 |
| sem_rank | 0.507 | 135.581 | 144.674 |
| in_both | 0.620 | 0.337 | 0.098 |
| lex_score | 0.773 | 45.804 | 14.509 |
| sem_score | 0.470 | 0.306 | 0.355 |
| rrf60 | 0.826 | 0.009 | 0.004 |
| rrf10 | 0.832 | 0.033 | 0.008 |
| name_in_query | 0.412 | 0.000 | 0.178 |
| name_cov | 0.412 | 0.000 | 0.058 |
| is_module | 0.645 | 0.000 | 0.290 |
| is_test | 0.669 | 0.302 | 0.643 |
| is_method | 0.410 | 0.395 | 0.575 |
| struct_any | 0.576 | 0.174 | 0.022 |
| rel_uses | 0.543 | 0.093 | 0.008 |
| rel_imported-definition | 0.500 | 0.000 | 0.000 |
| rel_child | 0.515 | 0.035 | 0.006 |
| rel_parent | 0.499 | 0.000 | 0.002 |
| rel_sibling | 0.531 | 0.070 | 0.008 |
| rel_test | 0.499 | 0.000 | 0.002 |

correlation matrix (double-counting check):
| | lex_rank | sem_rank | in_both | name_in_query | name_cov | struct_any | rrf60 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| lex_rank | 1.00 | -0.35 | -0.33 | -0.09 | -0.07 | -0.07 | -0.76 |
| sem_rank | -0.35 | 1.00 | -0.34 | 0.01 | -0.02 | -0.08 | -0.27 |
| in_both | -0.33 | -0.34 | 1.00 | 0.08 | 0.09 | 0.15 | 0.64 |
| name_in_query | -0.09 | 0.01 | 0.08 | 1.00 | 0.84 | 0.04 | 0.10 |
| name_cov | -0.07 | -0.02 | 0.09 | 0.84 | 1.00 | 0.06 | 0.10 |
| struct_any | -0.07 | -0.08 | 0.15 | 0.04 | 0.06 | 1.00 | 0.14 |
| rrf60 | -0.76 | -0.27 | 0.64 | 0.10 | 0.10 | 0.14 | 1.00 |

structural neighbor precision by relation (top-3 K=60 seeds):
| relation | neighbors | gold | precision |
| --- | ---: | ---: | ---: |
| uses | 3282 | 10 | 0.003 |
| imported-definition | 5 | 0 | 0.000 |
| child | 291 | 3 | 0.010 |
| parent | 41 | 0 | 0.000 |
| sibling | 520 | 6 | 0.012 |
| test | 222 | 0 | 0.000 |

seed correctness by confidence: both: 9/190 gold (0.05), lex: 3/20 gold (0.15)

neighbor precision by (relation, seed confidence, seed correctness):
  ('child', 'both', 'seed-nongold'): 1/241 = 0.004
  ('child', 'lex', 'seed-nongold'): 1/42 = 0.024
  ('parent', 'both', 'seed-nongold'): 0/33 = 0.000
  ('sibling', 'both', 'seed-gold'): 3/32 = 0.094
  ('sibling', 'both', 'seed-nongold'): 1/438 = 0.002
  ('sibling', 'lex', 'seed-gold'): 2/19 = 0.105
  ('sibling', 'lex', 'seed-nongold'): 0/31 = 0.000
  ('test', 'both', 'seed-gold'): 0/13 = 0.000
  ('test', 'both', 'seed-nongold'): 0/203 = 0.000
  ('uses', 'both', 'seed-gold'): 1/128 = 0.008
  ('uses', 'both', 'seed-nongold'): 9/2802 = 0.003
  ('uses', 'lex', 'seed-gold'): 0/51 = 0.000
  ('uses', 'lex', 'seed-nongold'): 0/301 = 0.000

flat bonus autopsy (β=0.5, top-20): tasks 70, candidates promoted into top-10 43 (gold: 3), gold displaced out of top-10 1
