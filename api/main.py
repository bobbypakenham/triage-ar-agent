"""
api/main.py — FastAPI wrapper around the Triage backend.

Exposes the existing Streamlit app's capabilities (briefing, customers, CSV
upload, triage runs, payment/communication logging) as a JSON HTTP API so the
Next.js frontend can drive the same SQLite-backed engine.

Design notes / critical rules honoured:
  - The agent chain (runner → orchestrator → agent_claude) reads st.secrets and
    runs load_dotenv() at import time. We NEVER import it at module top — it is
    imported lazily inside the /triage endpoints only (see handover rule #1).
  - database.py is the single source of truth. All reads/writes go through it.
  - tools.* return {"error": ...} dicts when a customer is missing; callers here
    guard with `"error" in result` (handover rule #3).

Run from the repo root (so the relative data/triage.db path resolves):
    TRIAGE_API_SECRET=... uvicorn api.main:app --reload
"""

from contextlib import asynccontextmanager
from datetime import date, datetime

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from pydantic import BaseModel

# Load .env up front so TRIAGE_API_SECRET / ANTHROPIC_API_KEY are present even on
# read-only requests that never import the agent chain (which has its own
# load_dotenv()). Without this, the auth dependency would see no secret.
load_dotenv()

from api.security import require_api_key  # noqa: E402
from src import briefing as briefing_mod
from src import csv_handler, database, tools

# ─────────────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the default user's schema exists before serving. Safe
    # (CREATE IF NOT EXISTS) and never wipes existing data.
    database.ensure_initialized()
    yield


app = FastAPI(
    title="Triage API",
    version="0.1.0",
    summary="HTTP wrapper around the Triage AR engine.",
    lifespan=lifespan,
)


class UserDatabaseMiddleware:
    """Route each request to its user's isolated SQLite database.

    Reads the Supabase user id from the X-User-ID header (sent by the Next.js
    server) and stores it in a contextvar for the duration of the request, so
    every database call resolves to that user's file. Pure ASGI (not
    BaseHTTPMiddleware) so the contextvar reliably propagates into the sync route
    handlers that run in the threadpool. Absent header → the "default" user.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        user_id = headers.get(b"x-user-id", b"default").decode() or "default"
        token = database.set_current_user_id(user_id)
        database.ensure_initialized()
        try:
            await self.app(scope, receive, send)
        finally:
            database.reset_current_user_id(token)


app.add_middleware(UserDatabaseMiddleware)


# ─────────────────────────────────────────────────────────────────────
# Health (unauthenticated)
# ─────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "service": "triage-api"}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _today_str() -> str:
    return date.today().isoformat()


def _overdue_and_outstanding(customer_id: str) -> tuple[float, float]:
    """(overdue_total, outstanding_total) for a customer's open invoices."""
    open_invoices = database.get_open_invoices(customer_id)
    outstanding = sum(inv["amount"] for inv in open_invoices)
    overdue = sum(inv["amount"] for inv in open_invoices if inv["days_past_due"] > 0)
    return round(overdue, 2), round(outstanding, 2)


def _display_name(profile: dict) -> str:
    return (
        profile.get("company_name")
        or profile.get("contact_name")
        or profile.get("customer_id", "")
    )


# ─────────────────────────────────────────────────────────────────────
# Read endpoints
# ─────────────────────────────────────────────────────────────────────


@app.get("/api/dashboard", tags=["read"], dependencies=[Depends(require_api_key)])
def dashboard():
    """Top-level numbers for the dashboard home: classification counts, value at
    risk, ledger totals, and the freshness of the latest run."""
    results, run_time, run_date = database.get_latest_results()
    customers = database.get_all_customers()

    counts = {"red": 0, "amber": 0, "green": 0}
    for r in results:
        cls = r.get("classification")
        if cls in counts:
            counts[cls] += 1

    total_overdue = 0.0
    total_outstanding = 0.0
    for c in customers:
        overdue, outstanding = _overdue_and_outstanding(c["customer_id"])
        total_overdue += overdue
        total_outstanding += outstanding

    stale = bool(run_date) and run_date < _today_str()

    return {
        "run_date": run_date,
        "run_time": run_time,
        "stale": stale,
        "has_results": bool(results),
        "counts": counts,
        "needs_attention": counts["red"] + counts["amber"],
        "totals": {
            "customers": len(customers),
            "reviewed": len(results),
            "outstanding": round(total_outstanding, 2),
            "overdue": round(total_overdue, 2),
        },
    }


def _enrich_recommendation(r: dict) -> dict:
    cid = r["customer_id"]
    profile = tools.get_customer_profile(cid)
    name = _display_name(profile) if "error" not in profile else cid
    overdue, _ = _overdue_and_outstanding(cid)
    return {
        "customer_id": cid,
        "name": name,
        "classification": r.get("classification"),
        "recommended_action": r.get("recommended_action"),
        "pattern_noticed": r.get("pattern_noticed"),
        "reasoning": r.get("reasoning"),
        "overdue_value": overdue,
        "drafted_email": r.get("drafted_email"),
    }


@app.get("/api/briefing", tags=["read"], dependencies=[Depends(require_api_key)])
def get_briefing():
    """The latest morning briefing — bucketed recommendations plus the rendered
    markdown the Streamlit app shows."""
    results, run_time, run_date = database.get_latest_results()
    if not results:
        return {
            "run_date": None,
            "run_time": None,
            "stale": False,
            "summary": {"red": 0, "amber": 0, "green": 0, "total_at_risk": 0.0},
            "red": [],
            "amber": [],
            "green": [],
            "markdown": "",
        }

    enriched = [_enrich_recommendation(r) for r in results]
    red = [e for e in enriched if e["classification"] == "red"]
    amber = [e for e in enriched if e["classification"] == "amber"]
    green = [e for e in enriched if e["classification"] == "green"]
    total_at_risk = round(sum(e["overdue_value"] for e in red + amber), 2)

    return {
        "run_date": run_date,
        "run_time": run_time,
        "stale": run_date < _today_str(),
        "summary": {
            "red": len(red),
            "amber": len(amber),
            "green": len(green),
            "total_at_risk": total_at_risk,
        },
        "red": red,
        "amber": amber,
        "green": green,
        # save=False so we don't write a markdown file from an API read.
        "markdown": briefing_mod.generate_briefing(results, save=False),
    }


@app.get("/api/customers", tags=["read"], dependencies=[Depends(require_api_key)])
def list_customers():
    """All customers with their open balance and latest classification."""
    customers = database.get_all_customers()
    latest_results, _, _ = database.get_latest_results()
    latest_by_id = {r["customer_id"]: r for r in latest_results}

    out = []
    for c in customers:
        cid = c["customer_id"]
        overdue, outstanding = _overdue_and_outstanding(cid)
        open_invoices = database.get_open_invoices(cid)
        days_overdue = max(
            (inv["days_past_due"] for inv in open_invoices), default=0
        )
        rec = latest_by_id.get(cid)
        stats = tools.get_payment_stats(cid)
        behavior = (
            stats.get("behavior_classification")
            if isinstance(stats, dict) and "error" not in stats
            else None
        )
        out.append(
            {
                "customer_id": cid,
                "name": c.get("company_name") or c.get("contact_name") or cid,
                "customer_type": c.get("customer_type"),
                "contact_email": c.get("contact_email"),
                "open_invoice_count": len(open_invoices),
                "outstanding": outstanding,
                "overdue": overdue,
                "days_overdue": days_overdue,
                "classification": rec.get("classification") if rec else None,
                "recommended_action": rec.get("recommended_action") if rec else None,
                "behavior_classification": behavior,
            }
        )
    return out


@app.get("/api/runs", tags=["read"], dependencies=[Depends(require_api_key)])
def list_runs():
    """Past triage runs, newest first, derived from the batch_results table.

    Each run reports per-classification counts and which customers changed
    classification versus the previous (older) run. Overdue value at the time of
    a run is not stored historically, so it is omitted here (it can only be
    recorded going forward)."""
    run_dates = database.get_run_dates()  # newest first
    name_by_id = {
        c["customer_id"]: (
            c.get("company_name") or c.get("contact_name") or c["customer_id"]
        )
        for c in database.get_all_customers()
    }
    results_by_date = {d: database.get_results_for_date(d) for d in run_dates}

    runs = []
    for i, run_date in enumerate(run_dates):
        results = results_by_date[run_date]
        counts = {"red": 0, "amber": 0, "green": 0}
        for r in results:
            cls = r.get("classification")
            if cls in counts:
                counts[cls] += 1
        run_time = next((r.get("run_time") for r in results if r.get("run_time")), None)

        # Compare against the previous (chronologically older) run, if any.
        older_date = run_dates[i + 1] if i + 1 < len(run_dates) else None
        changes = []
        if older_date:
            older = {
                r["customer_id"]: r.get("classification")
                for r in results_by_date[older_date]
            }
            for r in results:
                prev = older.get(r["customer_id"])
                cur = r.get("classification")
                if prev and cur and prev != cur:
                    changes.append(
                        {
                            "customer_id": r["customer_id"],
                            "name": name_by_id.get(r["customer_id"], r["customer_id"]),
                            "from": prev,
                            "to": cur,
                        }
                    )

        runs.append(
            {
                "run_date": run_date,
                "run_time": run_time,
                "total": len(results),
                "counts": counts,
                "compared_to": older_date,
                "changes": changes,
            }
        )
    return runs


@app.get(
    "/api/customers/{customer_id}",
    tags=["read"],
    dependencies=[Depends(require_api_key)],
)
def customer_detail(customer_id: str):
    """Full picture of one customer: profile, payment behaviour stats, open and
    paid invoices, communications, and the latest recommendation."""
    profile = tools.get_customer_profile(customer_id)
    if "error" in profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=profile["error"])

    payment_stats = tools.get_payment_stats(customer_id)
    if isinstance(payment_stats, dict) and "error" in payment_stats:
        payment_stats = None

    latest_results, _, _ = database.get_latest_results()
    recommendation = next(
        (r for r in latest_results if r["customer_id"] == customer_id), None
    )

    return {
        "profile": profile,
        "payment_stats": payment_stats,
        "open_invoices": database.get_open_invoices(customer_id),
        "paid_invoices": database.get_paid_invoices(customer_id),
        "communications": database.get_communications(customer_id),
        "recommendation": recommendation,
    }


# ─────────────────────────────────────────────────────────────────────
# Write endpoints
# ─────────────────────────────────────────────────────────────────────


class MarkPaidBody(BaseModel):
    paid_date: str | None = None  # defaults to today
    amount_paid: float | None = None  # None = full payment


@app.post(
    "/api/invoices/{invoice_id}/mark-paid",
    tags=["write"],
    dependencies=[Depends(require_api_key)],
)
def mark_paid(invoice_id: str, body: MarkPaidBody):
    database.mark_invoice_paid(
        invoice_id,
        body.paid_date or _today_str(),
        body.amount_paid,
    )
    return {"ok": True}


class CommunicationBody(BaseModel):
    customer_id: str
    note: str
    date_sent: str | None = None  # defaults to today
    customer_responded: bool = False
    invoice_id: str | None = None


@app.post(
    "/api/communications",
    tags=["write"],
    dependencies=[Depends(require_api_key)],
)
def log_communication(body: CommunicationBody):
    if not database.get_customer(body.customer_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer {body.customer_id} not found",
        )
    database.log_manual_communication(
        customer_id=body.customer_id,
        date_sent=body.date_sent or _today_str(),
        note=body.note,
        customer_responded=body.customer_responded,
        invoice_id=body.invoice_id,
    )
    return {"ok": True}


class EmailApprovalBody(BaseModel):
    customer_id: str
    run_date: str
    original_body: str
    approved_body: str
    invoice_id: str | None = None


@app.post(
    "/api/emails/approve",
    tags=["write"],
    dependencies=[Depends(require_api_key)],
)
def approve_email(body: EmailApprovalBody):
    database.log_email_approval(
        customer_id=body.customer_id,
        run_date=body.run_date,
        original_body=body.original_body,
        approved_body=body.approved_body,
        invoice_id=body.invoice_id,
    )
    return {"ok": True, "was_edited": body.approved_body != body.original_body}


@app.post("/api/upload", tags=["write"], dependencies=[Depends(require_api_key)])
async def upload_csv(file: UploadFile = File(...)):
    """Upload an aged-debtors CSV. Auto-detects columns, normalises, and atomically
    replaces the current dataset (handover rule: replace_dataset rolls back on
    failure, never half-imports)."""
    try:
        df = csv_handler.load_csv(file.file)
    except Exception as exc:  # pandas parse error, empty file, etc.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not read CSV: {exc}",
        )

    mapping = csv_handler.auto_detect_columns(df)
    errors = csv_handler.validate_mapping(mapping)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Missing required columns.", "errors": errors, "mapping": mapping},
        )

    customers, invoices, skipped = csv_handler.normalise(df, mapping)
    if not invoices:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No valid invoice rows found in the uploaded file.",
        )

    database.replace_dataset(customers, invoices)
    return {
        "ok": True,
        "mapping": mapping,
        "skipped_rows": skipped,
        "summary": csv_handler.summary_stats(customers, invoices),
    }


# ─────────────────────────────────────────────────────────────────────
# Triage runs — agent chain imported LAZILY (handover rule #1)
# ─────────────────────────────────────────────────────────────────────


def _serialize_run_state(state: dict) -> dict:
    finished_at = state.get("finished_at")
    return {
        "running": state.get("running", False),
        "done": state.get("done", 0),
        "total": state.get("total", 0),
        "current": state.get("current", ""),
        "error": state.get("error"),
        "finished_at": finished_at.isoformat()
        if isinstance(finished_at, datetime)
        else finished_at,
    }


@app.post("/api/triage/run", tags=["triage"], dependencies=[Depends(require_api_key)])
def triage_run():
    """Kick off a batch triage run on a background thread. Returns immediately;
    poll /api/triage/status for progress. Refuses to start a second concurrent
    run."""
    from src.runner import get_state, start_run  # lazy: pulls in the agent chain

    # Pass the current user so the background thread writes to their database.
    started = start_run(database.get_current_user_id())
    return {"started": started, "status": _serialize_run_state(get_state())}


@app.get("/api/triage/status", tags=["triage"], dependencies=[Depends(require_api_key)])
def triage_status():
    from src.runner import get_state  # lazy

    return _serialize_run_state(get_state())
