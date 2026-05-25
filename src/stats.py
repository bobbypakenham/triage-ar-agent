"""
stats.py — Pure functions for computing payment behavior statistics.

These functions take raw invoice data and return clean numerical summaries
that the agent reasons over. No LLM involved here — just math.
"""

from datetime import datetime, timedelta
from statistics import mean, median, stdev


# Pretend "today" for consistent demos — must match generate_data.py and tools.py
TODAY = datetime(2026, 5, 17)


def _parse_date(date_str):
    """Convert 'YYYY-MM-DD' string to a datetime object."""
    return datetime.strptime(date_str, "%Y-%m-%d")


def _days_between(start_date_str, end_date_str):
    """Number of days between two date strings."""
    start = _parse_date(start_date_str)
    end = _parse_date(end_date_str)
    return (end - start).days


def compute_days_to_pay(paid_invoices):
    """
    For each paid invoice, compute how many days the customer took to pay
    (from issue_date to paid_date).
    Returns a list of integers.
    """
    return [
        _days_between(inv["issue_date"], inv["paid_date"])
        for inv in paid_invoices
    ]


def compute_reliability_score(paid_invoices):
    """
    Fraction of invoices paid on or before the due date.
    Returns a float between 0.0 and 1.0.
    """
    if not paid_invoices:
        return 0.0
    
    on_time_count = sum(
        1 for inv in paid_invoices
        if _days_between(inv["due_date"], inv["paid_date"]) <= 0
    )
    return on_time_count / len(paid_invoices)

def compute_avg_days_late(paid_invoices):
    """
    Average number of days late across all paid invoices.
    Positive = paid after due date (late), negative = paid early.
    Measured from the due_date, so payment terms are already accounted for.
    """
    if not paid_invoices:
        return 0.0
    days_late_list = [
        _days_between(inv["due_date"], inv["paid_date"])
        for inv in paid_invoices
    ]
    return mean(days_late_list)


def compute_trend(days_to_pay_list, recent_window=3):
    """
    Compare recent payment behavior to historical behavior.
    Returns "improving", "stable", or "deteriorating".
    
    - improving: recent average is meaningfully lower than historical
    - deteriorating: recent average is meaningfully higher than historical
    - stable: recent average is within 1 standard deviation of historical
    """
    if len(days_to_pay_list) < recent_window + 2:
        return "insufficient_data"
    
    recent = days_to_pay_list[-recent_window:]
    historical = days_to_pay_list[:-recent_window]
    
    recent_avg = mean(recent)
    historical_avg = mean(historical)
    historical_std = stdev(historical) if len(historical) > 1 else 0
    
    diff = recent_avg - historical_avg
    
    if abs(diff) <= historical_std:
        return "stable"
    elif diff > 0:
        return "deteriorating"
    else:
        return "improving"


def classify_behavior(paid_invoices_count, reliability, std_dev_days, trend, avg_days_late):
    """
    Produce a single human-readable label for the customer's payment behavior.
    The agent uses this as a shortcut but can still see the raw stats.

    Lateness severity is judged by avg_days_late (how many days past the due
    date they typically pay), NOT by reliability alone. This distinguishes a
    customer who is reliably a few days late (fine) from one who is chronically
    very overdue (a real concern).
    """
    if paid_invoices_count < 3:
        return "insufficient_data"

    # Severe lateness is the strongest concern signal — gate on how late, not
    # merely whether they were ever late.
    if avg_days_late > 30:
        return "high_risk"

    # High variance is only a concern if it actually causes missed due dates.
    # A customer who pays anywhere from day 25-50 on Net 60 has high variance
    # but is never late — that's harmless, not erratic.
    if std_dev_days > 15 and reliability < 0.8:
        return "erratic"

    # Was reliable, now slipping — the early-warning case worth catching.
    if reliability >= 0.9 and trend == "deteriorating":
        return "deteriorating_reliable"

    # Pays on time. High reliability proves any timing variance is harmless,
    # so we don't require low variance here.
    if reliability >= 0.9:
        return "reliable"

    # Moderately late: typically 15-30 days past due. Worth watching/chasing.
    if avg_days_late > 15:
        return "moderately_late"

    # Slightly late: typically 1-15 days past due. Slow but not a worry.
    if avg_days_late > 1:
        return "slightly_late"

    # On time or early, but with some inconsistency.
    if 0.6 <= reliability < 0.9:
        return "slow_but_consistent"

    return "mixed"

def compute_payment_stats(paid_invoices, current_open_invoice=None):
    """
    Main function: takes a list of paid invoices (and optionally the
    current open invoice) and returns the full stats dictionary.
    """
    if not paid_invoices:
        return {
            "total_invoices_paid": 0,
            "behavior_classification": "insufficient_data",
            "note": "No payment history available"
        }
    
    days_to_pay = compute_days_to_pay(paid_invoices)
    
    avg_dtp = mean(days_to_pay)
    median_dtp = median(days_to_pay)
    std_dtp = stdev(days_to_pay) if len(days_to_pay) > 1 else 0.0
    
    recent_window = min(3, len(days_to_pay))
    recent_avg_dtp = mean(days_to_pay[-recent_window:])
    
    reliability = compute_reliability_score(paid_invoices)
    trend = compute_trend(days_to_pay)
    avg_days_late = compute_avg_days_late(paid_invoices)
    classification = classify_behavior(
        len(paid_invoices), reliability, std_dtp, trend, avg_days_late
    )
    
    # Summary of recently paid invoices (last 60 days) — gives the agent context
    # about whether the customer has been actively paying or gone silent
    recent_paid_summary = []
    sixty_days_ago = TODAY - timedelta(days=60)
    for inv in paid_invoices:
        paid_date = _parse_date(inv["paid_date"])
        if paid_date >= sixty_days_ago:
            days_to_pay = _days_between(inv["issue_date"], inv["paid_date"])
            recent_paid_summary.append({
                "invoice_id": inv.get("invoice_id", "unknown"),
                "amount": inv.get("amount", 0),
                "paid_date": inv["paid_date"],
                "days_to_pay": days_to_pay,
            })
    # Sort by paid_date, most recent first
    recent_paid_summary.sort(key=lambda x: x["paid_date"], reverse=True)
    
    stats = {
        "total_invoices_paid": len(paid_invoices),
        "avg_days_to_pay": round(avg_dtp, 1),
        "median_days_to_pay": median_dtp,
        "std_dev_days_to_pay": round(std_dtp, 1),
        "recent_avg_days_to_pay": round(recent_avg_dtp, 1),
        "drift_days": round(recent_avg_dtp - avg_dtp, 1),
        "trend": trend,
        "reliability_score": round(reliability, 2),
        "avg_days_late": round(avg_days_late, 1),
        "behavior_classification": classification,
        "recent_paid_invoices": recent_paid_summary,
    }
    
    # If a current open invoice is provided, compute how far it deviates
    # from the customer's normal pattern — but only when the metric is
    # actually meaningful:
    #   1. Enough payment history (>= 5 invoices) for std dev to mean something
    #   2. The invoice has passed the customer's normal payment window
    #      (days_outstanding >= avg_days_to_pay) — a fresh invoice isn't
    #      "deviating", they just haven't had their normal window to pay yet
    # We always report days_outstanding; we conditionally report the sigma.
    if current_open_invoice:
        issue_date = _parse_date(current_open_invoice["issue_date"])
        days_outstanding = (TODAY - issue_date).days
        stats["current_days_outstanding"] = days_outstanding

        enough_history = len(paid_invoices) >= 5
        past_normal_window = days_outstanding >= avg_dtp

        if enough_history and past_normal_window and std_dtp > 0:
            deviation_sigmas = (days_outstanding - avg_dtp) / std_dtp
            # Clamp to ±5 — beyond that the exact value is meaningless,
            # "very unusual" is all the agent needs to know.
            deviation_sigmas = max(-5, min(5, deviation_sigmas))
            stats["current_deviation_sigmas"] = round(deviation_sigmas, 1)
    
    return stats


# ---------------------------------------------------------------------
# Quick self-test — runs if you execute this file directly.
# Lets us verify the math works before plugging it into the agent.
# ---------------------------------------------------------------------
if __name__ == "__main__":
    # Fake paid invoices for a reliable customer who's starting to drift
    fake_paid_invoices = [
        {"issue_date": "2024-01-01", "due_date": "2024-01-31", "paid_date": "2024-01-28"},
        {"issue_date": "2024-02-01", "due_date": "2024-03-02", "paid_date": "2024-02-29"},
        {"issue_date": "2024-03-01", "due_date": "2024-03-31", "paid_date": "2024-03-30"},
        {"issue_date": "2024-04-01", "due_date": "2024-05-01", "paid_date": "2024-04-29"},
        {"issue_date": "2024-05-01", "due_date": "2024-05-31", "paid_date": "2024-05-30"},
        {"issue_date": "2024-06-01", "due_date": "2024-07-01", "paid_date": "2024-06-30"},
        {"issue_date": "2024-07-01", "due_date": "2024-07-31", "paid_date": "2024-08-08"},  # late
        {"issue_date": "2024-08-01", "due_date": "2024-08-31", "paid_date": "2024-09-09"},  # later
        {"issue_date": "2024-09-01", "due_date": "2024-10-01", "paid_date": "2024-10-12"},  # later still
    ]
    
    fake_open_invoice = {
        "issue_date": "2026-04-01",
        "due_date": "2026-05-01",
        "amount": 4500
    }
    
    stats = compute_payment_stats(fake_paid_invoices, fake_open_invoice)
    
    print("Payment stats for test customer:")
    for key, value in stats.items():
        print(f"  {key}: {value}")