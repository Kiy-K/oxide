Full index, medians over 7 paired reps (ms). store Δ = serial time removed from the store loop; parse Δ = what the parse stage gained;
CPU: store CPU removed vs parse-worker CPU added; net = total process CPU change.

| repo | Δ wall | Δ store wall | Δ parse wall | Δ store CPU | Δ parse CPU | net Δ CPU | CPU moved to workers / removed from store |
|---|--:|--:|--:|--:|--:|--:|--:|
| flask | -101 | -108 | +5 | -108 | +57 | -54 | 53% |
| darkreader | -164 | -220 | +56 | -220 | +125 | -96 | 57% |
| axios | -83 | -134 | +50 | -134 | +83 | -50 | 62% |
| tokio | -535 | -671 | +124 | -669 | +360 | -309 | 54% |
| gin | -163 | -223 | +66 | -223 | +136 | -86 | 61% |
| gson | -327 | -406 | +79 | -405 | +233 | -161 | 57% |
| zstd | -1033 | -1136 | +139 | -1134 | +476 | -657 | 42% |
| fmt | -798 | -1308 | +502 | -1306 | +766 | -533 | 59% |

fmt full per-rep wall (B, C, ratio, first):
  6122 5955 -2.7% first=baseline
  5255 4981 -5.2% first=challenger
  6541 4236 -35.2% first=baseline
  5758 4171 -27.6% first=challenger
  5999 4165 -30.6% first=baseline
  5745 4947 -13.9% first=challenger
  5789 5182 -10.5% first=baseline

order effect, full index: median paired Δ wall by which variant ran first:
  baseline first: -22.9% (n=32)
  challenger first: -24.5% (n=24)
