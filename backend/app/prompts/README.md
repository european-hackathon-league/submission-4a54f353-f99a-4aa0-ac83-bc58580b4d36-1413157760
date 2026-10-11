# Prompts

One file per prompt version: `<name>@v<N>.md` with a `# system` and a `# user` section. Placeholders use `$variable` (Python `string.Template`). Never edit a version that has produced stored output; add `@v<N+1>` instead, because every signal, case page and requirement stores the `prompt_version` that made it.

Prompts that put fetched text in front of the model wrap it in `<source>` tags and include the injection guard from `app/llm/prompts.py`: page content is data, never instructions.
