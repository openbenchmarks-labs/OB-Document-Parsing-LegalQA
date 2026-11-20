# harness

Runs the benchmark and calls the parsing tools. No grading code lives here.

- `run.py` — Command-line runner (`init`, `smoke`, `run`, `watch`, `derive-full-run`, `list-workflows`).
- `config.py` — Pinned dataset revision, question counts, and the workflow name.
- `dataset.py` — Downloads and checks the dataset, and builds the text the reader sees for each question.
- `runner.py` — Runs one arm: parses every contract, asks the reader every question, then sends the answers to the judge.
- `parsing.py` — Parses one contract with one setup, retries failed calls, and caches the result.
- `tool_registry.py` — Loads every `tools/<tool>.yaml` and links it to its adapter.
- `adapters/` — One file per tool that sends the contract to the tool's API and returns markdown.
- `adapters/common.py` — Shared helpers: strip API keys from logs and record each request and response.
- `vendor_errors.py` — Decides whether a failed tool call is worth retrying.
- `pricing.py` — Turns pages, credits, and tokens into dollars. Reader and judge model prices live here; tool prices come from `tools/`.
- `llm_client.py` — Loads `.env` and builds the OpenAI client for the reader and judge.
- `storage.py` — Writes run files safely and reuses cached answers.
- `tests/` — Offline tests for the files above.
