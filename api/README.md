# Triage API (FastAPI wrapper)

A thin HTTP layer over the existing Triage engine (`src/`), so the Next.js
frontend can drive the same SQLite-backed briefing/agent without going through
Streamlit. The engine is unchanged — this only wraps it.

## Auth

Shared-secret, server-to-server. Every `/api/*` request must send:

```
X-API-Key: <TRIAGE_API_SECRET>
```

The browser never calls this API directly — the Next.js server proxies to it and
enforces real per-user auth (Supabase) at its own layer. `/health` is open.

## Running locally

From the **repo root** (so the relative `data/triage.db` path resolves):

```bash
source venv/bin/activate
# TRIAGE_API_SECRET is read from .env (see .env.example); ANTHROPIC_API_KEY too
uvicorn api.main:app --reload --port 8000
```

Interactive docs: http://localhost:8000/docs

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness (no auth) |
| GET | `/api/dashboard` | Counts, value at risk, ledger totals, run freshness |
| GET | `/api/briefing` | Latest briefing — red/amber/green buckets + markdown |
| GET | `/api/customers` | All customers with balances + latest classification |
| GET | `/api/customers/{id}` | Profile, payment stats, invoices, comms, recommendation |
| POST | `/api/upload` | Upload aged-debtors CSV (multipart `file`) — replaces dataset |
| POST | `/api/triage/run` | Start a batch run (background thread) |
| GET | `/api/triage/status` | Poll run progress |
| POST | `/api/invoices/{id}/mark-paid` | Mark invoice paid/partial |
| POST | `/api/communications` | Log a manual communication |
| POST | `/api/emails/approve` | Log an email approval (tracks `was_edited`) |

## Notes

- The agent chain (`runner` → `orchestrator` → `agent_claude`) is imported
  **lazily** inside the `/api/triage/*` handlers only, because `agent_claude`
  reads `st.secrets` and runs `load_dotenv()` at import time (handover rule #1).
- `init_db()` runs on startup — safe (`CREATE IF NOT EXISTS`), never wipes data.
- This lives on the `api` branch; `main` continues to auto-deploy to Streamlit
  Cloud untouched.
