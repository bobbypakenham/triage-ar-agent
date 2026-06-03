"""
tests/test_stress.py — Robustness / edge-case stress tests for Triage.

These exercise the agent pipeline and the stats/CSV/DB layers against awkward
inputs. The real Anthropic API is NEVER called — agent behaviour is mocked at
two boundaries:

  * agent_claude._call_with_retry — to simulate raw model responses (e.g. text
    with no tool call), so analyze_customer's own fallback is exercised.
  * orchestrator.analyze_customer — to simulate per-customer outcomes (a normal
    recommendation, an escalation, or a raised API error) within a batch.

Every test asserts either a valid result or a graceful failure — never a raw
unhandled exception. DB tests use the shared in-memory `db` fixture from
conftest.py.
"""

import sys
import time
import types
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402
from src import tools, csv_handler, stats, orchestrator, agent_claude  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _paid(issue, due, paid, *, inv_id="INV", amount=1000.0,
          status="paid", amount_paid=None):
    inv = {
        "invoice_id": inv_id, "issue_date": issue, "due_date": due,
        "paid_date": paid, "amount": amount, "status": status,
    }
    if amount_paid is not None:
        inv["amount_paid"] = amount_paid
    return inv


CSV_MAPPING = {
    "customer_name": "Customer Name",
    "invoice_id":    "Invoice Number",
    "amount":        "Amount Due",
    "issue_date":    "Invoice Date",
    "due_date":      "Due Date",
}


def _rate_limit_error():
    """Build a real anthropic.RateLimitError where possible; fall back to a
    plain exception if the SDK's constructor shape differs. run_batch catches
    broad Exception either way, so the behaviour under test is unchanged."""
    try:
        import httpx
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(429, request=request)
        return anthropic.RateLimitError("rate limit exceeded", response=response, body=None)
    except Exception:
        return RuntimeError("rate limit exceeded (429)")


# ─────────────────────────────────────────────────────────────────────
# 1. Zero invoices
# ─────────────────────────────────────────────────────────────────────

def test_zero_invoices_handled_gracefully(db):
    """A customer with no invoices must not crash tools/stats, and the batch
    should auto-classify them green without ever calling the agent."""
    db.upsert_customer({"customer_id": "C001", "company_name": "Acme Ltd"})

    assert tools.get_open_invoices("C001") == []          # empty list, not an error
    pstats = tools.get_payment_stats("C001")
    assert isinstance(pstats, dict)
    assert pstats["behavior_classification"] == "insufficient_data"

    with mock.patch.object(orchestrator, "analyze_customer") as agent:
        results = orchestrator.run_batch(verbose=False)

    agent.assert_not_called()                              # no API call for empty customer
    assert len(results) == 1
    assert results[0]["classification"] == "green"
    assert results[0]["recommended_action"] == "no_action"


# ─────────────────────────────────────────────────────────────────────
# 2. Zero-amount invoice — no division by zero
# ─────────────────────────────────────────────────────────────────────

def test_zero_amount_partials_do_not_divide_by_zero():
    base = date(2025, 1, 1)
    invs = []
    for i in range(3):
        issue = base + timedelta(days=i * 30)
        due = issue + timedelta(days=30)
        invs.append(_paid(
            issue.isoformat(), due.isoformat(), due.isoformat(),
            inv_id=f"P{i}", amount=0.0, status="partial", amount_paid=0.0,
        ))

    # Total payment weight is 0 here — the weighted averages must guard against
    # dividing by it rather than raising ZeroDivisionError.
    result = stats.compute_payment_stats(invs)
    assert isinstance(result, dict)
    assert result["avg_days_to_pay"] == 0.0
    assert result["avg_days_late"] == 0.0


def test_zero_amount_open_invoice_in_db(db):
    db.upsert_customer({"customer_id": "C001", "company_name": "Acme Ltd"})
    db.upsert_invoice({
        "invoice_id": "Z1", "customer_id": "C001",
        "issue_date": "2026-01-01", "due_date": "2026-01-31",
        "amount": 0.0, "status": "open",
    })

    invs = tools.get_open_invoices("C001")
    assert isinstance(invs, list) and invs[0]["amount"] == 0
    assert isinstance(tools.get_payment_stats("C001"), dict)  # no crash


# ─────────────────────────────────────────────────────────────────────
# 3. Huge invoice amount
# ─────────────────────────────────────────────────────────────────────

def test_huge_invoice_amount_flows_to_escalation(db):
    """A €999,999,999 overdue invoice must be handled numerically and routed to
    the agent. The escalation decision itself is the model's (prompt rule:
    invoices over €25k escalate), so we mock that decision and assert the
    pipeline persists it as a red escalation."""
    huge = 999_999_999.0
    today = date.today()
    db.upsert_customer({"customer_id": "C001", "company_name": "Mega Corp"})
    db.upsert_invoice({
        "invoice_id": "BIG", "customer_id": "C001",
        "issue_date": (today - timedelta(days=60)).isoformat(),
        "due_date":   (today - timedelta(days=30)).isoformat(),
        "amount": huge, "status": "open",
    })

    invs = tools.get_open_invoices("C001")
    assert invs[0]["amount"] == huge              # no overflow / precision crash
    assert invs[0]["days_past_due"] == 30
    assert orchestrator._worth_analyzing("C001") is True  # would be sent to the agent

    def _escalate(cid, verbose=False):
        return {
            "customer_id": cid, "classification": "red",
            "pattern_noticed": "Balance far exceeds the escalation threshold",
            "recommended_action": "escalate_to_human",
            "drafted_email": None, "reasoning": "Enormous outstanding balance.",
        }

    with mock.patch.object(orchestrator, "analyze_customer", side_effect=_escalate):
        results = orchestrator.run_batch(verbose=False)

    assert len(results) == 1
    assert results[0]["classification"] == "red"
    assert results[0]["recommended_action"] == "escalate_to_human"


# ─────────────────────────────────────────────────────────────────────
# 4. All invoices paid exactly on the due date
# ─────────────────────────────────────────────────────────────────────

def test_all_paid_exactly_on_due_date():
    base = date(2024, 1, 1)
    invs = []
    for i in range(6):
        issue = base + timedelta(days=i * 35)
        due = issue + timedelta(days=30)
        invs.append(_paid(issue.isoformat(), due.isoformat(), due.isoformat(),
                          inv_id=f"INV{i}"))  # paid_date == due_date

    result = stats.compute_payment_stats(invs)
    assert result["reliability_score"] == 1.0    # on-or-before due counts as on time
    assert result["avg_days_late"] == 0.0


# ─────────────────────────────────────────────────────────────────────
# 5. 100+ invoices — completes quickly
# ─────────────────────────────────────────────────────────────────────

def test_120_invoices_complete_quickly():
    base = date(2022, 1, 1)
    invs = []
    for i in range(120):
        issue = base + timedelta(days=i * 7)
        due = issue + timedelta(days=30)
        paid = due - timedelta(days=2)
        invs.append(_paid(issue.isoformat(), due.isoformat(), paid.isoformat(),
                          inv_id=f"INV{i:04d}"))

    start = time.perf_counter()
    result = stats.compute_payment_stats(invs)
    elapsed = time.perf_counter() - start

    assert result["total_invoices_paid"] == 120
    assert elapsed < 2.0          # generous bound — should be milliseconds


# ─────────────────────────────────────────────────────────────────────
# 6. Malformed dates
# ─────────────────────────────────────────────────────────────────────

def test_malformed_date_in_csv_is_skipped():
    df = pd.DataFrame([
        {"Customer Name": "Acme", "Invoice Number": "INV-1", "Amount Due": "100",
         "Invoice Date": "2026-01-01", "Due Date": "2026-01-31"},
        {"Customer Name": "Beta", "Invoice Number": "INV-2", "Amount Due": "200",
         "Invoice Date": "2026-02-01", "Due Date": "not-a-date"},
    ])
    customers, invoices, skipped = csv_handler.normalise(df, CSV_MAPPING)

    assert skipped == 1                       # the bad-date row is dropped, not fatal
    assert len(invoices) == 1
    assert invoices[0]["invoice_id"] == "INV-1"


def test_malformed_date_in_db_does_not_crash(db):
    db.upsert_customer({"customer_id": "C001", "company_name": "Acme Ltd"})
    db.upsert_invoice({
        "invoice_id": "BAD", "customer_id": "C001",
        "issue_date": "not-a-date", "due_date": "not-a-date",
        "amount": 100.0, "status": "open",
    })

    # julianday() over a junk date returns NULL; the code coalesces to 0 rather
    # than raising.
    invs = tools.get_open_invoices("C001")
    assert isinstance(invs, list)
    assert invs[0]["days_past_due"] == 0
    assert invs[0]["days_outstanding"] == 0


# ─────────────────────────────────────────────────────────────────────
# 7. Missing contact email
# ─────────────────────────────────────────────────────────────────────

def test_missing_contact_email_is_graceful(db):
    db.upsert_customer({
        "customer_id": "C001", "company_name": "Acme Ltd", "contact_email": None,
    })

    profile = tools.get_customer_profile("C001")
    assert profile["contact_email"] is None        # falls back to None, no crash
    assert profile["company_name"] == "Acme Ltd"    # still usable for a 'Hello,' greeting


# ─────────────────────────────────────────────────────────────────────
# 8. Agent returns text but never calls record_recommendation
# ─────────────────────────────────────────────────────────────────────

def test_agent_text_without_recommendation_escalates(monkeypatch):
    """If the model produces only text and never records a recommendation,
    analyze_customer must fall back to a red escalation, not return nothing."""
    tools.reset_recommendations()
    monkeypatch.setattr(agent_claude, "api_key", "test-key")  # pass the not-set guard

    text_only = types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text="Some musings, no tool call.")],
        stop_reason="end_turn",
    )
    monkeypatch.setattr(agent_claude, "_call_with_retry",
                        lambda client, messages: text_only)

    rec = agent_claude.analyze_customer("C001", verbose=False)

    assert rec["customer_id"] == "C001"
    assert rec["classification"] == "red"
    assert rec["recommended_action"] == "escalate_to_human"
    tools.reset_recommendations()


# ─────────────────────────────────────────────────────────────────────
# 9. Duplicate invoice IDs — upsert, no duplicate rows
# ─────────────────────────────────────────────────────────────────────

def test_duplicate_invoice_id_upserts(db):
    db.upsert_customer({"customer_id": "C001", "company_name": "Acme Ltd"})
    inv = {
        "invoice_id": "DUP", "customer_id": "C001",
        "issue_date": "2026-01-01", "due_date": "2026-01-31",
        "amount": 100.0, "status": "open",
    }
    db.upsert_invoice(inv)
    db.upsert_invoice({**inv, "amount": 150.0})   # same id again (e.g. re-upload)

    rows = db.get_all_invoices("C001")
    assert len(rows) == 1                          # one row, not two
    assert rows[0]["amount"] == 150.0              # updated in place


def test_duplicate_invoice_id_from_csv_upserts(db):
    df = pd.DataFrame([
        {"Customer Name": "Beta", "Invoice Number": "INV-9", "Amount Due": "500",
         "Invoice Date": "2026-01-01", "Due Date": "2026-01-31"},
        {"Customer Name": "Beta", "Invoice Number": "INV-9", "Amount Due": "500",
         "Invoice Date": "2026-01-01", "Due Date": "2026-01-31"},
    ])
    customers, invoices, _ = csv_handler.normalise(df, CSV_MAPPING)
    for c in customers:
        db.upsert_customer(c)
    for i in invoices:
        db.upsert_invoice(i)

    cid = customers[0]["customer_id"]
    assert len(db.get_all_invoices(cid)) == 1      # deduped by primary key


# ─────────────────────────────────────────────────────────────────────
# 10. Mid-batch rate limit — other customers keep processing
# ─────────────────────────────────────────────────────────────────────

def test_mid_batch_rate_limit_does_not_abort_run(db, monkeypatch):
    today = date.today()
    for cid, name in [("C001", "Alpha"), ("C002", "Bravo"), ("C003", "Charlie")]:
        db.upsert_customer({"customer_id": cid, "company_name": name})
        db.upsert_invoice({
            "invoice_id": f"INV-{cid}", "customer_id": cid,
            "issue_date": (today - timedelta(days=60)).isoformat(),
            "due_date":   (today - timedelta(days=20)).isoformat(),  # overdue -> analysed
            "amount": 1000.0, "status": "open",
        })

    def _side_effect(cid, verbose=False):
        if cid == "C002":
            raise _rate_limit_error()
        return {
            "customer_id": cid, "classification": "amber",
            "pattern_noticed": "overdue", "recommended_action": "send_tier_1",
            "drafted_email": None, "reasoning": "chase",
        }

    monkeypatch.setattr(orchestrator, "analyze_customer", _side_effect)

    results = orchestrator.run_batch(verbose=False)   # must NOT raise
    by_id = {r["customer_id"]: r for r in results}

    assert len(results) == 3
    # The rate-limited customer becomes an error escalation...
    assert by_id["C002"]["classification"] == "red"
    assert by_id["C002"]["recommended_action"] == "escalate_to_human"
    assert by_id["C002"].get("_source") == "error"
    # ...and the others are processed normally.
    assert by_id["C001"]["recommended_action"] == "send_tier_1"
    assert by_id["C003"]["recommended_action"] == "send_tier_1"
