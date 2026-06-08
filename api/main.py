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

import base64
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime

import anthropic
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


def _customer_currency(customer_id: str) -> str:
    """The currency to present a customer's figures in: the currency of their
    open invoices (the first, since they share a ledger), defaulting to EUR.
    Drives whether the frontend may rewrite a stray £ to € in agent text."""
    for inv in database.get_open_invoices(customer_id):
        if inv.get("currency"):
            return inv["currency"]
    return "EUR"


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
        "currency": _customer_currency(cid),
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
                "currency": _customer_currency(cid),
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
# PDF invoice upload — extract with Claude vision, confirm, then store
#
# For pilots (e.g. DATEV users) who can only export individual invoice PDFs,
# not an aged-debtors CSV. /pdf extracts fields for the user to confirm/edit;
# /pdf/confirm writes the confirmed invoice through the SAME merge/upsert path
# as the CSV upload (replace_dataset preserves existing data and paid history).
#
# anthropic is safe to import at module top — unlike the agent chain it does
# NOT read st.secrets / run load_dotenv() at import (handover rule #1). The
# client reads ANTHROPIC_API_KEY from the env load_dotenv() set up above.
# ─────────────────────────────────────────────────────────────────────

PDF_EXTRACTION_MODEL = "claude-sonnet-4-20250514"

# A tool schema forces Claude to return structured JSON rather than prose we
# would have to parse out of markdown. Every field is optional — anything the
# model can't find on the invoice is simply omitted and left blank for the user.
_EXTRACTION_TOOL = {
    "name": "record_invoice",
    "description": "Record the fields extracted from a single invoice PDF.",
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_name": {
                "type": "string",
                "description": "The customer being billed (the bill-to / recipient), "
                "NOT the sender/vendor issuing the invoice.",
            },
            "invoice_number": {"type": "string", "description": "Invoice number or reference."},
            "invoice_date": {"type": "string", "description": "Invoice/issue date as YYYY-MM-DD."},
            "due_date": {"type": "string", "description": "Payment due date as YYYY-MM-DD."},
            "amount": {
                "type": "number",
                "description": "Total amount due, numeric only — no currency symbol or "
                "thousands separators.",
            },
            "currency": {"type": "string", "description": "ISO currency code, e.g. EUR, GBP, USD."},
            "contact_email": {"type": "string", "description": "Billing contact email, if shown."},
        },
        "required": [],
    },
}

_EXTRACTION_PROMPT = (
    "This PDF is a single invoice. Extract the billing fields using the "
    "record_invoice tool. The customer is the party being billed (bill-to / "
    "recipient), not the company that issued the invoice. Give dates as "
    "YYYY-MM-DD and the amount as a plain number (the total due, no symbols). "
    "Omit any field you cannot find on the document rather than guessing."
)


def _extract_invoice_from_pdf(pdf_bytes: bytes) -> dict:
    """Send the PDF to Claude (vision) and return the raw extracted field dict.
    Raises on any API/transport failure; the caller maps that to a 422."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    b64 = base64.standard_b64encode(pdf_bytes).decode()
    message = client.messages.create(
        model=PDF_EXTRACTION_MODEL,
        max_tokens=1024,
        tools=[_EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "record_invoice"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _EXTRACTION_PROMPT},
                ],
            }
        ],
    )
    for block in message.content:
        if block.type == "tool_use" and block.name == "record_invoice":
            return block.input or {}
    return {}


def _clean_str(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _status_and_days_overdue(due_date: str | None) -> tuple[str, int]:
    """Default status from the due date: overdue (with a positive day count) if
    it is in the past, otherwise open."""
    if not due_date:
        return "open", 0
    try:
        due = datetime.strptime(due_date, "%Y-%m-%d").date()
    except ValueError:
        return "open", 0
    days = (date.today() - due).days
    return ("overdue", days) if days > 0 else ("open", 0)


@app.post("/api/upload/pdf", tags=["write"], dependencies=[Depends(require_api_key)])
async def upload_pdf(file: UploadFile = File(...)):
    """Extract invoice fields from an uploaded PDF for the user to confirm. Does
    NOT touch the database — the frontend shows these in an editable form and
    posts the confirmed values to /api/upload/pdf/confirm."""
    is_pdf = (file.content_type == "application/pdf") or (
        file.filename or ""
    ).lower().endswith(".pdf")
    if not is_pdf:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please upload a PDF file.",
        )

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded PDF is empty.",
        )

    try:
        raw = _extract_invoice_from_pdf(pdf_bytes)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not extract invoice data. Please check the PDF and try again.",
        )

    # Normalise dates/amount through the same parsers the CSV path uses, so the
    # confirmation form is pre-filled with clean, consistent values.
    invoice_date = csv_handler._parse_date(raw["invoice_date"]) if raw.get("invoice_date") else None
    due_date = csv_handler._parse_date(raw["due_date"]) if raw.get("due_date") else None
    amount = csv_handler._parse_amount(raw["amount"]) if raw.get("amount") is not None else None
    status_value, days_overdue = _status_and_days_overdue(due_date)

    return {
        "customer_name": _clean_str(raw.get("customer_name")),
        "invoice_number": _clean_str(raw.get("invoice_number")),
        "invoice_date": invoice_date,
        "due_date": due_date,
        "amount": amount,
        "currency": (_clean_str(raw.get("currency")) or "EUR").upper(),
        "contact_email": _clean_str(raw.get("contact_email")),
        "status": status_value,
        "days_overdue": days_overdue,
    }


class PdfInvoiceBody(BaseModel):
    customer_name: str
    invoice_number: str | None = None
    invoice_date: str
    due_date: str
    amount: float
    currency: str | None = None
    contact_email: str | None = None
    status: str | None = None  # overdue / open / paid; defaults from due_date


@app.post(
    "/api/upload/pdf/confirm",
    tags=["write"],
    dependencies=[Depends(require_api_key)],
)
def confirm_pdf(body: PdfInvoiceBody):
    """Persist a confirmed/edited PDF invoice via the same merge/upsert path as
    the CSV upload: reconciles the customer by name against existing ones,
    upserts the invoice, and never downgrades paid history. Returns the same
    response shape as /api/upload."""
    name = (body.customer_name or "").strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Customer name is required.",
        )

    issue_date = csv_handler._parse_date(body.invoice_date)
    due_date = csv_handler._parse_date(body.due_date)
    if not issue_date or not due_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Valid invoice and due dates are required (YYYY-MM-DD).",
        )

    amount = csv_handler._parse_amount(body.amount)
    if amount is None or amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A positive invoice amount is required.",
        )

    invoice_id = (body.invoice_number or "").strip() or f"INV-{str(uuid.uuid4())[:8].upper()}"

    allowed = {"open", "overdue", "paid", "partial"}
    status_value = (body.status or "").strip().lower()
    if status_value not in allowed:
        status_value, _ = _status_and_days_overdue(due_date)
    # A paid/partial invoice must carry a payment date so days-to-pay is
    # computable downstream; fall back to the due date (mirrors csv_handler).
    paid_date = due_date if status_value in ("paid", "partial") else None

    try:
        terms_days = max(
            0,
            (
                datetime.strptime(due_date, "%Y-%m-%d").date()
                - datetime.strptime(issue_date, "%Y-%m-%d").date()
            ).days,
        )
    except ValueError:
        terms_days = 30

    customer = {
        "customer_id": "C001",  # positional; replace_dataset reconciles by name
        "customer_type": "business",
        "company_name": name,
        "contact_name": None,
        "contact_email": _clean_str(body.contact_email),
        "account_opened": _today_str(),
        "payment_terms_days": terms_days,
        "credit_limit": None,
    }
    invoice = {
        "invoice_id": invoice_id,
        "customer_id": "C001",
        "issue_date": issue_date,
        "due_date": due_date,
        "amount": round(amount, 2),
        "currency": (_clean_str(body.currency) or "EUR").upper(),
        "status": status_value,
        "paid_date": paid_date,
    }

    database.replace_dataset([customer], [invoice])
    return {
        "ok": True,
        "mapping": None,
        "skipped_rows": 0,
        "summary": csv_handler.summary_stats([customer], [invoice]),
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
