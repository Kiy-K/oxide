## Oracle ladder (mean gold coverage; balanced, budget 4096)

| set | n | baseline | strict (exact key) | D alloc | B16 | B20 | B50 | B100 | B full | B chan16 | B∘D | C (dep role) | C (primary role) | C (top-seed score, primary) | C∘D | A pool | A+literal | A index | A index, full bodies |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | 70 | 0.534 | 0.493 | 0.759 | 0.691 | 0.706 | 0.779 | 0.791 | 0.836 | 0.729 | 0.910 | 0.592 | 0.534 | 0.605 | 0.816 | 0.910 | 0.924 | 1.000 | 0.995 |
| masked | 70 | 0.278 | 0.249 | 0.449 | 0.430 | 0.466 | 0.564 | 0.569 | 0.755 | 0.524 | 0.797 | 0.393 | 0.288 | 0.425 | 0.578 | 0.819 | 0.852 | 1.000 | 0.995 |
| heldout | 65 | 0.416 | 0.357 | 0.706 | 0.597 | 0.602 | 0.699 | 0.718 | 0.757 | 0.639 | 0.935 | 0.475 | 0.427 | 0.493 | 0.808 | 0.932 | 0.937 | 1.000 | 0.976 |
| cb | 21 | 0.208 | 0.208 | 0.354 | 0.255 | 0.264 | 0.328 | 0.342 | 0.362 | 0.271 | 0.574 | 0.328 | 0.188 | 0.356 | 0.535 | 0.577 | 0.577 | 0.691 | 0.899 |
| all | 226 | 0.391 | 0.352 | 0.610 | 0.543 | 0.561 | 0.647 | 0.660 | 0.744 | 0.597 | 0.851 | 0.472 | 0.395 | 0.494 | 0.714 | 0.857 | 0.873 | 0.971 | 0.980 |

Excluding known gold/corpus-mismatch lines (EVAL units removed, coverage renormalized; affects 4 CB tasks only):

| set | n | baseline | D | B full | C primary | A pool | A index |
|---|---:|---:|---:|---:|---:|---:|---:|
| cb | 21 | 0.213 | 0.366 | 0.371 | 0.193 | 0.594 | 0.713 |
| all | 226 | 0.391 | 0.611 | 0.745 | 0.395 | 0.859 | 0.973 |

Paired deltas vs baseline (bootstrap 95 % CI, 10k, seed 0), all 226 instances:

| arm | Δ coverage | CI |
|---|---:|---|
| D allocator oracle | +0.219 | [+0.175, +0.266] |
| B16 reorder within seed top-16 | +0.152 | [+0.110, +0.196] |
| B50 | +0.257 | [+0.206, +0.309] |
| B full | +0.354 | [+0.299, +0.410] |
| B chan16 (gold some channel had ≤16) | +0.207 | [+0.159, +0.256] |
| C dependency role | +0.082 | [+0.053, +0.113] |
| C primary role | +0.004 | [-0.006, +0.017] |
| C top-seed score, primary role | +0.103 | [+0.071, +0.139] |
| root tests/ as test role (diagnostic) | +0.018 | [-0.005, +0.041] |
| A pool + literal (route) | +0.483 | [+0.425, +0.539] |
| A pool | +0.466 | [+0.410, +0.523] |
| #25 canonical test predicate | +0.000 | [+0.000, +0.000] |

## Clean subset (label-uncertain lint-sweep tasks and later identical-gold duplicates removed)

Flagged: 23 instances (Counter({'dev': 11, 'masked': 11, 'heldout': 1})).

| set | n | baseline | D | B16 | B50 | B full | B chan16 | C primary | A pool | A+literal | EVAL | CANDGEN | ROUTE | STRUCT | FUSION | ALLOC |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | 59 | 0.608 | 0.830 | 0.755 | 0.842 | 0.888 | 0.800 | 0.608 | 0.960 | 0.960 | 0.0 % | 10.1 % | 0.0 % | 0.0 % | 40.7 % | 49.2 % |
| masked | 59 | 0.329 | 0.524 | 0.502 | 0.643 | 0.803 | 0.613 | 0.341 | 0.859 | 0.876 | 0.0 % | 19.7 % | 2.5 % | 5.1 % | 48.9 % | 23.7 % |
| heldout | 64 | 0.423 | 0.701 | 0.590 | 0.694 | 0.753 | 0.634 | 0.434 | 0.931 | 0.936 | 0.0 % | 8.3 % | 0.0 % | 4.7 % | 42.6 % | 44.4 % |
| cb | 21 | 0.208 | 0.354 | 0.255 | 0.328 | 0.362 | 0.271 | 0.188 | 0.577 | 0.577 | 4.1 % | 8.1 % | 0.0 % | 2.8 % | 37.1 % | 48.0 % |
| all | 203 | 0.427 | 0.651 | 0.578 | 0.685 | 0.766 | 0.638 | 0.432 | 0.882 | 0.888 | 0.6 % | 12.5 % | 0.9 % | 3.6 % | 43.6 % | 38.8 % |

## Failure taxonomy

Share of **lost gold weight** by stage (unit-weighted; each task sums to its uncovered fraction):

| set | n | lost weight | EVAL | CANDGEN | ROUTE | STRUCT | FUSION | ALLOC |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | 70 | 32.6 | 0.0 % | 16.4 % | 3.1 % | 1.0 % | 37.5 % | 42.0 % |
| masked | 70 | 50.6 | 0.0 % | 21.4 % | 5.9 % | 4.0 % | 49.2 % | 19.5 % |
| heldout | 65 | 37.9 | 0.0 % | 8.0 % | 0.0 % | 4.6 % | 41.5 % | 45.9 % |
| cb | 21 | 16.6 | 4.1 % | 8.1 % | 0.0 % | 2.8 % | 37.1 % | 48.0 % |
| natural (dev+heldout+cb) | 156 | 87.2 | 0.8 % | 11.2 % | 1.1 % | 2.9 % | 39.2 % | 44.8 % |

Task **primary** stage (≥ ⅔ of the task's lost weight in one stage, else MULTI):

| set | n | HIT | EVAL | CANDGEN | ROUTE | STRUCT | FUSION | ALLOC | MULTI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | 70 | 32 (46 %) | 0 (0 %) | 5 (7 %) | 1 (1 %) | 0 (0 %) | 12 (17 %) | 16 (23 %) | 4 (6 %) |
| masked | 70 | 17 (24 %) | 0 (0 %) | 10 (14 %) | 3 (4 %) | 1 (1 %) | 22 (31 %) | 8 (11 %) | 9 (13 %) |
| heldout | 65 | 20 (31 %) | 0 (0 %) | 1 (2 %) | 0 (0 %) | 1 (2 %) | 13 (20 %) | 20 (31 %) | 10 (15 %) |
| cb | 21 | 0 (0 %) | 0 (0 %) | 0 (0 %) | 0 (0 %) | 0 (0 %) | 3 (14 %) | 9 (43 %) | 9 (43 %) |
| natural | 156 | 52 (33 %) | 0 (0 %) | 6 (4 %) | 1 (1 %) | 1 (1 %) | 28 (18 %) | 45 (29 %) | 23 (15 %) |

Misses only (coverage < 1): 157 of 226; hard misses (coverage 0): 110.

| set | misses | EVAL | CANDGEN | ROUTE | STRUCT | FUSION | ALLOC | MULTI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | 38 | 0 % | 13 % | 3 % | 0 % | 32 % | 42 % | 11 % |
| masked | 53 | 0 % | 19 % | 6 % | 2 % | 42 % | 15 % | 17 % |
| heldout | 45 | 0 % | 2 % | 0 % | 2 % | 29 % | 44 % | 22 % |
| cb | 21 | 0 % | 0 % | 0 % | 0 % | 14 % | 43 % | 43 % |

Sub-reasons (lost weight, summed over tasks):

| stage | sub-reason | dev | masked | heldout | cb |
|---|---|---:|---:|---:|---:|
| FUSION | fused rank 101+; both channels ranked it >16 | 3.80 | 15.40 | 5.17 | 2.76 |
| ALLOC | beyond primary cap | 7.17 | 7.17 | 5.82 | 0.36 |
| CANDGEN | no channel/hop/literal reaches it | 5.33 | 10.80 | 3.05 | 1.20 |
| FUSION | fused rank 17-50; a channel ranked it <=16 (RRF pushed it down) | 3.45 | 5.62 | 4.37 | 0.49 |
| FUSION | fused rank 17-50; both channels ranked it >16 | 3.62 | 3.65 | 4.12 | 2.06 |
| ALLOC | per-file diversity cap | 3.37 | 1.20 | 6.40 | 2.24 |
| ALLOC | subsumed by overlapping symbol | 3.17 | 1.50 | 5.20 | 1.10 |
| ROUTE | literal channel (unrouted) finds it | 1.00 | 3.00 | 0.00 | 0.00 |
| FUSION | fused rank 51-100; both channels ranked it >16 | 1.37 | 0.00 | 1.39 | 0.77 |
| ALLOC | snippet window (per-item 350-token cap) | 0.00 | 0.00 | 0.00 | 3.44 |
| STRUCT | within expansion scope, cut by cap | 0.00 | 0.70 | 1.40 | 0.46 |
| STRUCT | outside expansion scope | 0.33 | 1.33 | 0.33 | 0.00 |
| FUSION | fused rank 51-100; a channel ranked it <=16 (RRF pushed it down) | 0.00 | 0.20 | 0.69 | 0.00 |
| ALLOC | module subsumed by concrete symbols | 0.00 | 0.00 | 0.00 | 0.85 |
| EVAL | gold file not indexed | 0.00 | 0.00 | 0.00 | 0.68 |
| CANDGEN | module-only bearer | 0.00 | 0.00 | 0.00 | 0.15 |
| FUSION | fused rank 101+; a channel ranked it <=16 (RRF pushed it down) | 0.00 | 0.00 | 0.00 | 0.09 |

## By query class

Natural sets (dev + heldout + cb); masked reported separately because it is the dev tasks with gold-name tokens removed.

| query class | n | baseline cov | lost wt | EVAL | CANDGEN | ROUTE | STRUCT | FUSION | ALLOC | D−base | B16−base | Bfull−base | CP−base | A+lit−A |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| natural: NL behavioral description | 50 | 0.380 | 31.0 | 0 % | 11 % | 0 % | 4 % | 40 % | 44 % | +0.286 | +0.194 | +0.374 | +0.004 | +0.000 |
| natural: callers/impact | 3 | 0.667 | 1.0 | 0 % | 100 % | 0 % | 0 % | 0 % | 0 % | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 |
| natural: exact identifier/symbol | 24 | 0.487 | 12.3 | 0 % | 19 % | 8 % | 2 % | 35 % | 36 % | +0.190 | +0.076 | +0.204 | +0.000 | +0.042 |
| natural: filename/path | 5 | 0.500 | 2.5 | 0 % | 16 % | 0 % | 0 % | 34 % | 50 % | +0.250 | +0.200 | +0.290 | +0.000 | +0.000 |
| natural: mixed/other (long text with identifiers) | 51 | 0.472 | 26.9 | 2 % | 7 % | 0 % | 3 % | 44 % | 45 % | +0.236 | +0.163 | +0.302 | -0.002 | +0.007 |
| natural: quoted literal/error text | 22 | 0.411 | 13.0 | 1 % | 5 % | 0 % | 3 % | 35 % | 55 % | +0.226 | +0.130 | +0.277 | +0.008 | +0.000 |
| natural: test discovery | 1 | 0.500 | 0.5 | 0 % | 0 % | 0 % | 0 % | 0 % | 100 % | +0.500 | +0.000 | +0.000 | +0.000 | +0.000 |
| masked: NL behavioral description | 26 | 0.194 | 21.0 | 0 % | 28 % | 0 % | 10 % | 44 % | 18 % | +0.156 | +0.162 | +0.502 | +0.008 | +0.000 |
| masked: callers/impact | 3 | 0.667 | 1.0 | 0 % | 100 % | 0 % | 0 % | 0 % | 0 % | +0.000 | +0.000 | +0.000 | -0.167 | +0.000 |
| masked: exact identifier/symbol | 6 | 0.000 | 6.0 | 0 % | 33 % | 17 % | 0 % | 50 % | 0 % | +0.000 | +0.000 | +0.500 | +0.000 | +0.167 |
| masked: filename/path | 3 | 0.333 | 2.0 | 0 % | 0 % | 0 % | 0 % | 50 % | 50 % | +0.333 | +0.333 | +0.667 | +0.000 | +0.000 |
| masked: implementation discovery (NL) | 2 | 0.000 | 2.0 | 0 % | 50 % | 0 % | 0 % | 50 % | 0 % | +0.000 | +0.000 | +0.333 | +0.000 | +0.000 |
| masked: mixed/other (long text with identifiers) | 21 | 0.400 | 12.6 | 0 % | 8 % | 16 % | 0 % | 40 % | 36 % | +0.281 | +0.214 | +0.414 | +0.048 | +0.063 |
| masked: quoted literal/error text | 9 | 0.333 | 6.0 | 0 % | 0 % | 0 % | 0 % | 92 % | 8 % | +0.111 | +0.111 | +0.667 | +0.000 | +0.000 |

## Channel recall / rank diagnostics

`literal` is a research-only channel (query-derived identifier/quoted patterns through `oxide::literal::search`); it is **not routed** into production fusion. `union` = best of lexical/semantic rank. Depth is each channel's own top-200 (literal: up to 200 hits per pattern, ≤ 8 patterns).

**dev (115 targets)** — target-level recall@K (targets: gold symbols; CB: innermost concrete symbol holding each gold line)

| channel | R@5 | R@10 | R@16 | R@50 | R@200 | absent from channel | median rank when present |
|---|---:|---:|---:|---:|---:|---:|---:|
| literal (any depth; ranks are discovery order, not relevance) | – | – | – | – | 0.487 | 51 % | – |
| lexical | 0.478 | 0.626 | 0.670 | 0.835 | 0.887 | 11 % | 4.0 |
| semantic | 0.252 | 0.313 | 0.357 | 0.470 | 0.652 | 35 % | 11 |
| union | 0.557 | 0.696 | 0.730 | 0.843 | 0.922 | 8 % | 3.0 |
| fused | 0.339 | 0.522 | 0.661 | 0.809 | 0.887 | 8 % | 8.0 |

**masked (115 targets)** — target-level recall@K (targets: gold symbols; CB: innermost concrete symbol holding each gold line)

| channel | R@5 | R@10 | R@16 | R@50 | R@200 | absent from channel | median rank when present |
|---|---:|---:|---:|---:|---:|---:|---:|
| literal (any depth; ranks are discovery order, not relevance) | – | – | – | – | 0.330 | 67 % | – |
| lexical | 0.304 | 0.348 | 0.400 | 0.496 | 0.652 | 35 % | 8 |
| semantic | 0.061 | 0.070 | 0.122 | 0.200 | 0.348 | 65 % | 41.0 |
| union | 0.322 | 0.374 | 0.452 | 0.557 | 0.748 | 25 % | 10.0 |
| fused | 0.165 | 0.226 | 0.339 | 0.530 | 0.670 | 25 % | 20.0 |

**heldout (174 targets)** — target-level recall@K (targets: gold symbols; CB: innermost concrete symbol holding each gold line)

| channel | R@5 | R@10 | R@16 | R@50 | R@200 | absent from channel | median rank when present |
|---|---:|---:|---:|---:|---:|---:|---:|
| literal (any depth; ranks are discovery order, not relevance) | – | – | – | – | 0.310 | 69 % | – |
| lexical | 0.282 | 0.402 | 0.483 | 0.672 | 0.799 | 20 % | 10 |
| semantic | 0.310 | 0.379 | 0.443 | 0.615 | 0.828 | 17 % | 14.5 |
| union | 0.420 | 0.540 | 0.644 | 0.770 | 0.920 | 8 % | 8.0 |
| fused | 0.328 | 0.448 | 0.557 | 0.718 | 0.856 | 8 % | 11.5 |

**cb (182 targets)** — target-level recall@K (targets: gold symbols; CB: innermost concrete symbol holding each gold line)

| channel | R@5 | R@10 | R@16 | R@50 | R@200 | absent from channel | median rank when present |
|---|---:|---:|---:|---:|---:|---:|---:|
| literal (any depth; ranks are discovery order, not relevance) | – | – | – | – | 0.154 | 85 % | – |
| lexical | 0.088 | 0.132 | 0.165 | 0.247 | 0.473 | 53 % | 38.5 |
| semantic | 0.104 | 0.126 | 0.154 | 0.286 | 0.511 | 49 % | 37 |
| union | 0.154 | 0.203 | 0.247 | 0.379 | 0.604 | 40 % | 25.0 |
| fused | 0.121 | 0.165 | 0.192 | 0.346 | 0.511 | 40 % | 35.0 |

### By query class (natural sets pooled; masked separate)

| regime / class | targets | lex R@16 | sem R@16 | union R@16 | fused R@16 | lex R@200 | sem R@200 | union R@200 | literal R@any | sem-only in union@200 | lex-only in union@200 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| natural: NL behavioral description | 108 | 0.45 | 0.26 | 0.59 | 0.54 | 0.81 | 0.73 | 0.93 | 0.00 | 0.12 | 0.19 |
| natural: callers/impact | 4 | 0.75 | 0.50 | 0.75 | 0.75 | 0.75 | 0.75 | 0.75 | 0.75 | 0.00 | 0.00 |
| natural: exact identifier/symbol | 53 | 0.62 | 0.57 | 0.74 | 0.60 | 0.79 | 0.79 | 0.89 | 0.53 | 0.09 | 0.09 |
| natural: filename/path | 12 | 0.58 | 0.42 | 0.58 | 0.50 | 0.67 | 0.75 | 0.83 | 0.25 | 0.17 | 0.08 |
| natural: mixed/other (long text with identifiers) | 208 | 0.35 | 0.25 | 0.42 | 0.37 | 0.64 | 0.60 | 0.74 | 0.37 | 0.09 | 0.13 |
| natural: quoted literal/error text | 84 | 0.32 | 0.31 | 0.45 | 0.36 | 0.61 | 0.62 | 0.73 | 0.31 | 0.12 | 0.11 |
| natural: test discovery | 2 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.50 | 0.00 | 0.00 |
| masked: NL behavioral description | 46 | 0.33 | 0.11 | 0.39 | 0.28 | 0.61 | 0.28 | 0.67 | 0.00 | 0.07 | 0.39 |
| masked: callers/impact | 4 | 0.50 | 0.25 | 0.50 | 0.50 | 0.50 | 0.25 | 0.50 | 0.25 | 0.00 | 0.25 |
| masked: exact identifier/symbol | 8 | 0.00 | 0.00 | 0.00 | 0.00 | 0.38 | 0.12 | 0.50 | 0.25 | 0.12 | 0.38 |
| masked: filename/path | 3 | 0.67 | 0.00 | 0.67 | 0.67 | 1.00 | 0.67 | 1.00 | 0.67 | 0.00 | 0.33 |
| masked: implementation discovery (NL) | 4 | 0.00 | 0.00 | 0.00 | 0.00 | 0.25 | 0.50 | 0.75 | 0.00 | 0.50 | 0.25 |
| masked: mixed/other (long text with identifiers) | 40 | 0.53 | 0.15 | 0.60 | 0.45 | 0.75 | 0.38 | 0.82 | 0.65 | 0.07 | 0.45 |
| masked: quoted literal/error text | 10 | 0.60 | 0.20 | 0.60 | 0.40 | 0.80 | 0.60 | 1.00 | 0.70 | 0.20 | 0.40 |

## Anatomy of FUSION_ORDER_LOSS units

| set | units | lost wt | fused rank 17–50 | 51–100 | 101+ | a channel had it ≤16 | lexical ≤16 | semantic ≤16 | lexical rank median | semantic rank median |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | 25 | 12.2 | 58 % | 11 % | 31 % | 28 % | 28 % | 0 % | 24 | 99 |
| masked | 42 | 24.9 | 37 % | 1 % | 62 % | 23 % | 22 % | 2 % | 50.0 | 87 |
| heldout | 55 | 15.7 | 54 % | 13 % | 33 % | 32 % | 15 % | 17 % | 39 | 67 |
| cb | 89 | 6.2 | 41 % | 13 % | 46 % | 9 % | 4 % | 6 % | 52 | 113 |

## Which oracle recovers which lost unit (lost weight recovered, by attributed stage)

| set | stage | lost wt | B16 | B50 | B full | B chan16 | C dep | C primary | C top |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | CANDGEN | 5.33 | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % |
| dev | ROUTE | 1.00 | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % |
| dev | STRUCT | 0.33 | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % | 100 % |
| dev | FUSION | 12.23 | 2 % | 50 % | 83 % | 25 % | 33 % | 0 % | 39 % |
| dev | ALLOC | 13.70 | 94 % | 95 % | 95 % | 91 % | 0 % | 0 % | 1 % |
| masked | CANDGEN | 10.80 | 0 % | 6 % | 8 % | 8 % | 0 % | 0 % | 0 % |
| masked | ROUTE | 3.00 | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % |
| masked | STRUCT | 2.03 | 26 % | 26 % | 26 % | 26 % | 100 % | 10 % | 100 % |
| masked | FUSION | 24.87 | 2 % | 37 % | 89 % | 24 % | 24 % | 4 % | 33 % |
| masked | ALLOC | 9.87 | 98 % | 100 % | 100 % | 100 % | 0 % | 2 % | 2 % |
| heldout | CANDGEN | 3.05 | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % |
| heldout | STRUCT | 1.73 | 0 % | 0 % | 0 % | 0 % | 88 % | 12 % | 100 % |
| heldout | FUSION | 15.74 | 0 % | 44 % | 69 % | 23 % | 14 % | 3 % | 29 % |
| heldout | ALLOC | 17.42 | 71 % | 69 % | 68 % | 66 % | 0 % | 0 % | 0 % |
| cb | EVAL | 0.68 | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % |
| cb | CANDGEN | 1.34 | 3 % | 3 % | 1 % | 0 % | 0 % | 0 % | 0 % |
| cb | STRUCT | 0.46 | 4 % | 4 % | 0 % | 0 % | 100 % | 0 % | 98 % |
| cb | FUSION | 6.17 | 4 % | 10 % | 24 % | 4 % | 22 % | 0 % | 23 % |
| cb | ALLOC | 7.98 | 11 % | 26 % | 25 % | 19 % | 9 % | 3 % | 33 % |

## Is there a request-time signal at the selection seam? (channel-rank patterns, fused ranks 6–16)

| set | pattern | candidates | gold | precision [Wilson 95 %] |
|---|---|---:|---:|---|
| dev | lexical ≤5, no semantic | 80 | 10 | 0.125 [0.069, 0.215] |
| dev | in both channels | 588 | 26 | 0.044 [0.030, 0.064] |
| dev | other | 102 | 1 | 0.010 [0.002, 0.053] |
| masked | lexical ≤5, no semantic | 108 | 11 | 0.102 [0.058, 0.173] |
| masked | in both channels | 475 | 9 | 0.019 [0.010, 0.036] |
| masked | other | 187 | 0 | 0.000 [0.000, 0.020] |
| heldout | lexical ≤5, no semantic | 43 | 2 | 0.047 [0.013, 0.155] |
| heldout | in both channels | 647 | 37 | 0.057 [0.042, 0.078] |
| heldout | other | 25 | 1 | 0.040 [0.007, 0.195] |
| cb | lexical ≤5, no semantic | 15 | 1 | 0.067 [0.012, 0.298] |
| cb | in both channels | 206 | 22 | 0.107 [0.072, 0.156] |
| cb | other | 10 | 0 | 0.000 [-0.000, 0.278] |

One-hop neighbors of the top-5 seeds (expansion admitted + cap-lost, ast-grep callers admitted + cap-lost), excluding seeds:

| set | neighbors / task | gold | precision [Wilson 95 %] |
|---|---:|---:|---|
| dev | 69.4 | 6 | 0.0012 [0.0006, 0.0027] |
| masked | 71.4 | 9 | 0.0018 [0.0009, 0.0034] |
| heldout | 52.6 | 16 | 0.0047 [0.0029, 0.0076] |
| cb | 66.8 | 32 | 0.0228 [0.0162, 0.0320] |

## Pack efficiency (baseline)

| set | used tok | relevant tok | rel/used | items | false-positive items (no gold line delivered) |
|---|---:|---:|---:|---:|---:|
| dev | 1225 | 180.3 | 0.1471 | 6.96 | 6.19 (89 %) |
| masked | 1338 | 103.5 | 0.0774 | 7.11 | 6.74 (95 %) |
| heldout | 1399 | 191.1 | 0.1366 | 6.54 | 5.65 (86 %) |
| cb | 1918 | 151.3 | 0.0789 | 7.05 | 6.14 (87 %) |

## #25 canonical test predicate

- Pre-allocation candidates where `context::is_test_symbol` and `symbols::is_test_symbol` disagree: **10** across **7** of 226 task-instances; gold-bearing: **0**.
- Research arm (canonical predicate for role assignment): packs changed on **0** of 226; mean Δ coverage +0.0000.
- Disagreeing symbols: `test` ×6, `TestCaseFunction.runtest` ×2, `_pytest` ×1, `TestCaseFunction.addSubTest` ×1

