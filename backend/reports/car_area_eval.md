# Car-area tagging eval

Sample: 300 BMW feedback verbatims whose VFC topic maps to a specific car area (one signal per verbatim, deterministic order). Expected = car area of the VFC topic in `seed/review/vfc_topics.csv`; predicted = tag from the verbatim text alone.

| Tagger | Accuracy |
|---|---|
| heuristic@v1 | 34.7% |

LLM tagger not run: no `OPENAI_API_KEY` configured.

Most frequent confusions (heuristic@v1), expected -> predicted:

- driving_dynamics -> none: 56
- powertrain -> driving_dynamics: 11
- powertrain -> none: 9
- quality -> none: 8
- infotainment -> none: 7
- powertrain -> infotainment: 7
- safety -> none: 5
- driving_dynamics -> exterior: 5
