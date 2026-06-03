"""
tests/test_core.py — Core unit tests for Triage.

Covers the data plumbing that everything else depends on:
  - CSV normalisation (csv_handler) against a small sample CSV
  - SQLite upsert + retrieval (database)
  - get_open_invoices computing days_past_due correctly (tools/database)
  - migrate_from_json completing without errors

Run:  pytest tests/ -v   (from the repo root)

Every test that touches the database uses the `db` fixture, which points the
database module at a throwaway SQLite file in a temp dir and chdirs there — so
the real data/triage.db and data/*.json are never touched.
"""

import sys
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

# Make `src` importable regardless of how pytest is invoked.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import database, tools, csv_handler, stats  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    """Isolated, in-memory SQLite database for fast, fully-isolated DB tests.

    database.get_conn() normally opens a *new* connection on every call. A bare
    ':memory:' path would therefore give each call its own private, empty
    database — writes would never be visible to later reads. So we create ONE
    persistent in-memory connection and monkeypatch get_conn to hand it back
    every time. SQLite's `with conn:` context manager commits (or rolls back)
    but does NOT close, so the single connection safely survives every
    `with get_conn() as conn:` block in the module.

    We also chdir into tmp_path so any relative paths (the 'data/' dir, the
    JSON migration source) resolve inside the temp dir and never touch real
    project files. The connection is closed on teardown.
    """
    monkeypatch.chdir(tmp_path)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    monkeypatch.setattr(database, "get_conn", lambda: conn)

    database.init_db()
    try:
        yield database
    finally:
        conn.close()


SAMPLE_CSV = (
    "Customer Name,Invoice Number,Amount Due,Invoice Date,Due Date\n"
    "Acme Ltd,INV-001,1000.00,2026-01-01,2026-01-31\n"
    "Acme Ltd,INV-002,500.00,2026-02-01,2026-03-03\n"
    'Globex,INV-003,"£2,500.50",2026-03-01,2026-03-31\n'
    ",INV-004,100,2026-04-01,2026-04-30\n"           # no customer name -> skipped
    "Beta,INV-005,not-a-number,2026-05-01,2026-05-31\n"  # bad amount    -> skipped
)

EXPLICIT_MAPPING = {
    "customer_name": "Customer Name",
    "invoice_id":    "Invoice Number",
    "amount":        "Amount Due",
    "issue_date":    "Invoice Date",
    "due_date":      "Due Date",
}


@pytest.fixture
def sample_csv(tmp_path):
    p = tmp_path / "ledger.csv"
    p.write_text(SAMPLE_CSV, encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────────────────────
# CSV normalisation
# ─────────────────────────────────────────────────────────────────────

def test_csv_autodetect_and_validate(sample_csv):
    df = csv_handler.load_csv(sample_csv)
    mapping = csv_handler.auto_detect_columns(df)

    # All required fields should be auto-detected from these clear headers.
    for field in csv_handler.REQUIRED_FIELDS:
        assert mapping.get(field), f"{field} was not auto-detected (got {mapping.get(field)!r})"

    assert mapping["customer_name"] == "Customer Name"
    assert mapping["invoice_id"] == "Invoice Number"
    assert mapping["amount"] == "Amount Due"
    assert mapping["issue_date"] == "Invoice Date"
    assert mapping["due_date"] == "Due Date"

    # A fully-mapped CSV produces no validation errors.
    assert csv_handler.validate_mapping(mapping) == []


def test_validate_mapping_flags_missing_required():
    incomplete = {"customer_name": "A", "invoice_id": "B"}  # amount/dates missing
    errors = csv_handler.validate_mapping(incomplete)
    assert len(errors) == 3  # amount, issue_date, due_date
    assert any("amount" in e for e in errors)


def test_csv_normalise(sample_csv):
    df = csv_handler.load_csv(sample_csv)
    customers, invoices, skipped = csv_handler.normalise(df, EXPLICIT_MAPPING)

    # Two valid customers (Acme Ltd, Globex); two rows dropped (no name, bad amount).
    assert len(customers) == 2
    assert len(invoices) == 3
    assert skipped == 2

    names = {c["company_name"] for c in customers}
    assert names == {"Acme Ltd", "Globex"}

    # Currency symbol + thousands separator are stripped when parsing the amount.
    globex_id = next(c["customer_id"] for c in customers if c["company_name"] == "Globex")
    globex_inv = next(i for i in invoices if i["customer_id"] == globex_id)
    assert globex_inv["amount"] == pytest.approx(2500.50)

    # payment_terms_days derived from due - issue (30 days here).
    acme = next(c for c in customers if c["company_name"] == "Acme Ltd")
    assert acme["payment_terms_days"] == 30

    # Normalised invoices start life as 'open'.
    assert all(inv["status"] == "open" for inv in invoices)


def test_summary_stats(sample_csv):
    df = csv_handler.load_csv(sample_csv)
    customers, invoices, _ = csv_handler.normalise(df, EXPLICIT_MAPPING)
    stats = csv_handler.summary_stats(customers, invoices)

    assert stats["total_customers"] == 2
    assert stats["total_invoices"] == 3
    assert stats["total_outstanding"] == pytest.approx(4000.50)
    assert stats["total_overdue"] <= stats["total_outstanding"]


# ─────────────────────────────────────────────────────────────────────
# Database upsert + retrieval
# ─────────────────────────────────────────────────────────────────────

def test_customer_upsert_and_retrieval(db):
    db.upsert_customer({
        "customer_id": "C001",
        "company_name": "Acme Ltd",
        "contact_email": "ap@acme.test",
        "payment_terms_days": 14,
        "credit_limit": 5000,
        "account_opened": "2026-01-01",
    })

    got = db.get_customer("C001")
    assert got is not None
    assert got["company_name"] == "Acme Ltd"
    assert got["payment_terms_days"] == 14
    assert got["contact_email"] == "ap@acme.test"

    # Upsert again with changed fields: updates in place, no duplicate row.
    db.upsert_customer({
        "customer_id": "C001",
        "company_name": "Acme Limited",
        "payment_terms_days": 30,
    })
    got = db.get_customer("C001")
    assert got["company_name"] == "Acme Limited"
    assert got["payment_terms_days"] == 30
    assert len(db.get_all_customers()) == 1

    assert db.get_customer("C999") is None


def test_invoice_upsert_and_retrieval(db):
    db.upsert_customer({"customer_id": "C001", "company_name": "Acme Ltd"})
    db.upsert_invoice({
        "invoice_id": "INV-001",
        "customer_id": "C001",
        "issue_date": "2026-01-01",
        "due_date": "2026-01-31",
        "amount": 1000.0,
        "status": "open",
    })

    all_invs = db.get_all_invoices("C001")
    assert len(all_invs) == 1
    assert all_invs[0]["invoice_id"] == "INV-001"
    assert all_invs[0]["amount"] == pytest.approx(1000.0)
    assert all_invs[0]["status"] == "open"


def test_paid_invoice_not_downgraded_to_open(db):
    """upsert_invoice must never flip a paid invoice back to open."""
    db.upsert_customer({"customer_id": "C001", "company_name": "Acme Ltd"})
    db.upsert_invoice({
        "invoice_id": "INV-001", "customer_id": "C001",
        "issue_date": "2026-01-01", "due_date": "2026-01-31",
        "amount": 1000.0, "status": "paid", "paid_date": "2026-01-20",
    })
    # A re-import that thinks it's open should be ignored for status.
    db.upsert_invoice({
        "invoice_id": "INV-001", "customer_id": "C001",
        "issue_date": "2026-01-01", "due_date": "2026-01-31",
        "amount": 1000.0, "status": "open",
    })
    inv = db.get_all_invoices("C001")[0]
    assert inv["status"] == "paid"


# ─────────────────────────────────────────────────────────────────────
# get_open_invoices: days_past_due
# ─────────────────────────────────────────────────────────────────────

def test_get_open_invoices_days_past_due(db):
    today = date.today()
    overdue_due = (today - timedelta(days=10)).isoformat()
    future_due = (today + timedelta(days=5)).isoformat()
    issued = (today - timedelta(days=40)).isoformat()

    db.upsert_customer({"customer_id": "C001", "company_name": "Acme Ltd"})
    db.upsert_invoice({
        "invoice_id": "INV-OVERDUE", "customer_id": "C001",
        "issue_date": issued, "due_date": overdue_due,
        "amount": 500.0, "status": "open",
    })
    db.upsert_invoice({
        "invoice_id": "INV-FUTURE", "customer_id": "C001",
        "issue_date": issued, "due_date": future_due,
        "amount": 200.0, "status": "open",
    })

    invs = tools.get_open_invoices("C001")
    assert isinstance(invs, list)
    by_id = {i["invoice_id"]: i for i in invs}

    assert by_id["INV-OVERDUE"]["days_past_due"] == 10
    # Not yet due -> clamped to 0, never negative.
    assert by_id["INV-FUTURE"]["days_past_due"] == 0
    # days_outstanding measured from issue date.
    assert by_id["INV-OVERDUE"]["days_outstanding"] == 40


def test_get_open_invoices_unknown_customer(db):
    result = tools.get_open_invoices("C404")
    assert isinstance(result, dict)
    assert "error" in result


# ─────────────────────────────────────────────────────────────────────
# migrate_from_json
# ─────────────────────────────────────────────────────────────────────

def test_migrate_from_json_completes(db, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "customers.json").write_text(json.dumps([
        {"customer_id": "C001", "company_name": "Acme Ltd",
         "payment_terms_days": 30, "account_opened": "2026-01-01"},
    ]))
    (data_dir / "invoices.json").write_text(json.dumps([
        {"invoice_id": "INV-001", "customer_id": "C001",
         "issue_date": "2026-01-01", "due_date": "2026-01-31",
         "amount": 1000.0, "status": "open"},
    ]))

    # Should complete without raising and load the JSON into the empty DB.
    db.migrate_from_json()

    customers = db.get_all_customers()
    assert len(customers) == 1
    assert customers[0]["company_name"] == "Acme Ltd"
    assert len(db.get_all_invoices("C001")) == 1


def test_migrate_from_json_idempotent(db, tmp_path):
    """Second run is a no-op (DB already populated) and must not raise/duplicate."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "customers.json").write_text(json.dumps([
        {"customer_id": "C001", "company_name": "Acme Ltd",
         "payment_terms_days": 30, "account_opened": "2026-01-01"},
    ]))

    db.migrate_from_json()
    db.migrate_from_json()  # second call returns early
    assert len(db.get_all_customers()) == 1


def test_migrate_from_json_no_files_ok(db):
    """With no JSON files present, migration just completes quietly."""
    db.migrate_from_json()
    assert db.get_all_customers() == []


# ─────────────────────────────────────────────────────────────────────
# stats.compute_payment_stats
# ─────────────────────────────────────────────────────────────────────

def _paid_invoice(issue, due, paid, *, inv_id, amount=1000.0):
    return {
        "invoice_id": inv_id,
        "issue_date": issue,
        "due_date":   due,
        "paid_date":  paid,
        "amount":     amount,
        "status":     "paid",
    }


def _build_history(days_late_per_invoice, *, terms_days=30, base=date(2025, 1, 1)):
    """Build a chronological list of paid invoices with a known lateness profile.

    days_late_per_invoice[i] is how many days past the due date invoice i was
    paid (negative = paid early). All dates land in 2025 — comfortably outside
    the 60-day 'recent' window from any plausible run date — so the result is
    deterministic regardless of when the test runs.
    """
    invoices = []
    for i, days_late in enumerate(days_late_per_invoice):
        issue = base + timedelta(days=i * 35)        # well-spaced, strictly ordered
        due   = issue + timedelta(days=terms_days)
        paid  = due + timedelta(days=days_late)
        invoices.append(_paid_invoice(
            issue.isoformat(), due.isoformat(), paid.isoformat(),
            inv_id=f"INV-{i:03d}",
        ))
    return invoices


def test_compute_payment_stats_slipping_customer():
    """6 paid invoices, half on-time/early and half increasingly late.

    days_late = [-2, -1, 0, 5, 10, 15]
      avg_days_late      = mean([-2,-1,0,5,10,15])          = 4.5
      reliability_score  = 3 on-time (<=0 late) / 6          = 0.5
      days_to_pay        = [28,29,30,35,40,45] (Net 30)
      trend              = recent [35,40,45] avg 40 vs hist
                           [28,29,30] avg 29 (std 1) -> deteriorating
      classification     = avg_days_late in (1,15], reliability < 0.9,
                           variance not erratic -> 'slightly_late'
    """
    history = _build_history([-2, -1, 0, 5, 10, 15])
    result = stats.compute_payment_stats(history)

    assert result["total_invoices_paid"] == 6
    assert result["avg_days_late"] == 4.5
    assert result["reliability_score"] == 0.5
    assert result["trend"] == "deteriorating"
    assert result["behavior_classification"] == "slightly_late"


def test_compute_payment_stats_reliable_customer():
    """6 invoices all paid 3 days early: perfectly reliable and stable."""
    history = _build_history([-3, -3, -3, -3, -3, -3])
    result = stats.compute_payment_stats(history)

    assert result["total_invoices_paid"] == 6
    assert result["avg_days_late"] == -3.0
    assert result["reliability_score"] == 1.0
    assert result["trend"] == "stable"            # no variation between windows
    assert result["behavior_classification"] == "reliable"


def test_compute_payment_stats_high_risk_customer():
    """Chronically very overdue (avg_days_late > 30) -> 'high_risk'."""
    history = _build_history([35, 40, 38, 45, 42, 50])
    result = stats.compute_payment_stats(history)

    assert result["reliability_score"] == 0.0     # never on time
    assert result["avg_days_late"] == pytest.approx(41.7, abs=0.05)
    assert result["behavior_classification"] == "high_risk"


# ─────────────────────────────────────────────────────────────────────
# stats — partial payments
# ─────────────────────────────────────────────────────────────────────

def _partial_invoice(issue, due, paid, *, inv_id, amount=1000.0, amount_paid=600.0):
    return {
        "invoice_id": inv_id, "issue_date": issue, "due_date": due,
        "paid_date": paid, "amount": amount, "amount_paid": amount_paid,
        "status": "partial",
    }


def test_partial_payment_counts_as_partial_reliability_credit():
    """An on-time partial payment earns credit equal to the proportion paid
    (amount_paid / amount), not a flat 1.0."""
    base = date(2025, 1, 1)
    invoices = []
    for i in range(5):
        issue = base + timedelta(days=i * 35)
        due = issue + timedelta(days=30)
        paid = due - timedelta(days=10)  # paid on time
        invoices.append(_partial_invoice(
            issue.isoformat(), due.isoformat(), paid.isoformat(),
            inv_id=f"INV-{i:03d}", amount=1000.0, amount_paid=600.0,  # 60% paid
        ))

    result = stats.compute_payment_stats(invoices)

    # On time, but only 60% paid -> 0.6 credit each, not 1.0.
    assert result["reliability_score"] == 0.6
    assert result["behavior_classification"] != "reliable"


def test_partial_payment_weights_avg_days_to_pay():
    """avg_days_to_pay is weighted by the proportion paid: a half-paid invoice
    pulls the average half as hard as a fully paid one."""
    base = date(2025, 1, 1)

    def _mk(i, days_to_pay, full):
        issue = base + timedelta(days=i * 40)
        due = issue + timedelta(days=30)
        paid = issue + timedelta(days=days_to_pay)
        if full:
            return _paid_invoice(issue.isoformat(), due.isoformat(),
                                 paid.isoformat(), inv_id=f"F{i}")
        return _partial_invoice(issue.isoformat(), due.isoformat(),
                                paid.isoformat(), inv_id=f"P{i}",
                                amount=1000.0, amount_paid=500.0)  # 50%

    invoices = [_mk(0, 20, True), _mk(1, 40, False), _mk(2, 40, False)]
    result = stats.compute_payment_stats(invoices)

    # Weighted: (1*20 + 0.5*40 + 0.5*40) / (1 + 0.5 + 0.5) = 60 / 2 = 30.
    # (The unweighted mean would be 33.3.)
    assert result["avg_days_to_pay"] == 30.0


def test_only_partial_payments_never_classified_reliable():
    """A customer who only ever pays part of each bill is not 'reliable', even
    when those part-payments are all on time and a high proportion."""
    base = date(2025, 1, 1)
    invoices = []
    for i in range(6):
        issue = base + timedelta(days=i * 35)
        due = issue + timedelta(days=30)
        paid = due - timedelta(days=5)  # on time
        invoices.append(_partial_invoice(
            issue.isoformat(), due.isoformat(), paid.isoformat(),
            inv_id=f"INV-{i:03d}", amount=1000.0, amount_paid=900.0,  # 90% paid
        ))

    result = stats.compute_payment_stats(invoices)

    # 90% on-time credit would read as 'reliable' for full payments; only-partial
    # demotes it.
    assert result["reliability_score"] == 0.9
    assert result["behavior_classification"] != "reliable"
    assert result["behavior_classification"] == "slow_but_consistent"


def test_get_payment_stats_includes_partial_invoices(db):
    """tools.get_payment_stats feeds partial invoices into the stats (it used to
    drop everything that wasn't status 'paid')."""
    db.upsert_customer({"customer_id": "C001", "company_name": "Acme Ltd"})
    db.upsert_invoice({
        "invoice_id": "F1", "customer_id": "C001",
        "issue_date": "2025-01-01", "due_date": "2025-01-31",
        "amount": 1000.0, "status": "paid", "paid_date": "2025-01-20",
    })
    db.upsert_invoice({
        "invoice_id": "P1", "customer_id": "C001",
        "issue_date": "2025-02-01", "due_date": "2025-03-03",
        "amount": 1000.0, "status": "open",
    })
    db.mark_invoice_paid("P1", "2025-02-15", amount_paid=400.0)  # -> partial

    result = tools.get_payment_stats("C001")

    # Both the fully paid and the partial invoice now count as history.
    assert result["total_invoices_paid"] == 2


# ─────────────────────────────────────────────────────────────────────
# get_communications_log: 60-day window
# ─────────────────────────────────────────────────────────────────────

def test_communications_log_60_day_window(db):
    """Comms attached to invoices paid more than 60 days ago are dropped;
    comms on recently-paid or still-open invoices are kept."""
    today = date.today()
    db.upsert_customer({"customer_id": "C001", "company_name": "Acme Ltd"})

    # Paid 90 days ago -> outside the 60-day window -> comm excluded.
    db.upsert_invoice({
        "invoice_id": "INV-OLD", "customer_id": "C001",
        "issue_date": (today - timedelta(days=200)).isoformat(),
        "due_date":   (today - timedelta(days=170)).isoformat(),
        "amount": 100.0, "status": "paid",
        "paid_date":  (today - timedelta(days=90)).isoformat(),
    })
    # Paid 10 days ago -> inside the 60-day window -> comm included.
    db.upsert_invoice({
        "invoice_id": "INV-RECENT", "customer_id": "C001",
        "issue_date": (today - timedelta(days=40)).isoformat(),
        "due_date":   (today - timedelta(days=10)).isoformat(),
        "amount": 200.0, "status": "paid",
        "paid_date":  (today - timedelta(days=10)).isoformat(),
    })
    # Still open -> always included regardless of dates.
    db.upsert_invoice({
        "invoice_id": "INV-OPEN", "customer_id": "C001",
        "issue_date": (today - timedelta(days=20)).isoformat(),
        "due_date":   (today + timedelta(days=10)).isoformat(),
        "amount": 300.0, "status": "open",
    })

    # One reminder per invoice, all sent recently.
    for inv_id in ("INV-OLD", "INV-RECENT", "INV-OPEN"):
        db.add_communication({
            "customer_id": "C001", "invoice_id": inv_id, "tier": 1,
            "date_sent": (today - timedelta(days=5)).isoformat(),
            "customer_responded": False,
            "response_summary": f"reminder re {inv_id}",
        })

    log = tools.get_communications_log("C001")
    returned_ids = {c["invoice_id"] for c in log}

    assert "INV-OLD" not in returned_ids        # paid > 60 days ago -> excluded
    assert "INV-RECENT" in returned_ids         # paid within 60 days -> included
    assert "INV-OPEN" in returned_ids           # open -> always included
    assert len(log) == 2


# ─────────────────────────────────────────────────────────────────────
# mark_invoice_paid
# ─────────────────────────────────────────────────────────────────────

def _open_invoice(db, invoice_id, amount=1000.0):
    db.upsert_customer({"customer_id": "C001", "company_name": "Acme Ltd"})
    db.upsert_invoice({
        "invoice_id": invoice_id, "customer_id": "C001",
        "issue_date": "2026-01-01", "due_date": "2026-01-31",
        "amount": amount, "status": "open",
    })


def test_mark_invoice_paid_full(db):
    """amount_paid=None means fully paid: status 'paid', paid_date + amount set."""
    _open_invoice(db, "INV-1", amount=1000.0)
    db.mark_invoice_paid("INV-1", "2026-02-05")  # no amount -> full

    inv = db.get_all_invoices("C001")[0]
    assert inv["status"] == "paid"
    assert inv["paid_date"] == "2026-02-05"
    assert inv["amount_paid"] == pytest.approx(1000.0)


def test_mark_invoice_paid_partial(db):
    """A part-payment (< invoice amount) sets status 'partial' and records it."""
    _open_invoice(db, "INV-2", amount=1000.0)
    db.mark_invoice_paid("INV-2", "2026-02-05", amount_paid=400.0)

    inv = db.get_all_invoices("C001")[0]
    assert inv["status"] == "partial"
    assert inv["paid_date"] == "2026-02-05"
    assert inv["amount_paid"] == pytest.approx(400.0)


def test_paid_invoice_not_downgraded_after_mark_paid(db):
    """Once mark_invoice_paid sets an invoice paid, a later 'open' re-import
    must not flip it back to open."""
    _open_invoice(db, "INV-3", amount=1000.0)
    db.mark_invoice_paid("INV-3", "2026-02-05")

    # Re-import from a CSV that still thinks the invoice is open.
    db.upsert_invoice({
        "invoice_id": "INV-3", "customer_id": "C001",
        "issue_date": "2026-01-01", "due_date": "2026-01-31",
        "amount": 1000.0, "status": "open",
    })

    inv = db.get_all_invoices("C001")[0]
    assert inv["status"] == "paid"
    assert inv["paid_date"] == "2026-02-05"


# ─────────────────────────────────────────────────────────────────────
# save_batch_result + get_latest_results
# ─────────────────────────────────────────────────────────────────────

def test_save_and_get_latest_results(db):
    """Two runs on different dates: get_latest_results returns only the most
    recent, and rebuilds the drafted_email dict from the stored columns."""
    db.upsert_customer({"customer_id": "C001", "company_name": "Acme Ltd"})

    # Older run, no email drafted.
    db.save_batch_result({
        "run_date": "2026-01-01", "run_time": "09:00", "customer_id": "C001",
        "classification": "green", "recommended_action": "no_action",
        "reasoning": "all clear",
    })
    # Newer run, with a drafted email.
    db.save_batch_result({
        "run_date": "2026-06-01", "run_time": "08:30", "customer_id": "C001",
        "classification": "red", "recommended_action": "escalate",
        "pattern_noticed": "60 days overdue",
        "reasoning": "no response to two reminders",
        "drafted_email": {"subject": "Overdue invoice", "body": "Please pay."},
    })

    results, run_time, run_date = db.get_latest_results()

    # Only the most recent run is returned.
    assert run_date == "2026-06-01"
    assert run_time == "08:30"
    assert len(results) == 1

    r = results[0]
    assert r["customer_id"] == "C001"
    assert r["classification"] == "red"
    assert r["recommended_action"] == "escalate"
    # drafted_email reconstructed as a dict from the subject/body columns.
    assert r["drafted_email"] == {"subject": "Overdue invoice", "body": "Please pay."}

    # The older run is not in the latest set, but is still queryable, and an
    # action with no drafted email rebuilds drafted_email as None.
    older = db.get_results_for_date("2026-01-01")
    assert len(older) == 1
    assert older[0]["classification"] == "green"
    assert older[0]["drafted_email"] is None


def test_save_batch_result_upserts_same_run_date(db):
    """Re-saving the same (run_date, customer) updates in place, not duplicates."""
    db.upsert_customer({"customer_id": "C001", "company_name": "Acme Ltd"})
    db.save_batch_result({
        "run_date": "2026-06-01", "customer_id": "C001",
        "classification": "amber", "recommended_action": "reminder_tier_1",
    })
    db.save_batch_result({
        "run_date": "2026-06-01", "customer_id": "C001",
        "classification": "red", "recommended_action": "escalate",
    })

    results = db.get_results_for_date("2026-06-01")
    assert len(results) == 1
    assert results[0]["classification"] == "red"


# ─────────────────────────────────────────────────────────────────────
# replace_dataset — atomic CSV import
# ─────────────────────────────────────────────────────────────────────

def test_replace_dataset_swaps_in_new_data(db):
    """A successful import wipes the old dataset and installs the new one."""
    db.upsert_customer({"customer_id": "OLD", "company_name": "Old Co"})
    db.upsert_invoice({
        "invoice_id": "OLD-1", "customer_id": "OLD",
        "issue_date": "2025-01-01", "due_date": "2025-01-31",
        "amount": 50.0, "status": "open",
    })

    db.replace_dataset(
        customers=[{"customer_id": "NEW", "company_name": "New Co",
                    "payment_terms_days": 14}],
        invoices=[{"invoice_id": "NEW-1", "customer_id": "NEW",
                   "issue_date": "2026-01-01", "due_date": "2026-01-31",
                   "amount": 100.0, "status": "open"}],
    )

    customers = db.get_all_customers()
    assert len(customers) == 1
    assert customers[0]["customer_id"] == "NEW"
    assert db.get_customer("OLD") is None        # old data gone
    assert len(db.get_all_invoices("NEW")) == 1


def test_replace_dataset_rolls_back_on_failure(db):
    """If the import fails part-way, the previous dataset is left intact rather
    than wiped-then-half-loaded."""
    db.upsert_customer({"customer_id": "OLD", "company_name": "Old Co"})

    # The second record is missing 'customer_id' -> KeyError mid-import, after
    # the wipe has already run. The single transaction must roll back.
    bad_customers = [
        {"customer_id": "NEW", "company_name": "New Co"},
        {"company_name": "Broken Co"},  # no customer_id -> raises
    ]
    with pytest.raises(KeyError):
        db.replace_dataset(customers=bad_customers, invoices=[])

    survivors = db.get_all_customers()
    assert len(survivors) == 1
    assert survivors[0]["customer_id"] == "OLD"   # previous data preserved


# ─────────────────────────────────────────────────────────────────────
# orchestrator._agent_error — per-customer failure fallback
# ─────────────────────────────────────────────────────────────────────

def test_agent_error_is_a_valid_escalation():
    """A failed customer becomes a red escalation that save_batch_result can
    persist (no missing keys), so failures surface instead of vanishing."""
    from src import orchestrator

    rec = orchestrator._agent_error("C001", RuntimeError("API down"))

    assert rec["customer_id"] == "C001"
    assert rec["classification"] == "red"
    assert rec["recommended_action"] == "escalate_to_human"
    assert rec["drafted_email"] is None
    assert "API down" in rec["reasoning"]
    assert {"customer_id", "classification", "recommended_action"} <= rec.keys()
