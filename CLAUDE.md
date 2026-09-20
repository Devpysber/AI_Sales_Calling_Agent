# AI Voice Agent

FastAPI backend (`app/`), SQLAlchemy + Alembic (`migrations/`), Vite/TS frontend (`frontend/src/`), Docker deploy (`deploy/`, `docker-compose*.yml`).

## Rules
- Minimal diffs. Do not rewrite working code or tests.
- Ignore `AI_Voice_Agent - Copy/` (stale duplicate), `data/`, `venv/`, `node_modules/`.
- Tests: `pytest tests/` backend; `cd frontend && npm run build` for TS check.
- Key: `app/services/tts.py` (TTS), LLM prompt in agent service, call flow under `app/`.
- Reply terse. Show diffs, not full files.
