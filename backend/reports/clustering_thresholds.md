# Case clustering: threshold report

Embedding provider: `local:hash1536`. Signals: 11091 in 612 topic + aspect groups. Only the 35 groups with >= 50 signals are split by similarity; every other group is one case. Open rule: >= 10 distinct customers per line.

| Cosine threshold | Cases | Singletons | Median size | Largest | Cohesion | Would open |
|---|---|---|---|---|---|---|
| 0.10 | 759 | 12% | 6 | 606 | 0.637 | 156 |
| 0.15 | 1206 | 19% | 4.0 | 279 | 0.694 | 153 |
| 0.20 | 1914 | 34% | 2.0 | 256 | 0.781 | 138 |
| 0.30 | 3417 | 66% | 1 | 161 | 0.898 | 126 |
