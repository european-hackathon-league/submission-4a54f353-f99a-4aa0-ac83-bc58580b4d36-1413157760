# Review files (phase 2 checkpoint)

Proposals by the implementing agent, to be reviewed with the team. Edit the proposed columns in place and set `confirmed` to `yes` on every row you accept. Rows without `yes` load as candidates (not confirmed).

| File | Content |
|---|---|
| `car_areas.csv` | The fixed car-area list agents tag from |
| `vfc_topics.csv` | BMW VFC level-2 topic -> proposed feature and car area, with row counts |
| `features.csv` | Proposed feature catalog |
| `study_attributes.csv` | Customer study attribute -> feature |
| `option_list_<vehicle>.csv` | Option-list extraction: item x variant availability (standard / optional / absent) and price |
| `option_features.csv` | Option-list item -> feature |
| `tech_data_<vehicle>.csv` | Customer-facing technical data from the option lists |

After the review, re-run `uv run python -m app.seed.run` on a fresh database. The generators never overwrite these files.
