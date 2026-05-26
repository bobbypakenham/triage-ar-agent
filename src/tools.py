"""
tools.py — Functions the agent calls to gather information.

Each tool reads from the JSON data files and returns clean, structured data.
The agent calls these via Ollama's tool-calling interface.

Tools are deliberately simple and deterministic: they fetch, filter, or
compute — no judgment, no LLM. Judgment happens in the agent layer.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from src.stats import compute_payment_stats


DATA_DIR = Path("data")
def _today():
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------
# Internal helpers — load JSON files into memory once.
# In a real system these would be database queries.
# ---------------------------------------------------------------------

def _load_json(filename):
    with open(DATA_DIR / filename, "r") as f:
        return json.load(f)


def _load_all():
    return (
        _load_json("customers.json"),
        _load_json("invoices.json"),
        _load_json("communications.json") if (DATA_DIR / "communications.json").exists() else [],
    )

_CUSTOMERS, _INVOICES, _COMMUNICATIONS = _load_all()

def reload_data():
    """Reload all data files from disk. Call this after a CSV upload."""
    global _CUSTOMERS, _INVOICES, _COMMUNICATIONS
    _CUSTOMERS, _INVOICES, _COMMUNICATIONS = _load_all()


# In-memory store for the agent's recommendations during a batch run.
# Cleared at the start of each batch by the orchestrator.
_RECOMMENDATIONS = []


def _find_customer(customer_id):
    """Internal helper — find a customer record by ID."""
    for c in _CUSTOMERS:
        if c["customer_id"] == customer_id:
            return c
    return None


def _invoices_for_customer(customer_id):
    """Internal helper — all invoices for a customer."""
    return [inv for inv in _INVOICES if inv["customer_id"] == customer_id]


# ---------------------------------------------------------------------
# The tools the agent can call.
# Each one returns a Python dict/list that gets JSON-serialized for the model.
# ---------------------------------------------------------------------

def get_customer_profile(customer_id):
    """
    Return basic profile information for a customer:
    name, contact details, payment terms, credit limit, account age.
    """
    customer = _find_customer(customer_id)
    if not customer:
        return {"error": f"Customer {customer_id} not found"}
    
    account_opened_date = datetime.strptime(customer["account_opened"], "%Y-%m-%d")
    account_age_days = (_today() - account_opened_date).days
    account_age_months = round(account_age_days / 30, 1)
    
    return {
        "customer_id": customer["customer_id"],
        "customer_type": customer["customer_type"],
        "company_name": customer["company_name"],
        "contact_name": customer["contact_name"],
        "contact_email": customer["contact_email"],
        "payment_terms_days": customer["payment_terms_days"],
        "credit_limit": customer["credit_limit"],
        "account_age_months": account_age_months,
    }


def get_open_invoices(customer_id):
    """
    Return all currently open or overdue invoices for a customer,
    each with computed days_outstanding.
    """
    customer = _find_customer(customer_id)
    if not customer:
        return {"error": f"Customer {customer_id} not found"}
    
    open_invoices = []
    for inv in _invoices_for_customer(customer_id):
        if inv["status"] not in ("open", "overdue"):
            continue
        
        issue_date = datetime.strptime(inv["issue_date"], "%Y-%m-%d")
        due_date = datetime.strptime(inv["due_date"], "%Y-%m-%d")
        days_outstanding = (_today() - issue_date).days
        days_past_due = max(0, (_today() - due_date).days)
        
        open_invoices.append({
            "invoice_id": inv["invoice_id"],
            "issue_date": inv["issue_date"],
            "due_date": inv["due_date"],
            "amount": inv["amount"],
            "status": inv["status"],
            "days_outstanding": days_outstanding,
            "days_past_due": days_past_due,
        })
    
    return open_invoices


def get_payment_stats(customer_id):
    """
    Return pre-computed payment behavior statistics for a customer,
    based on their full paid invoice history.
    
    Includes deviation from normal pattern if the customer has open invoices.
    """
    customer = _find_customer(customer_id)
    if not customer:
        return {"error": f"Customer {customer_id} not found"}
    
    all_invoices = _invoices_for_customer(customer_id)
    paid_invoices = [inv for inv in all_invoices if inv["status"] == "paid"]
    open_invoices = [inv for inv in all_invoices if inv["status"] in ("open", "overdue")]
    
    # Use the oldest open invoice for deviation calculation (most concerning one)
    current_open = None
    if open_invoices:
        current_open = min(open_invoices, key=lambda i: i["issue_date"])
    
    return compute_payment_stats(paid_invoices, current_open)


def get_communications_log(customer_id):
    """
    Return prior reminder emails sent to this customer about their
    currently open invoices, plus comms about invoices paid in the
    last 60 days (so the agent can see whether the customer responds
    to reminders).
    """
    customer = _find_customer(customer_id)
    if not customer:
        return {"error": f"Customer {customer_id} not found"}
    
    sixty_days_ago = _today() - timedelta(days=60)
    
    # Identify which invoices to include comms for:
    # - All currently open/overdue invoices
    # - Invoices paid within the last 60 days
    relevant_invoice_ids = set()
    for inv in _invoices_for_customer(customer_id):
        if inv["status"] in ("open", "overdue"):
            relevant_invoice_ids.add(inv["invoice_id"])
        elif inv["status"] == "paid" and inv.get("paid_date"):
            paid_date = datetime.strptime(inv["paid_date"], "%Y-%m-%d")
            if paid_date >= sixty_days_ago:
                relevant_invoice_ids.add(inv["invoice_id"])
    
    relevant_comms = [
        {
            "log_id": c["log_id"],
            "invoice_id": c["invoice_id"],
            "date_sent": c["date_sent"],
            "tier": c["tier"],
            "customer_responded": c["customer_responded"],
            "response_summary": c["response_summary"],
        }
        for c in _COMMUNICATIONS
        if c["customer_id"] == customer_id and c["invoice_id"] in relevant_invoice_ids
    ]
    
    relevant_comms.sort(key=lambda c: c["date_sent"])
    
    return relevant_comms

def record_recommendation(customer_id, classification, pattern_noticed,
                          recommended_action, drafted_email, reasoning):
    """
    Save the agent's final recommendation for this customer.
    Called once per customer at the end of analysis.
    """
    recommendation = {
        "customer_id": customer_id,
        "classification": classification,
        "pattern_noticed": pattern_noticed,
        "recommended_action": recommended_action,
        "drafted_email": drafted_email,
        "reasoning": reasoning,
    }
    _RECOMMENDATIONS.append(recommendation)
    return {"status": "recorded", "customer_id": customer_id}


# ---------------------------------------------------------------------
# Helpers used by the orchestrator (not exposed to the agent).
# ---------------------------------------------------------------------

def reset_recommendations():
    """Clear the recommendations store at the start of a batch run."""
    _RECOMMENDATIONS.clear()


def get_all_recommendations():
    """Return everything the agent has recorded during the current batch."""
    return list(_RECOMMENDATIONS)


def get_all_customers():
    """Return all customer records (used by orchestrator for the batch loop)."""
    return list(_CUSTOMERS)


# ---------------------------------------------------------------------
# Quick self-test — run this file directly to verify the tools work.
# ---------------------------------------------------------------------

if __name__ == "__main__":
    # Pick the first customer with open invoices to test
    test_id = None
    for c in _CUSTOMERS:
        if get_open_invoices(c["customer_id"]):
            test_id = c["customer_id"]
            break
    
    if not test_id:
        print("No customers with open invoices found.")
    else:
        print(f"Testing tools on customer {test_id}\n")
        
        print("--- get_customer_profile ---")
        print(json.dumps(get_customer_profile(test_id), indent=2, default=str))
        
        print("\n--- get_open_invoices ---")
        print(json.dumps(get_open_invoices(test_id), indent=2, default=str))
        
        print("\n--- get_payment_stats ---")
        print(json.dumps(get_payment_stats(test_id), indent=2, default=str))
        
        print("\n--- get_communications_log ---")
        print(json.dumps(get_communications_log(test_id), indent=2, default=str))