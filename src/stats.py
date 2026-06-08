"""
stats.py — Pure functions for computing payment behavior statistics.

These functions take raw invoice data and return clean numerical summaries
that the agent reasons over. No LLM involved here — just math.
"""

from datetime import datetime, timedelta
from statistics import mean, median, stdev


def _today():
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_date(date_str):
    """Convert 'YYYY-MM-DD' string to a datetime object."""
    return datetime.strptime(date_str, "%Y-%m-%d")


def _days_between(start_date_str, end_date_str):
    """Number of days between two date strings."""
    start = _parse_date(start_date_str)
    end = _parse_date(end_date_str)
    return (end - start).days


def _payment_weight(inv):
    """How much of an invoice has actually been paid, as a fraction in [0, 1].

    A fully paid invoice counts as a whole payment (1.0). A partial payment
    counts proportionally — amount_paid / amount — so e.g. €600 paid against a
    €1,000 invoice contributes 0.6 of a payment to the behaviour stats rather
    than a full one. Anything missing the figures it needs degrades to 0.0.
    """
    if inv.get("status") == "partial":
        amount = inv.get("amount") or 0
        amount_paid = inv.get("amount_paid") or 0
        if amount > 0:
            return max(0.0, min(1.0, amount_paid / amount))
        return 0.0
    return 1.0


def compute_days_to_pay(paid_invoices):
    """
    For each paid invoice, compute how many days the customer took to pay
    (from issue_date to paid_date — for partials, the partial payment date).
    Returns a list of integers.
    """
    return [
        _days_between(inv["issue_date"], inv["paid_date"])
        for inv in paid_invoices
    ]


# A customer is treated as paying "consistently" when the robust spread of
# their days-late stays within this many days. ~a week of jitter is normal and
# harmless; beyond it the timing is genuinely unpredictable.
CONSISTENT_SPREAD_DAYS = 7.0


def _days_late_list(paid_invoices):
    """Days past the due date for each payment (negative = paid early)."""
    return [
        _days_between(inv["due_date"], inv["paid_date"])
        for inv in paid_invoices
    ]


def _mad(values):
    """Median absolute deviation — a spread measure that, unlike the standard
    deviation, is barely moved by a single outlier. A customer who pays 4, 2, 3
    then 35 days late has a tiny MAD (the 35 is ignored) but a large stdev."""
    if len(values) < 2:
        return 0.0
    m = median(values)
    return median([abs(v - m) for v in values])


def _completeness(paid_invoices):
    """Average proportion of each bill actually paid (1.0 = always paid in full,
    lower for habitual partial payers)."""
    if not paid_invoices:
        return 0.0
    return mean(_payment_weight(inv) for inv in paid_invoices)


def _has_recent_deviation(days_late):
    """True when an otherwise-tight, not-habitually-late history has a single
    most-recent payment that lands far outside the customer's normal range —
    i.e. a reliable customer with one recent deviation, NOT a genuinely erratic
    one. Distinguished from erratic by looking at the spread of the *earlier*
    payments: those must have been consistent and roughly on time."""
    if len(days_late) < 4:
        return False
    recent, earlier = days_late[-1], days_late[:-1]
    if _mad(earlier) > CONSISTENT_SPREAD_DAYS:   # bulk was never consistent
        return False
    if mean(earlier) > 15:                       # bulk was already habitually late
        return False
    return abs(recent - median(earlier)) > max(15, 5 * _mad(earlier))


def compute_reliability_score(paid_invoices):
    """
    Reliability = how *predictable* a customer's payment timing is, in [0, 1].

    The primary signal is the consistency of their days-late: a low robust
    spread (MAD) means they pay on a dependable rhythm — and that is what makes
    them reliable, whether they pay early, on time, or habitually a few days
    late. A customer who always pays 5 days late is MORE reliable than one who
    pays anywhere from on-time to two months late, and this score reflects that.
    A single one-off outlier barely moves it (MAD is outlier-resistant).

    The consistency is then damped by completeness: a customer who only ever
    pays part of each bill cannot be fully reliable however punctual the part
    payments are, so the score is scaled by the average proportion paid.
    """
    if not paid_invoices:
        return 0.0

    spread = _mad(_days_late_list(paid_invoices))
    consistency = 1.0 / (1.0 + spread / CONSISTENT_SPREAD_DAYS)
    return consistency * _completeness(paid_invoices)


def compute_avg_days_to_pay(paid_invoices):
    """
    Weighted average days-to-pay (issue_date → paid_date). Each invoice is
    weighted by the proportion paid, so partial payments pull the average less
    than full ones. Returns 0.0 when there is nothing paid to average.
    """
    total_weight = sum(_payment_weight(inv) for inv in paid_invoices)
    if total_weight == 0:
        return 0.0
    weighted_days = sum(
        _payment_weight(inv) * _days_between(inv["issue_date"], inv["paid_date"])
        for inv in paid_invoices
    )
    return weighted_days / total_weight


def compute_avg_days_late(paid_invoices):
    """
    Weighted average days late across paid invoices, measured from the due_date
    (so payment terms are already accounted for). Positive = paid late,
    negative = paid early. Partial payments contribute proportionally, matching
    avg_days_to_pay.
    """
    total_weight = sum(_payment_weight(inv) for inv in paid_invoices)
    if total_weight == 0:
        return 0.0
    weighted_late = sum(
        _payment_weight(inv) * _days_between(inv["due_date"], inv["paid_date"])
        for inv in paid_invoices
    )
    return weighted_late / total_weight


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


def classify_behavior(paid_invoices_count, reliability, spread, trend, avg_days_late, recent_deviation):
    """
    Produce a single human-readable label for the customer's payment behavior.
    The agent uses this as a shortcut but can still see the raw stats.

    Consistency (the robust spread of days-late) is the primary signal. A
    customer who pays predictably — even predictably late — is reliable or
    slow-but-consistent, NOT erratic. "Erratic" is reserved for genuinely high
    variance (sometimes on time, sometimes very overdue, unpredictable), where
    the deviation signal can't be trusted. Lateness *severity* (avg_days_late)
    then sets the label for the predictable payers.
    """
    if paid_invoices_count < 3:
        return "insufficient_data"

    consistent = spread <= CONSISTENT_SPREAD_DAYS

    # Genuinely unpredictable timing. This is the ONLY thing that earns
    # "erratic" — not a single one-off outlier, and not simply paying late on a
    # dependable rhythm. (A robust spread is used, so one stray invoice does not
    # tip a consistent payer over into erratic.)
    if not consistent:
        return "erratic"

    # --- consistent / predictable payers below ---

    # An otherwise-tight, on-time-ish history with one recent outlier: reliable
    # with a single recent deviation, the early-warning case worth catching.
    if recent_deviation:
        return "deteriorating_reliable"

    # A highly consistent customer that is nonetheless trending slower.
    if reliability >= 0.9 and trend == "deteriorating":
        return "deteriorating_reliable"

    # Severity ladder for predictable payers. Because the timing is consistent,
    # these labels describe a dependable-but-late rhythm, not volatility.
    if avg_days_late > 30:
        return "high_risk"          # predictably very overdue — a real concern
    if avg_days_late > 15:
        return "moderately_late"    # predictably 15-30 days late — worth chasing
    if avg_days_late > 1:
        return "slightly_late"      # predictably a few days late — low concern
    return "reliable"               # consistent and on time or early

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

    # avg_dtp is weighted by the proportion paid (partials count for less);
    # median/std/trend use the raw timing of every payment, partial or not.
    avg_dtp = compute_avg_days_to_pay(paid_invoices)
    median_dtp = median(days_to_pay)
    std_dtp = stdev(days_to_pay) if len(days_to_pay) > 1 else 0.0

    recent_window = min(3, len(days_to_pay))
    recent_avg_dtp = mean(days_to_pay[-recent_window:])

    reliability = compute_reliability_score(paid_invoices)
    trend = compute_trend(days_to_pay)
    avg_days_late = compute_avg_days_late(paid_invoices)

    # Classification keys off the *robust* spread of days-late (MAD), so a single
    # outlier does not read as volatility, plus a flag for an otherwise-tight
    # history with one recent deviation.
    days_late = _days_late_list(paid_invoices)
    spread_days_late = _mad(days_late)
    recent_deviation = _has_recent_deviation(days_late)
    classification = classify_behavior(
        len(paid_invoices), reliability, spread_days_late, trend,
        avg_days_late, recent_deviation,
    )

    # A customer who has only ever made partial payments — never clearing a
    # single invoice in full — is not "reliable", however on-time those part
    # payments are. Demote so the agent treats the outstanding balances as the
    # live concern they are.
    only_partial = all(inv.get("status") == "partial" for inv in paid_invoices)
    if only_partial and classification == "reliable":
        classification = "slow_but_consistent"
    
    # Summary of recently paid invoices (last 60 days) — gives the agent context
    # about whether the customer has been actively paying or gone silent
    recent_paid_summary = []
    today = _today()
    sixty_days_ago = today - timedelta(days=60)
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
        days_outstanding = (_today() - issue_date).days
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