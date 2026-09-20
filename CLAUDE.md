# AI Voice Agent

FastAPI backend (`app/`), SQLAlchemy + Alembic (`migrations/`), Vite/TS frontend (`frontend/src/`), Docker deploy (`deploy/`, `docker-compose*.yml`).

## Rules
- Minimal diffs. Never rewrite working code or tests. Never echo unchanged code.
- Read only the function/range needed (`offset`/`limit`), not whole files.
- Grep before Read. One targeted search, not repeated sweeps.
- No subagents unless asked.
- Ignore `AI_Voice_Agent - Copy/` (stale duplicate), `data/`, `venv/`, `node_modules/`.
- Verify: `pytest tests/ -q` backend; `cd frontend && npx tsc --noEmit` frontend.
- Key: `app/services/tts.py` (TTS), LLM prompt in agent service.
- Reply terse. Diff or file:line, not prose.
