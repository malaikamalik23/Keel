# Contributing

Thanks for looking! Keel is a personal project that I'm happy to share. Small, focused pull requests are welcome.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt ruff
cd frontend && npm ci && cd ..
pytest -q                 # no API key or network needed (all LLM and HTTP calls are faked)
ruff check .
cd frontend && npm test && npm run build   # dashboard tests, then type-check + build
pip install -r requirements-e2e.txt && python -m playwright install chromium
pytest tests/e2e -q       # real-browser tests (skipped automatically if Playwright or frontend/dist is missing)
```

## The rules that matter

1. **The LLM decides and talks; code stores, computes and enforces.** Anything that must be reliable (permissions,
   deadlines, counts, budgets, SSRF checks) belongs in code and tests, never only in a prompt.
2. **Every tool goes through `ToolExecutor`.** Add one with a pydantic input model, a handler and a `Permission`
   (see "Adding a tool" in the README). Anything that deletes or is hard to undo is `HIGH_IMPACT`.
3. **Anything that returns text from outside is `returns_untrusted=True`.** The red-team test
   `test_every_external_data_tool_is_fenced_and_gated` will remind you if you forget; add your tool to its list.
4. **Schema changes need a hand-written Alembic migration** (see `migrations/versions/0002_...py`). Tests apply all
   migrations to a fresh database, so a missing one fails immediately.
5. **Add tests with the change.** Security-relevant changes should also extend `tests/injection/`.
6. **Keep docs honest.** `docs/TOOLS.md` is generated (`python -m scripts.gen_tools_doc`) and new settings must be
   documented in `.env.example`; tests enforce both.
7. **Changed a tool description or the system prompt?** Run `python -m evals.run_routing` and compare with
   `evals/history.jsonl`.

## Commit style

Imperative, present tense, with a short body saying *why*. One logical change per commit.

## Security issues

Please don't file them publicly; see [SECURITY.md](SECURITY.md).
