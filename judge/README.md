# judge

Grades answers against ground truth. No parsing or tool code lives here.

- `grader.py` — The judge prompt. Sorts each answer into correct, stale, fused, other, or ambiguous, and keeps a phrase-match check next to it for auditing.
- `board.py` — Scores each arm from its graded answers, ranks the arms, and writes `report.json` and `report.md`.
- `tests/` — Offline tests for the files above.
