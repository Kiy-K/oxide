# Real-repository Java evaluation

| Repository | HEAD | Java files | Entities | Relations | Complete files | Audit calls/imports |
|---|---|---:|---:|---:|---:|---:|
| gson | `854c8255b625cf1e13c701a83ea9ccb4caaa576a` | 264 | 4,220 | 48,243 | 264 | 10 / 10 |
| mockito | `116a4d6231dc35f90543060af41dcde00fe4e2ae` | 1,004 | 9,879 | 78,231 | 1,004 | 10 / 10 |

Both bench outputs parsed as JSON. Median cold index time over the requested three iterations: gson 1.602s; mockito 2.864s. Cold context medians: gson 0.0810s; mockito 0.1202s. Spot-check details: `spotcheck.md` (40/40 sampled audited edges judged correct).
