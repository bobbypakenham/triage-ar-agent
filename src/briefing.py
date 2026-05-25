"""
briefing.py — Turns batch results into a dated morning briefing.

Reads the saved results JSON (produced by the orchestrator) and formats
it into a markdown report a credit controller would read each morning.

Structure:
  - Header (generation date, data-as-of date, one-line summary)
  - Summary stats (green/amber/red counts, total value at risk)
  - RED   — needs your attention (escalations, Tier 3)
  - AMBER  — reminders ready to send (with drafted emails)
  - GREEN  — no action needed (compact list)

Sections are ordered by urgency: act on red first, approve amber emails
next, green is just an all-clear list at the bottom.
"""

import json
from datetime import datetime
from pathlib import Path

from src import tools


TODAY = datetime(2026, 5, 17)
BRIEFINGS_DIR = Path("briefings")


# ---------------------------------------------------------------------
# Helpers for enriching recommendations with customer/invoice context
# ---------------------------------------------------------------------

def _customer_display_name(customer_id):
    """Return a human-readable name: company name, or contact name for individuals."""
    profile = tools.get_customer_profile(customer_id)
    if profile.get("company_name"):
        return profile["company_name"]
    if profile.get("contact_name"):
        return profile["contact_name"]
    return customer_id


def _total_overdue_value(customer_id):
    """Sum of all overdue invoice amounts for a customer."""
    open_invoices = tools.get_open_invoices(customer_id)
    return sum(inv["amount"] for inv in open_invoices if inv["days_past_due"] > 0)


# ---------------------------------------------------------------------
# Section formatters
# ---------------------------------------------------------------------

def _format_red_section(red_recs):
    """Escalations and Tier 3 — the act-first cases."""
    if not red_recs:
        return "_No customers require urgent attention._\n"

    lines = []
    for r in red_recs:
        cid = r["customer_id"]
        name = _customer_display_name(cid)
        action = r["recommended_action"].replace("_", " ").title()
        overdue = _total_overdue_value(cid)

        lines.append(f"### {name} ({cid}) — {action}")
        lines.append(f"**Overdue value:** £{overdue:,.2f}")
        lines.append(f"**What we noticed:** {r['pattern_noticed']}")
        lines.append(f"**Reasoning:** {r['reasoning']}")

        # Tier 3 cases have a drafted email; escalations don't
        if r.get("drafted_email"):
            email = r["drafted_email"]
            lines.append(f"\n**Drafted email — _{email['subject']}_**")
            lines.append("```")
            lines.append(email["body"])
            lines.append("```")

        lines.append("")  # blank line between customers

    return "\n".join(lines)


def _format_amber_section(amber_recs):
    """Reminders ready to send, each with its drafted email."""
    if not amber_recs:
        return "_No reminders to send today._\n"

    lines = []
    for r in amber_recs:
        cid = r["customer_id"]
        name = _customer_display_name(cid)
        action = r["recommended_action"].replace("_", " ").title()
        overdue = _total_overdue_value(cid)

        lines.append(f"### {name} ({cid}) — {action}")
        if overdue > 0:
            lines.append(f"**Overdue value:** £{overdue:,.2f}")
        lines.append(f"**What we noticed:** {r['pattern_noticed']}")

        if r.get("drafted_email"):
            email = r["drafted_email"]
            lines.append(f"\n**Drafted email — _{email['subject']}_**")
            lines.append("```")
            lines.append(email["body"])
            lines.append("```")
        else:
            # An amber with no email (e.g. no_action that landed amber) — show reasoning
            lines.append(f"**Reasoning:** {r['reasoning']}")

        lines.append("")

    return "\n".join(lines)


def _format_green_section(green_recs):
    """Compact list — these need nothing."""
    if not green_recs:
        return "_None._\n"

    lines = ["| Customer | ID | Note |", "|---|---|---|"]
    for r in green_recs:
        cid = r["customer_id"]
        name = _customer_display_name(cid)
        note = r["pattern_noticed"]
        lines.append(f"| {name} | {cid} | {note} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------
# Main briefing generator
# ---------------------------------------------------------------------

def generate_briefing(results, save=True):
    """
    Build the morning briefing from a list of recommendation dicts.
    Returns the markdown string, and optionally saves it to a dated file.
    """
    # Bucket by classification
    red = [r for r in results if r["classification"] == "red"]
    amber = [r for r in results if r["classification"] == "amber"]
    green = [r for r in results if r["classification"] == "green"]

    # Total value at risk = sum of all overdue across non-green customers
    total_at_risk = sum(_total_overdue_value(r["customer_id"]) for r in (red + amber))

    date_str = TODAY.strftime("%d %B %Y")
    needs_attention = len(red) + len(amber)

    # Build the markdown
    md = []
    md.append(f"# AR Morning Briefing — {date_str}")
    md.append(f"_Generated {date_str}, reflecting ledger state as of {date_str}._\n")
    md.append(f"**{len(results)} customers reviewed. {needs_attention} need attention "
              f"({len(red)} urgent, {len(amber)} reminders). £{total_at_risk:,.2f} total overdue.**\n")

    md.append("## Summary")
    md.append(f"- 🔴 **{len(red)}** urgent (escalations / final notices)")
    md.append(f"- 🟡 **{len(amber)}** reminders ready to review and send")
    md.append(f"- 🟢 **{len(green)}** no action needed")
    md.append(f"- **£{total_at_risk:,.2f}** total value at risk\n")

    md.append("---\n")
    md.append("## 🔴 Needs Your Attention")
    md.append("_Review these first — escalations and final notices._\n")
    md.append(_format_red_section(red))

    md.append("---\n")
    md.append("## 🟡 Reminders Ready to Send")
    md.append("_Review each drafted email, then approve and send._\n")
    md.append(_format_amber_section(amber))

    md.append("---\n")
    md.append("## 🟢 No Action Needed")
    md.append("_Paying normally or within terms — listed for completeness._\n")
    md.append(_format_green_section(green))

    briefing_text = "\n".join(md)

    if save:
        BRIEFINGS_DIR.mkdir(exist_ok=True)
        date_file = TODAY.strftime("%Y-%m-%d")
        output_path = BRIEFINGS_DIR / f"briefing_{date_file}.md"
        with open(output_path, "w") as f:
            f.write(briefing_text)
        print(f"Briefing saved to {output_path}")

    return briefing_text


def generate_from_saved_results(results_path=None):
    """
    Load a saved results JSON and generate a briefing from it.
    Lets us regenerate the briefing without re-running the agent.
    """
    if results_path is None:
        date_file = TODAY.strftime("%Y-%m-%d")
        results_path = BRIEFINGS_DIR / f"results_{date_file}.json"

    with open(results_path, "r") as f:
        results = json.load(f)

    return generate_briefing(results, save=True)


# ---------------------------------------------------------------------
# Self-test — generate a briefing from the saved results file
# ---------------------------------------------------------------------

if __name__ == "__main__":
    briefing = generate_from_saved_results()
    print("\n" + "=" * 60)
    print("BRIEFING PREVIEW (first 60 lines)")
    print("=" * 60)
    for line in briefing.split("\n")[:60]:
        print(line)