"""
src/csv_handler.py

Parses an uploaded aged debtors CSV and normalises it into the same
format as data/invoices.json and data/customers.json so the existing
agent, stats, and tools work unchanged.
"""

import pandas as pd
import uuid
from datetime import datetime, date
from typing import Optional


FIELD_PATTERNS = {
    "customer_name": [
        "customer", "client", "contact", "name", "debtor", "company", "account"
    ],
    "invoice_id": [
        "invoice number", "invoice no", "invoice ref", "invoice id",
        "invoice", "inv", "reference", "ref", "number", "no"
    ],
    "amount": [
        "amount due", "amount outstanding", "balance due", "outstanding balance",
        "amount", "balance", "outstanding", "due", "total", "value", "owing"
    ],
    "issue_date": [
        "invoice date", "issue date", "raised date",
        "issue", "issued", "date", "created", "raised"
    ],
    "due_date": [
        "payment due date", "due date", "pay by date",
        "due", "payment due", "pay by", "payment date"
    ],
    "contact_email": ["contact email", "e-mail", "email"],
    "contact_name":  ["contact name", "contact person"],
    "payment_terms": ["payment terms", "terms", "net days", "net"],
    "status":        ["payment status", "invoice status", "status"],
    "paid_date":     [
        "date paid", "paid date", "settled date", "payment received",
        # Underscore variants common in accounting-software exports.
        "paid_date", "payment_date", "date_paid", "settled_date",
        "payment_received",
    ],
    "currency":      ["currency code", "currency", "ccy"],
}

REQUIRED_FIELDS = ["customer_name", "invoice_id", "amount", "issue_date", "due_date"]


def load_csv(filepath_or_buffer) -> pd.DataFrame:
    try:
        df = pd.read_csv(filepath_or_buffer, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(filepath_or_buffer, encoding="latin-1")
    df.columns = df.columns.str.strip()
    return df


def auto_detect_columns(df: pd.DataFrame) -> dict:
    csv_cols = list(df.columns)
    csv_cols_lower = [c.lower().strip() for c in csv_cols]
    mapping = {}
    for field, patterns in FIELD_PATTERNS.items():
        matched = None
        for pattern in sorted(patterns, key=len, reverse=True):
            for col_lower, col_original in zip(csv_cols_lower, csv_cols):
                if pattern in col_lower:
                    matched = col_original
                    break
            if matched:
                break
        mapping[field] = matched
    return mapping


def validate_mapping(mapping: dict) -> list:
    return [
        f"Required field '{field}' is not mapped to any column."
        for field in REQUIRED_FIELDS
        if not mapping.get(field)
    ]


def _parse_date(value) -> Optional[str]:
    if pd.isna(value):
        return None
    value = str(value).strip()
    formats = [
        "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
        "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%d/%m/%y", "%m/%d/%y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        return pd.to_datetime(value, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return None


def _parse_amount(value) -> Optional[float]:
    if pd.isna(value):
        return None
    value = str(value).strip()
    for char in ["£", "€", "$", ",", " "]:
        value = value.replace(char, "")
    try:
        return round(float(value), 2)
    except ValueError:
        return None


_CURRENCY_SYMBOLS = {"€": "EUR", "£": "GBP", "$": "USD"}


def _normalise_currency(value) -> Optional[str]:
    """Map a currency cell to a 3-letter ISO code. Accepts a symbol (€/£/$) or a
    code (EUR/GBP/USD). Returns None when blank/unrecognised so the database
    default ('EUR') applies."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in s:
            return code
    code = s.upper()
    if len(code) == 3 and code.isalpha():
        return code
    return None


def _normalise_status(value) -> str:
    """Map a free-text status cell to one of: paid / partial / overdue / open.
    Anything blank or unrecognised is treated as an outstanding 'open' invoice."""
    s = str(value).strip().lower()
    if s in ("", "nan", "none") or "unpaid" in s:
        return "open"
    if "partial" in s or "part" in s:
        return "partial"
    if "paid" in s or "settled" in s or "cleared" in s or "complete" in s:
        return "paid"
    if "overdue" in s or "past due" in s:
        return "overdue"
    return "open"


def normalise(df: pd.DataFrame, mapping: dict) -> tuple:
    today_str = date.today().isoformat()
    customers = {}
    invoices = []
    skipped = 0

    for _, row in df.iterrows():
        raw_name = str(row[mapping["customer_name"]]).strip() if mapping.get("customer_name") else None
        if not raw_name or raw_name.lower() in ("nan", "none", ""):
            skipped += 1
            continue

        issue_date = _parse_date(row[mapping["issue_date"]]) if mapping.get("issue_date") else None
        due_date   = _parse_date(row[mapping["due_date"]])   if mapping.get("due_date")   else None
        if not issue_date or not due_date:
            skipped += 1
            continue

        amount = _parse_amount(row[mapping["amount"]]) if mapping.get("amount") else None
        if amount is None or amount <= 0:
            skipped += 1
            continue

        raw_inv_id = str(row[mapping["invoice_id"]]).strip() if mapping.get("invoice_id") else None
        if not raw_inv_id or raw_inv_id.lower() in ("nan", "none", ""):
            raw_inv_id = f"INV-{str(uuid.uuid4())[:8].upper()}"

        try:
            terms_days = (
                datetime.strptime(due_date, "%Y-%m-%d").date()
                - datetime.strptime(issue_date, "%Y-%m-%d").date()
            ).days
            terms_days = max(0, terms_days)
        except Exception:
            terms_days = 30

        if raw_name not in customers:
            customer_id = f"C{str(len(customers) + 1).zfill(3)}"
            contact_name  = None
            contact_email = None
            if mapping.get("contact_name"):
                v = str(row[mapping["contact_name"]]).strip()
                contact_name = v if v.lower() not in ("nan", "none", "") else None
            if mapping.get("contact_email"):
                v = str(row[mapping["contact_email"]]).strip()
                contact_email = v if v.lower() not in ("nan", "none", "") else None

            customers[raw_name] = {
                "customer_id":        customer_id,
                "customer_type":      "business",
                "company_name":       raw_name,
                "contact_name":       contact_name,
                "contact_email":      contact_email,
                "account_opened":     today_str,
                "payment_terms_days": terms_days,
                "credit_limit":       None,
            }
        else:
            customers[raw_name]["payment_terms_days"] = terms_days

        customer_id = customers[raw_name]["customer_id"]

        # Status / paid-date columns are optional. Import them when present so a
        # ledger that already records paid invoices keeps that payment history —
        # the stats and agent layers need paid invoices to judge a customer.
        status = "open"
        if mapping.get("status"):
            status = _normalise_status(row[mapping["status"]])

        paid_date = None
        if mapping.get("paid_date"):
            paid_date = _parse_date(row[mapping["paid_date"]])
        # A paid/partial invoice must carry a payment date so days-to-pay can be
        # computed; fall back to the due date when the CSV did not supply one.
        if status in ("paid", "partial") and not paid_date:
            paid_date = due_date

        currency = None
        if mapping.get("currency"):
            currency = _normalise_currency(row[mapping["currency"]])

        invoices.append({
            "invoice_id":  raw_inv_id,
            "customer_id": customer_id,
            "issue_date":  issue_date,
            "due_date":    due_date,
            "amount":      amount,
            "currency":    currency,
            "status":      status,
            "paid_date":   paid_date,
        })

    return list(customers.values()), invoices, skipped


def summary_stats(customers: list, invoices: list) -> dict:
    today = date.today().isoformat()
    # Paid invoices are neither outstanding nor overdue — exclude them from the
    # money totals (they are still counted in total_invoices).
    outstanding = [inv for inv in invoices if inv.get("status") != "paid"]
    total_overdue = sum(
        inv["amount"] for inv in outstanding
        if inv.get("due_date") and inv["due_date"] < today
    )
    return {
        "total_customers":   len(customers),
        "total_invoices":    len(invoices),
        "total_outstanding": sum(inv["amount"] for inv in outstanding),
        "total_overdue":     total_overdue,
    }