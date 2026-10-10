# Case clustering: threshold report

Embedding provider: `local:hash1536`. Signals: 11095 in 153 features. Open rule: >= 10 distinct customers per line.

| Cosine threshold | Cases | Singletons | Median size | Largest | Cohesion | Would open |
|---|---|---|---|---|---|---|
| 0.15 | 1304 | 30% | 2.0 | 446 | 0.758 | 139 |
| 0.20 | 2660 | 45% | 2.0 | 419 | 0.852 | 110 |
| 0.25 | 4200 | 65% | 1.0 | 311 | 0.920 | 88 |
| 0.30 | 5527 | 76% | 1 | 233 | 0.952 | 74 |
| 0.35 | 6608 | 80% | 1.0 | 147 | 0.965 | 51 |
| 0.40 | 7547 | 83% | 1 | 77 | 0.974 | 39 |
