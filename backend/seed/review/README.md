# Review files (phase 2 checkpoint)

Proposals by the implementing agent, to be reviewed with the team. Edit the proposed columns in place and set `confirmed` to `yes` on every row you accept. Rows without `yes` load as candidates (not confirmed).

| File | Content |
|---|---|
| `study_attributes.csv` | Customer study attribute -> topic ID of the taxonomy (`proposed_topic`) |
| `option_list_<vehicle>.csv` | Option-list extraction: item x variant availability (standard / optional / absent) and price |
| `option_features.csv` | Option-list item -> topic ID of the taxonomy (`proposed_topic`) |
| `tech_data_<vehicle>.csv` | Customer-facing technical data from the option lists |

Car areas, topics and aspect groups are not reviewed here: they come from the fixed taxonomy in `taxonomy/` (changes.md, section Taxonomy). Topic IDs must exist in `taxonomy/topics.csv`.

After the review, re-run `uv run python -m app.seed.run` on a fresh database. The generators never overwrite these files.
