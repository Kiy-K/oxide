Cells are LadybugDB / HelixDB.

| | py_repo | email | asyncio | ansible |
|---|---|---|---|---|
| entities / relations | 65/194 / 65/194 | 814/3702 / 814/3702 | 1283/5224 / 1283/5224 | 20404/100019 / 20404/100019 |
| rebuild s (write+publish) | 0.26 / 0.29 | 0.49 / 1.43 | 0.55 / 2.23 | 7.41 / 78.93 |
| peak RSS after rebuild MiB | 114 / 41 | 130 / 123 | 140 / 164 | 625 / 943 |
| generation on disk MiB (db) | 8.5 (8.5) / 0.5 (0.5) | 19.4 (18.1) / 8.9 (7.6) | 22.0 (19.8) / 13.8 (11.5) | 135.8 (120.1) / 295.6 (279.9) |
| reopen ms | 14.1 / 3.1 | 15.2 / 3.9 | 14.6 / 5.0 | 14.4 / 11.2 |
| entities(1) cold/warm ms | 1.44/0.33 / 0.85/0.34 | 1.47/0.31 / 0.71/0.35 | 2.18/0.52 / 0.96/0.56 | 1.54/0.29 / 2.53/0.53 |
| entities(64) warm ms | 5.91 / 1.47 | 6.75 / 1.88 | 6.26 / 2.02 | 6.17 / 2.80 |
| hub adjacency(64) cold/warm ms | 5.24/1.60 / 1.39/0.89 | 6.01/1.81 / 3.90/3.05 | 6.58/1.69 / 3.81/3.25 | 20.58/9.42 / 178.27/166.27 |
| region(file) cold/warm ms | 1.88/1.93 / 0.98/0.71 | 1.96/2.01 / 1.45/1.01 | 1.99/1.95 / 1.81/1.37 | 3.29/2.11 / 2.28/1.96 |
| full adjacency per entity ms | 1.647 / 0.610 | 1.718 / 0.695 | 1.630 / 0.811 | 2.110 / 1.265 |
| lexical median ms | 1.44 / 1.16 | 4.78 / 3.12 | 6.35 / 4.18 | 9.02 / 14.01 |
| retrieve median ms | 1.32 / 1.08 | 1.22 / 1.91 | 2.53 / 2.29 | 3.34 / 6.63 |
| route median ms | 74.7 / 39.5 | 86.0 / 69.3 | 127.3 / 71.7 | 169.4 / 96.7 |
| RSS after reads MiB | 118 / 38 | 139 / 114 | 142 / 136 | 584 / 973 |
| soak RSS first→last MiB | 118→118 / 38→38 | 139→139 / 114→115 | 142→142 / 136→136 | 510→510 / 899→899 |
| cycle RSS first→last MiB | 167→220 / 48→99 | 191→238 / 182→278 | 218→237 / 192→300 | 675→755 / 1128→1337 |
| process peak RSS MiB | 249 / 99 | 271 / 278 | 274 / 312 | 970 / 1337 |

Lexical quality, 7 gold tasks (macro mean):

| variant | config | recall@1 | recall@5 | recall@10 | recall@20 | mrr | routed_coverage |
|---|---|---|---|---|---|---|---|
| ladybug | lexical-only | 0.286 | 0.857 | 1.000 | 1.000 | 0.540 | 1.000 |
| ladybug | lexical+structural | 0.286 | 0.857 | 1.000 | 1.000 | 0.516 | 1.000 |
| helix | lexical-only | 0.429 | 0.643 | 0.643 | 0.786 | 0.540 | 0.857 |
| helix | lexical+structural | 0.429 | 0.643 | 0.643 | 0.786 | 0.540 | 0.857 |
| helix-stem-tuned | lexical-only | 0.429 | 0.857 | 0.929 | 1.000 | 0.621 | 1.000 |
| helix-stem-tuned | lexical+structural | 0.429 | 0.857 | 0.929 | 1.000 | 0.621 | 1.000 |

Parity (lines differing / compared):

| | entity | adjacency | named | retrieve-structural | route-structural | trace-structural | lexical |
|---|---|---|---|---|---|---|---|
| py_repo | 0/65 | 0/65 | 0/54 | 0/8 | 0/8 | 0/8 | 6/8 |
| email | 0/814 | 0/814 | 0/625 | 0/1 | 0/1 | 0/1 | 1/1 |
| asyncio | 0/1283 | 0/1283 | 0/823 | 0/1 | 0/1 | 0/1 | 1/1 |
| ansible | 0/20404 | 0/20404 | 0/13826 | 0/1 | 0/1 | 0/1 | 1/1 |
