# reading_level

Measure how hard a passage is. No LLM calls live here.

| File | Role |
|------|------|
| `word_frequency.py` | Frequency table + hard/easy words + level vocab |
| `sentences.py` | Split text; flag long/short sentences |
| `score.py` | Grade estimate, bands, full `analyze_passage` report |
| `dok.py` | Depth of Knowledge helpers for questions |

Outside this package:
- `llm.py` — Azure Foundry agent calls
- `llm_pipeline.py` — worksheet generate + level rewrite
- `image_gen.py` — Flux illustrations
- `worksheet_blocks.py` — segment uploaded worksheets into blocks

There is no `ai.py` — use `llm` / `llm_pipeline` directly.
