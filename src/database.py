"""
src/database.py

Single source of truth for all database access in Triage.
Uses SQLite — no server needed, single file, ships with Python.

DB file location: data/triage.db
"""

import sqlite3
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path("data/triage.db")


# ─────────────────────────────────────────────────────────────────────
# CONNECTION
# ─────────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─────────────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────────────

def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id         TEXT PRIMARY KEY,
            company_name        TEXT NOT NULL,
            customer_type       TEXT DEFAULT 'business',
            contact_name        TEXT,
            contact_email       TEXT,
            payment_terms_days  INTEGER DEFAULT 30,
            credit_limit        REAL,
            first_seen          TEXT NOT NULL,
            last_updated        TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id          TEXT PRIMARY KEY,
            customer_id         TEXT NOT NULL REFERENCES customers(customer_id),
            issue_date          TEXT NOT NULL,
            due_date            TEXT NOT NULL,
            amount              REAL NOT NULL,
            status              TEXT NOT NULL DEFAULT 'open',
            paid_date           TEXT,
            last_updated        TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS communications (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id         TEXT NOT NULL REFERENCES customers(customer_id),
            invoice_id          TEXT,
            tier                INTEGER NOT NULL,
            date_sent           TEXT NOT NULL,
            customer_responded  INTEGER NOT NULL DEFAULT 0,
            response_summary    TEXT
        );

        CREATE TABLE IF NOT EXISTS batch_results (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date                TEXT NOT NULL,
            run_time                TEXT,
            customer_id             TEXT NOT NULL REFERENCES customers(customer_id),
            classification          TEXT NOT NULL,
            recommended_action      TEXT NOT NULL,
            pattern_noticed         TEXT,
            reasoning               TEXT,
            drafted_email_subject   TEXT,
            drafted_email_body      TEXT,
            UNIQUE(run_date, customer_id)
        );

        CREATE INDEX IF NOT EXISTS idx_invoices_customer
            ON invoices(customer_id);
        CREATE INDEX IF NOT EXISTS idx_invoices_status
            ON invoices(status);
        CREATE INDEX IF NOT EXISTS idx_comms_customer
            ON communications(customer_id);
        CREATE INDEX IF NOT EXISTS idx_results_date
            ON batch_results(run_date);
        """)

        conn.executescript("""
        CREATE TABLE IF NOT EXISTS email_approvals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            approved_at     TEXT NOT NULL,
            customer_id     TEXT NOT NULL,
            invoice_id      TEXT,
            run_date        TEXT NOT NULL,
            original_body   TEXT,
            approved_body   TEXT,
            was_edited      INTEGER NOT NULL DEFAULT 0
        );
        """)

        # Columns added after initial schema — safe to re-run, silently ignored if present
        for stmt in [
            "ALTER TABLE batch_results ADD COLUMN run_time TEXT",
            "ALTER TABLE invoices ADD COLUMN amount_paid REAL",
        ]:
            try:
                conn.execute(stmt)
            except Exception:
                pass


def init_and_migrate():
    """Convenience: init schema then seed from JSON files if the DB is empty."""
    init_db()
    migrate_from_json()


# ─────────────────────────────────────────────────────────────────────
# CUSTOMERS
# ─────────────────────────────────────────────────────────────────────

def upsert_customer(customer: dict):
    """Insert or update a customer record. Preserves first_seen date."""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT first_seen FROM customers WHERE customer_id = ?",
            (customer["customer_id"],)
        ).fetchone()

        # Prefer the original account_opened date from JSON; fall back to now
        first_seen = existing["first_seen"] if existing else (customer.get("account_opened") or now)

        conn.execute("""
            INSERT INTO customers
                (customer_id, company_name, customer_type, contact_name,
                 contact_email, payment_terms_days, credit_limit,
                 first_seen, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
                company_name        = excluded.company_name,
                customer_type       = excluded.customer_type,
                contact_name        = excluded.contact_name,
                contact_email       = excluded.contact_email,
                payment_terms_days  = excluded.payment_terms_days,
                credit_limit        = excluded.credit_limit,
                last_updated        = excluded.last_updated
        """, (
            customer["customer_id"],
            customer.get("company_name") or customer.get("name", ""),
            customer.get("customer_type", "business"),
            customer.get("contact_name"),
            customer.get("contact_email"),
            customer.get("payment_terms_days", 30),
            customer.get("credit_limit"),
            first_seen,
            now,
        ))


def get_customer(customer_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        return dict(row) if row else None


def get_all_customers() -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM customers ORDER BY company_name"
        ).fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────
# INVOICES
# ─────────────────────────────────────────────────────────────────────

def upsert_invoice(invoice: dict):
    """
    Insert or update an invoice.
    Never downgrades a paid invoice back to open.
    """
    now = datetime.now().isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT status, paid_date FROM invoices WHERE invoice_id = ?",
            (invoice["invoice_id"],)
        ).fetchone()

        if existing and existing["status"] == "paid":
            status    = "paid"
            paid_date = existing["paid_date"]
        else:
            status    = invoice.get("status", "open")
            paid_date = invoice.get("paid_date")

        conn.execute("""
            INSERT INTO invoices
                (invoice_id, customer_id, issue_date, due_date,
                 amount, status, paid_date, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(invoice_id) DO UPDATE SET
                customer_id  = excluded.customer_id,
                issue_date   = excluded.issue_date,
                due_date     = excluded.due_date,
                amount       = excluded.amount,
                status       = CASE
                                 WHEN invoices.status = 'paid' THEN 'paid'
                                 ELSE excluded.status
                               END,
                paid_date    = CASE
                                 WHEN invoices.status = 'paid' THEN invoices.paid_date
                                 ELSE excluded.paid_date
                               END,
                last_updated = excluded.last_updated
        """, (
            invoice["invoice_id"],
            invoice["customer_id"],
            invoice["issue_date"],
            invoice["due_date"],
            invoice["amount"],
            status,
            paid_date,
            now,
        ))


def get_open_invoices(customer_id: str) -> list:
    """Return open/overdue invoices with computed days_outstanding and days_past_due."""
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT *,
                   julianday(?) - julianday(issue_date) AS days_outstanding,
                   julianday(?) - julianday(due_date)   AS days_past_due
            FROM invoices
            WHERE customer_id = ? AND status IN ('open', 'overdue', 'partial')
            ORDER BY due_date ASC
        """, (today, today, customer_id)).fetchall()

        result = []
        for r in rows:
            inv = dict(r)
            inv["days_outstanding"] = max(0, int(inv["days_outstanding"] or 0))
            inv["days_past_due"]    = max(0, int(inv["days_past_due"] or 0))
            result.append(inv)
        return result


def get_all_invoices(customer_id: str) -> list:
    """Return all invoices (open + paid) for a customer, ordered by issue_date."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM invoices
            WHERE customer_id = ?
            ORDER BY issue_date ASC
        """, (customer_id,)).fetchall()
        return [dict(r) for r in rows]


def get_paid_invoices(customer_id: str) -> list:
    """Return all paid (and partial) invoices for the audit trail, newest first."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM invoices
            WHERE customer_id = ? AND status IN ('paid', 'partial')
            ORDER BY paid_date DESC
        """, (customer_id,)).fetchall()
        return [dict(r) for r in rows]


def mark_invoice_paid(invoice_id: str, paid_date: str, amount_paid: float = None):
    """
    Mark an invoice as paid (or partial if amount_paid < invoice amount).
    amount_paid=None means fully paid for the invoice amount.
    """
    now = datetime.now().isoformat()
    with get_conn() as conn:
        inv = conn.execute(
            "SELECT amount FROM invoices WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()
        if not inv:
            return

        full_amount = inv["amount"]
        if amount_paid is None or round(amount_paid, 2) >= round(full_amount, 2):
            status = "paid"
            amount_paid = full_amount
        else:
            status = "partial"

        conn.execute("""
            UPDATE invoices
            SET status = ?, paid_date = ?, amount_paid = ?, last_updated = ?
            WHERE invoice_id = ?
        """, (status, paid_date, round(amount_paid, 2), now, invoice_id))


# ─────────────────────────────────────────────────────────────────────
# COMMUNICATIONS
# ─────────────────────────────────────────────────────────────────────

def add_communication(comm: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO communications
                (customer_id, invoice_id, tier, date_sent,
                 customer_responded, response_summary)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            comm["customer_id"],
            comm.get("invoice_id"),
            comm["tier"],
            comm["date_sent"],
            1 if comm.get("customer_responded") else 0,
            comm.get("response_summary"),
        ))


def log_manual_communication(customer_id: str, date_sent: str, note: str,
                             customer_responded: bool, invoice_id=None):
    """Record a communication logged by hand — a phone call made, or a reply
    received outside the system. Stored with tier 0 to distinguish it from the
    automated reminder tiers (1/2/3). Left unlinked to an invoice by default,
    so it stays a free-form note and does not alter the agent's invoice-scoped
    reminder logic."""
    add_communication({
        "customer_id":        customer_id,
        "invoice_id":         invoice_id,
        "tier":               0,
        "date_sent":          date_sent,
        "customer_responded": customer_responded,
        "response_summary":   note,
    })


def get_communications(customer_id: str) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM communications
            WHERE customer_id = ?
            ORDER BY date_sent ASC
        """, (customer_id,)).fetchall()
        result = []
        for r in rows:
            c = dict(r)
            c["customer_responded"] = bool(c["customer_responded"])
            result.append(c)
        return result


# ─────────────────────────────────────────────────────────────────────
# EMAIL APPROVALS
# ─────────────────────────────────────────────────────────────────────

def log_email_approval(customer_id: str, run_date: str, original_body: str,
                       approved_body: str, invoice_id: str = None):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO email_approvals
                (approved_at, customer_id, invoice_id, run_date,
                 original_body, approved_body, was_edited)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            customer_id,
            invoice_id,
            run_date,
            original_body,
            approved_body,
            1 if approved_body != original_body else 0,
        ))


# ─────────────────────────────────────────────────────────────────────
# BATCH RESULTS
# ─────────────────────────────────────────────────────────────────────

def save_batch_result(result: dict):
    """Save one customer's agent result. Upserts by run_date + customer_id."""
    email = result.get("drafted_email") or {}
    run_time = result.get("run_time") or datetime.now().strftime("%H:%M")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO batch_results
                (run_date, run_time, customer_id, classification, recommended_action,
                 pattern_noticed, reasoning,
                 drafted_email_subject, drafted_email_body)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_date, customer_id) DO UPDATE SET
                run_time              = excluded.run_time,
                classification        = excluded.classification,
                recommended_action    = excluded.recommended_action,
                pattern_noticed       = excluded.pattern_noticed,
                reasoning             = excluded.reasoning,
                drafted_email_subject = excluded.drafted_email_subject,
                drafted_email_body    = excluded.drafted_email_body
        """, (
            result.get("run_date", date.today().isoformat()),
            run_time,
            result["customer_id"],
            result["classification"],
            result["recommended_action"],
            result.get("pattern_noticed"),
            result.get("reasoning"),
            email.get("subject"),
            email.get("body"),
        ))


def _rows_to_results(rows) -> list:
    """Convert batch_results rows to the dict shape app.py expects."""
    results = []
    for r in rows:
        result = dict(r)
        if result.get("drafted_email_subject"):
            result["drafted_email"] = {
                "subject": result["drafted_email_subject"],
                "body":    result["drafted_email_body"] or "",
            }
        else:
            result["drafted_email"] = None
        results.append(result)
    return results


def get_latest_results() -> tuple:
    """Return (results_list, run_time_str) for the most recent batch run."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(run_date) AS run_date FROM batch_results"
        ).fetchone()
        if not row or not row["run_date"]:
            return [], "—"

        run_date = row["run_date"]
        rows = conn.execute(
            "SELECT * FROM batch_results WHERE run_date = ?", (run_date,)
        ).fetchall()

        results = _rows_to_results(rows)
        run_time = rows[0]["run_time"] if rows and rows[0]["run_time"] else run_date
        return results, run_time


def get_results_for_date(run_date: str) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM batch_results WHERE run_date = ?", (run_date,)
        ).fetchall()
        return _rows_to_results(rows)


def get_run_dates() -> list:
    """Return all dates that have batch results, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT run_date FROM batch_results ORDER BY run_date DESC"
        ).fetchall()
        return [r["run_date"] for r in rows]


# ─────────────────────────────────────────────────────────────────────
# DATASET MANAGEMENT
# ─────────────────────────────────────────────────────────────────────

def clear_dataset():
    """
    Wipe all customers, invoices, communications, and batch results.
    Called before loading a new CSV so there's no stale data mixing.
    """
    with get_conn() as conn:
        conn.executescript("""
            DELETE FROM batch_results;
            DELETE FROM communications;
            DELETE FROM invoices;
            DELETE FROM customers;
        """)


# ─────────────────────────────────────────────────────────────────────
# MIGRATION — JSON → SQLite
# ─────────────────────────────────────────────────────────────────────

def migrate_from_json():
    """
    One-time migration: import existing JSON files into SQLite.
    Safe to call on every startup — skips if the DB already has customers.
    """
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        if count > 0:
            return

    data_dir = Path("data")
    customers, invoices, comms = [], [], []

    customers_path = data_dir / "customers.json"
    if customers_path.exists():
        with open(customers_path) as f:
            customers = json.load(f)
        for c in customers:
            upsert_customer(c)

    invoices_path = data_dir / "invoices.json"
    if invoices_path.exists():
        with open(invoices_path) as f:
            invoices = json.load(f)
        for inv in invoices:
            upsert_invoice(inv)

    comms_path = data_dir / "communications.json"
    if comms_path.exists():
        with open(comms_path) as f:
            comms = json.load(f)
        for c in comms:
            add_communication(c)

    briefings_dir = Path("briefings")
    if briefings_dir.exists():
        for results_file in sorted(briefings_dir.glob("results_*.json")):
            run_date = results_file.stem.replace("results_", "")
            with open(results_file) as f:
                results = json.load(f)
            for r in results:
                r["run_date"] = run_date
                r["run_time"] = None  # no time available for historical data
                save_batch_result(r)

    print(f"DB migration complete: {len(customers)} customers, {len(invoices)} invoices, {len(comms)} comms")
