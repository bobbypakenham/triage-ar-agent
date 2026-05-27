"""
orchestrator.py — Batch runner for the AR agent.

Loops over all customers, pre-filters out the ones that clearly need no
analysis (cheap Python triage), runs the agent on the rest, and collects
their recommendations.

The pre-filter matters: it's wasteful to spend an API call asking the agent
"does this customer with no open invoices need attention?" when we can answer
that in Python for free. The agent's job is judgment on the ambiguous cases,
not triage of the obvious ones.
"""

from datetime import datetime

from src import tools
from src.agent_claude import analyze_customer
from src.tools import _today


# ---------------------------------------------------------------------
# Pre-filter — decide which customers are worth sending to the agent
# ---------------------------------------------------------------------

def _worth_analyzing(customer_id):
    """
    Cheap triage: should this customer be sent to the agent?

    We analyze a customer if ANY of these is true:
      - They have an overdue invoice (past the due date)
      - They have an open invoice approaching its due date (within 7 days)
      - A prior reminder was sent that they haven't responded to

    Customers with no open invoices, or only fresh invoices far from due,
    are auto-classified green and skipped — no API call needed.
    """
    open_invoices = tools.get_open_invoices(customer_id)
    if not open_invoices:
        return False  # Nothing outstanding — clearly green

    for inv in open_invoices:
        # Overdue → analyze
        if inv["days_past_due"] > 0:
            return True
        # Approaching due date (within 7 days) → analyze
        due_date = datetime.strptime(inv["due_date"], "%Y-%m-%d")
        days_until_due = (due_date - _today()).days
        if 0 <= days_until_due <= 7:
            return True

    # Any unanswered prior reminders → analyze
    comms = tools.get_communications_log(customer_id)
    for c in comms:
        if not c["customer_responded"]:
            return True

    return False


def _auto_green(customer_id):
    """
    Build a 'green / no action' recommendation for a customer the
    pre-filter skipped. Keeps the briefing complete (every customer
    accounted for) without spending an API call.
    """
    open_invoices = tools.get_open_invoices(customer_id)
    if not open_invoices:
        pattern = "No outstanding invoices."
    else:
        pattern = "Open invoice(s) still well within terms; no action needed."

    return {
        "customer_id": customer_id,
        "classification": "green",
        "pattern_noticed": pattern,
        "recommended_action": "no_action",
        "drafted_email": None,
        "reasoning": "Auto-classified by pre-filter: no overdue invoices, nothing approaching due, no unanswered reminders.",
        "_source": "prefilter",  # Marks this as not-agent-generated, for transparency
    }

def _enforce_classification(recommendation):
    """
    Guarantee classification matches the action, regardless of what the
    model returned. The model makes the judgment (the action); code derives
    the classification deterministically. This removes any model
    inconsistency between the two fields.
    """
    action = recommendation["recommended_action"]
    if action in ("escalate_to_human", "send_tier_3"):
        recommendation["classification"] = "red"
    elif action in ("send_tier_1", "send_tier_2"):
        recommendation["classification"] = "amber"
    elif action == "no_action":
        recommendation["classification"] = "green"
    return recommendation


# ---------------------------------------------------------------------
# The batch runner
# ---------------------------------------------------------------------

def run_batch(verbose=True, limit=None):
    """
    Run the full batch.

    Args:
        verbose: print progress as it goes
        limit: if set, only process the first N customers (useful for testing
               without spending a full batch's worth of API calls)

    Returns:
        A list of recommendation dicts, one per customer.
    """
    tools.reset_recommendations()  # Clear any prior run's results

    customers = tools.get_all_customers()
    if limit:
        customers = customers[:limit]

    results = []
    analyzed_count = 0
    skipped_count = 0

    for i, customer in enumerate(customers, start=1):
        cid = customer["customer_id"]

        if _worth_analyzing(cid):
            if verbose:
                print(f"\n[{i}/{len(customers)}] Analyzing {cid}...")
            recommendation = analyze_customer(cid, verbose=verbose)
            recommendation = _enforce_classification(recommendation)
            results.append(recommendation)
            analyzed_count += 1
        else:
            if verbose:
                print(f"[{i}/{len(customers)}] Skipping {cid} (auto-green)")
            results.append(_auto_green(cid))
            skipped_count += 1

    if verbose:
        print(f"\n{'='*60}")
        print(f"Batch complete: {analyzed_count} analyzed by agent, {skipped_count} auto-green")
        print(f"{'='*60}")

    # Save results to disk so the briefing can be regenerated without
    # re-running the agent (which costs money and time).
    _save_results(results)

    return results


def _save_results(results):
    """Save batch results to SQLite and a JSON file in briefings/."""
    import json
    from pathlib import Path
    from src import database

    run_date = _today().strftime("%Y-%m-%d")
    run_time = datetime.now().strftime("%H:%M")

    for r in results:
        r_with_meta = {**r, "run_date": run_date, "run_time": run_time}
        database.save_batch_result(r_with_meta)

    briefings_dir = Path("briefings")
    briefings_dir.mkdir(exist_ok=True)
    output_path = briefings_dir / f"results_{run_date}.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"Results saved to {output_path} and SQLite")


# ---------------------------------------------------------------------
# Self-test — run a small batch to confirm it works
# ---------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    # Allow a limit from the command line, e.g. `python -m src.orchestrator 5`
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    print(f"Running batch on first {limit} customers...\n")
    results = run_batch(verbose=True, limit=limit)

    print("\nRESULTS SUMMARY:")
    for r in results:
        source = " (prefilter)" if r.get("_source") == "prefilter" else ""
        print(f"  {r['customer_id']}: {r['classification']:6s} — {r['recommended_action']}{source}")