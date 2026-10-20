# Contributing

Thanks for helping! kvleak stays **zero-dependency** and **local-only**; please keep it that way.

## Dev setup

```bash
git clone https://github.com/kvleak/kvleak && cd kvleak
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q && ruff check src tests
python examples/make_demo.py demo && kvleak --path demo
```

## Adding a log format

1. Write `read_<harness>(path) -> Transcript` in `src/kvleak/sources.py`. It must yield one
   `Step` per *billed* model call, deduped, in chronological order.
2. Teach `detect_format` to recognise it from the first few lines.
3. Add a small fixture-based test in `tests/`.

## Adding prices

Add a `Price(...)` row to `BUILTIN` in `src/kvleak/pricing.py` and cite the provider's public
pricing page in the PR. Keys match model ids by substring, and the longest match wins.

## Good first issues

- Readers for other agent CLIs (Codex CLI, Gemini CLI, Aider, OpenHands, Cline, ...)
- A `--watch` mode that re-renders the audit every N seconds
- Per-project rollups in the HTML report
