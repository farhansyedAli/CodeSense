# Retrieval evaluation harness

## Status
- `gold.json` — hand-labeled queries with correct file + line ranges per repo
- `runner.py` — TODO (next commit): queries current pipeline and scores
- `results/` — TODO: output files per run

## Workflow (planned)
1. Pre-index the repos listed in gold.json
2. `python -m evals.runner`
3. Metrics printed; result JSON written to `results/`
4. Compare runs over time