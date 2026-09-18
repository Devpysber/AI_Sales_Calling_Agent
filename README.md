# Samvaad AI — AI Sales Voice Agent

An AI sales agent that phones your leads in English, Hindi and other Indian languages. It answers questions from your company documents (RAG), qualifies each lead, books meetings, and records every conversation in a built-in CRM.

| Layer | Technology |
|---|---|
| Telephony | **Plivo** Voice API (outbound + inbound, signed webhooks) |
| Conversation LLM | **OpenRouter** (hedged requests across free models) → **Sarvam** `sarvam-105b` fallback |
| Voice | **Sarvam Bulbul v3** text-to-speech, 8 kHz phone audio |
| Knowledge (RAG) | PDF/DOCX/TXT/MD/CSV → chunks → hybrid BM25 + embedding search |
| Backend | FastAPI, SQLAlchemy 2, Alembic, gunicorn/uvicorn |
| State | PostgreSQL (SQLite locally), Redis (call sessions, audio, locks, rate limits) |
| Frontend | React 19 + TypeScript + Vite + Tailwind v4 + TanStack Query |
| Edge | nginx load balancer, rate limits, static SPA |

---

## Features

- **Dashboard:** calls today, answer rate, talk time, meetings, hot leads, a 14-day volume chart, the pipeline funnel, call outcomes, and a live activity feed.
- **Leads:**
  - search, filter and sort leads, with pagination
  - bulk call, bulk queue for auto-dial, and bulk delete
  - a detail panel showing AI notes, call history and a timeline of every change
  - a do-not-call flag and a preferred language per lead
- **Import:** upload CSV or Excel files. You can remap columns, set default language, tags and source, and duplicates are skipped. Rejected rows are listed with the reason.
- **Calls:** a live view with the transcript updating in real time, plus hang-up, AI summary, qualification, sentiment, outcome, AI latency and optional recording.
- **Activity:** an audit log of everything that happens:
  - leads created, imported or updated
  - calls placed, answered and ended
  - CRM fields the AI changed
  - meetings booked
  - automation runs and emails sent
- **Agent:** persona, voice (37 Sarvam speakers with preview), greetings, playbook, objection handling, qualification rules and guardrails.
  - **Playground:** talk to the agent in the browser, by text or microphone. It uses the same prompt, knowledge and voice as real calls. A turn inspector shows intent, qualification, CRM extraction and the knowledge sources used.
- **Knowledge Base:** upload service brochures, price lists and FAQs, or write notes directly. A retrieval tester shows which passages the agent would use.
- **Automation** (replaces n8n):
  - auto-dial new leads, and retry busy or unanswered calls with a gap and attempt limit
  - meeting reminders and a daily report by email
  - calling hours window (TRAI default 09:00–21:00 IST), allowed days, and a concurrent call cap
- **Settings:** live health checks for Plivo, OpenRouter, Sarvam, the public URL, signature verification and SMTP, plus inbound call setup and deployment details.

## How a call works

```
Dashboard / scheduler ──► Plivo REST (dial) ──► phone rings
                                                  │ answered
          POST /api/plivo/answer  ◄───────────────┘  greeting (Sarvam TTS, cached) + <GetInput speech>
          POST /api/plivo/input   ◄── customer speech text
               │ turn runs in a worker pool:
               │   RAG search → LLM (persona + lead + history + knowledge, JSON) → Sarvam TTS → Redis
               ▼
          reply ready in <3s? <Play> reply + <GetInput> : short hold tone + /api/plivo/wait (polls Redis)
          POST /api/plivo/hangup  ◄── status, duration → transcript saved → AI summary → CRM + activity
```

Every request is **stateless**: call state lives in Redis. Any API replica can serve any webhook.

---

## Run locally

```bash
# Backend
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                                  # then fill in the keys
uvicorn app.main:app --port 8010

# Frontend (hot reload, proxies /api to :8010)
cd frontend && npm install && npm run dev             # http://localhost:5173
```

`npm run build` puts the dashboard in `frontend/dist`. FastAPI then serves it at `http://localhost:8010`.

Plivo must be able to reach your machine. Expose the app with a tunnel and set `PUBLIC_BASE_URL` to that address:
```bash
cloudflared tunnel --url http://127.0.0.1:8010        # or: ngrok http 8010
```

Tests (mocked Plivo/LLM/TTS, full call flow):
```bash
pytest -q
```

## Deploy

### Single server
```bash
docker compose up -d --build        # app + Postgres + Redis on :8000
```

### Hostinger VPS (single server, HTTPS)

Tested layout for a KVM 2 (2 vCPU, 8 GB): Caddy (automatic HTTPS) → nginx → API (3 workers) + scheduler worker, PostgreSQL and Redis, all in Docker.

1. **Domain:** add an `A` record for e.g. `voice.yourcompany.com` pointing to the VPS IP. Plivo needs HTTPS for webhooks and the audio stream.
2. **On the VPS** (SSH as root):
   ```bash
   git clone https://github.com/Devpysber/AI_Sales_Calling_Agent.git /opt/psyber-voice
   cd /opt/psyber-voice && sudo bash deploy/vps-setup.sh
   ```
   It installs Docker, opens ports 22/80/443, adds 2 GB swap, creates `.env` (domain, admin login, random `SECRET_KEY` and database password), asks you to add provider keys, builds and starts everything, and schedules a daily database backup.
3. **Open** `https://your-domain`, sign in, then **Integrations & system → Inbound calls → Reconnect** so the Plivo number uses the new domain. Copy website form snippets again from **Automation** (the link now uses your domain).
4. **Updates:** `bash deploy/update.sh` · **Backup now:** `bash deploy/backup.sh` · **Logs:** `docker compose -f docker-compose.vps.yml logs -f api worker`

### Horizontally scaled
```bash
POSTGRES_PASSWORD=... docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml up -d --scale api=6
```

```
            Internet / Cloud LB (TLS)
                      │
                   nginx  ── static dashboard, rate limits, least-conn balancing
                      │
          ┌───────────┼───────────┐
        api #1      api #2  …   api #N     stateless (gunicorn × WEB_CONCURRENCY)
          └───────────┼───────────┘
                Redis │  Postgres
                      │
                   worker   scheduler (Redis leader lock: jobs run exactly once)
                  migrate   one-off Alembic job per deploy
```

On AWS or GCP, use managed Postgres (RDS / Cloud SQL) and managed Redis (ElastiCache / Memorystore). Run `api` and `worker` on ECS/Kubernetes behind an ALB. Scale `api` on CPU or request rate.

**Capacity notes**
- Each live call uses about one request every 3–8 seconds, plus a background turn thread (up to 64 per process).
- Four API containers × 4 workers comfortably handle hundreds of simultaneous calls.
- Real limits come from Plivo channel concurrency and LLM rate limits.
- The OpenRouter free tier is heavily rate limited, so buy credits or switch `LLM_PROVIDERS=sarvam,openrouter` before production volume.

### Production checklist
- [ ] `ENVIRONMENT=production`, strong `ADMIN_PASSWORD`, random `SECRET_KEY` (the same on every replica)
- [ ] `PUBLIC_BASE_URL` on your own HTTPS domain; `PLIVO_VALIDATE_SIGNATURE=true`
- [ ] Managed Postgres with backups, and Redis with persistence
- [ ] Paid LLM capacity (OpenRouter credits or Sarvam)
- [ ] Knowledge Base filled with your real services, pricing and FAQs, plus a company tagline set on the Agent page
- [ ] Calling hours, retry limits and the DND/consent process reviewed (TRAI / DLT)
- [ ] Email configured (`RESEND_API_KEY` + `EMAIL_FROM`, or SMTP) for reminders, reports and missed-call alerts
- [ ] Sign-in email and password set on the Admin profile page
- [ ] Plivo number connected under Inbound & transfer (reconnect after the public URL changes), transfer number set
- [ ] `COST_PER_*` rates filled in to see cost estimates on Analytics
- [ ] nginx (or your load balancer) passes WebSocket upgrades for `/api/plivo/stream` and live supervision

The API refuses to start with `ENVIRONMENT=production` when a critical item is missing: short or missing `SECRET_KEY`,
no admin password, non-HTTPS `PUBLIC_BASE_URL`, several workers without `REDIS_URL`, SQLite with several replicas,
or Plivo signature validation turned off. With Redis configured, live supervision works across replicas (events and
commands are relayed over Redis pub/sub), so any API replica can serve a supervisor.
- [ ] Credentials rotated if they were ever shared in chat or committed (see below)

## Security

- Secrets come only from environment variables. `.env` is git-ignored.
- The dashboard uses an HMAC-signed, HttpOnly session cookie. Login is rate limited. Integrations can use `API_TOKEN`.
- Plivo webhooks are verified with `X-Plivo-Signature-V3`. Generated audio lives at random, expiring URLs.
- Secret scanning: `pip install pre-commit && pre-commit install` runs gitleaks before every commit.

## Project layout

```
app/
  api/        auth, leads, calls, knowledge, agent, system (automation/activity/status/media), plivo webhooks
  core/       config, database, store (Redis/memory), auth, logging
  models/     Lead, Call, Event, Document/DocumentChunk, AppSetting
  services/   agent, llm, rag, tts, call_service, call_session, crm_service, scheduler, events, notifications
  main.py     API app      worker.py  scheduler process      migrate.py  migration job
migrations/   Alembic
frontend/     React dashboard
deploy/       nginx load balancer + web image
tests/        end-to-end flow tests
```

## License

MIT
