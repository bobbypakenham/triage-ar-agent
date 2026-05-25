"""
generate_data.py — One-off script to create synthetic AR data.

Generates three JSON files in data/:
- customers.json
- invoices.json
- communications.json

50 customers total: 35 businesses + 15 individuals across 7 archetypes
with realistic payment behaviors. Mixed payment terms (Net 7/14/30/60).

Run once: python generate_data.py
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)  # Reproducible — same data every run

TODAY = datetime(2026, 5, 17)
DATA_DIR = Path("data")

# Shuffled at runtime in main() so businesses get unique names
_AVAILABLE_COMPANIES = []


# ---------------------------------------------------------------------
# Archetypes — payment behavior templates, split by customer type.
# Each entry: (mean_days_to_pay, std_dev, count, optional flags)
# ---------------------------------------------------------------------

ARCHETYPES = {
    "reliable":         {"mean_days": 28, "std": 2,  "business": 12, "individual": 5},
    "slow_consistent":  {"mean_days": 42, "std": 3,  "business": 6,  "individual": 2},
    "deteriorating":    {"mean_days": 30, "std": 3,  "business": 6,  "individual": 2, "drift": True},
    "erratic":          {"mean_days": 35, "std": 18, "business": 3,  "individual": 2},
    "silent_then_back": {"mean_days": 30, "std": 4,  "business": 2,  "individual": 1, "gap": True},
    "problematic":      {"mean_days": 55, "std": 12, "business": 4,  "individual": 2},
    "new_customer":     {"mean_days": 30, "std": 3,  "business": 2,  "individual": 1, "max_invoices": 2},
}

FIRST_NAMES = ["Sarah", "Mark", "Emma", "James", "Olivia", "Daniel", "Sophie", "Liam",
               "Chloe", "Ethan", "Grace", "Noah", "Lucy", "Oliver", "Mia", "Harry",
               "Amelia", "George", "Isla", "Jack", "Ava", "Charlie", "Freya", "Leo"]
LAST_NAMES = ["Mitchell", "Reynolds", "Chen", "Patel", "O'Brien", "Walsh", "Andersson",
              "Müller", "Rossi", "Dubois", "Singh", "Kowalski", "Nakamura", "Thompson",
              "Hughes", "Khan", "Murphy", "Foster", "Bailey", "Carter"]
COMPANIES = ["Acme Logistics", "Bright Star Holdings", "Cedar Consulting", "Delta Systems",
             "Evergreen Trading", "Falcon Industries", "Grange Partners", "Horizon Media",
             "Ironbridge Supply", "Juniper Tech", "Keystone Group", "Lighthouse Co",
             "Meridian Labs", "Northwind", "Oakhill Services", "Pinecrest", "Quantum Works",
             "Ridgefield Corp", "Stonebrook", "Tidewater Group", "Umbrella Partners",
             "Vanguard Supplies", "Westport Holdings", "Xenon Systems", "Yorkshire Trading",
             "Zenith Consulting", "Alpine Logistics", "Beacon Group", "Crestview",
             "Drakefield", "Elmtree", "Foxglove", "Greystone", "Hartwell", "Ivybridge",
             "Jasmine Court", "Kingsbury", "Larkspur", "Maplewood", "Nightingale",
             "Orchard Lane", "Pembroke", "Quillington", "Rosewater", "Sterling Bay",
             "Thornfield", "Underwood", "Vineyard Hall", "Wellington Park", "Xander Crest",
             "Yewtree", "Zephyr Trading", "Ashcroft", "Blackthorn", "Cloverfield",
             "Dunbarton", "Edgewater", "Fairhaven", "Goldfinch", "Holloway"]
INDIVIDUAL_EMAIL_DOMAINS = ["gmail.com", "outlook.com", "yahoo.co.uk", "hotmail.com",
                            "icloud.com", "btinternet.com", "protonmail.com"]


def _clean(name):
    """Strip apostrophes and lowercase for email construction."""
    return name.lower().replace("'", "").replace(" ", "")


def business_email(first, last, company):
    return f"{_clean(first)}.{_clean(last)}@{_clean(company)}.co.uk"


def individual_email(first, last):
    domain = random.choice(INDIVIDUAL_EMAIL_DOMAINS)
    return f"{_clean(first)}.{_clean(last)}@{domain}"


def generate_business(customer_id):
    """Create a business customer record."""
    company = _AVAILABLE_COMPANIES.pop()
    has_contact = random.random() > 0.15  # 85% have a named contact
    
    if has_contact:
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        contact_name = f"{first} {last}"
        contact_email = business_email(first, last, company)
    else:
        contact_name = None
        contact_email = f"accounts@{_clean(company)}.co.uk"
    
    days_ago = random.randint(365, 365 * 3)
    account_opened = (TODAY - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    
    return {
        "customer_id": customer_id,
        "customer_type": "business",
        "company_name": f"{company} Ltd",
        "contact_name": contact_name,
        "contact_email": contact_email,
        "account_opened": account_opened,
        "payment_terms_days": random.choice([14, 30, 30, 30, 60]),  # weighted toward Net 30
        "credit_limit": random.choice([10000, 25000, 50000, 100000]),
    }


def generate_individual(customer_id):
    """Create an individual customer record."""
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    contact_name = f"{first} {last}"
    contact_email = individual_email(first, last)
    
    days_ago = random.randint(180, 365 * 2)
    account_opened = (TODAY - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    
    return {
        "customer_id": customer_id,
        "customer_type": "individual",
        "company_name": None,
        "contact_name": contact_name,
        "contact_email": contact_email,
        "account_opened": account_opened,
        "payment_terms_days": random.choice([7, 14, 14, 30]),  # weighted toward Net 14
        "credit_limit": random.choice([500, 1000, 2500, 5000]),
    }


def generate_invoice_history(customer, archetype_name, archetype):
    """Generate past invoices plus current open invoices for one customer."""
    customer_id = customer["customer_id"]
    customer_type = customer["customer_type"]
    terms = customer["payment_terms_days"]
    
    # Amount ranges differ by customer type
    if customer_type == "business":
        amount_range = (800, 8000)
        open_amount_range = (1500, 7500)
    else:
        amount_range = (150, 1500)
        open_amount_range = (200, 1500)
    
    max_invoices = archetype.get("max_invoices", random.randint(14, 24))
    invoices = []
    issue_date = TODAY - timedelta(days=max_invoices * 30 + 5)
    
    for i in range(max_invoices):
        invoice_id = f"INV-{issue_date.year}-{1000 + (int(customer_id[1:]) * 100) + i:04d}"
        due_date = issue_date + timedelta(days=terms)
        amount = round(random.uniform(*amount_range), 2)
        
        # Deteriorating: drift later over time
        if archetype.get("drift"):
            progress = i / max_invoices
            mean_days = archetype["mean_days"] + (progress * 18)
        else:
            mean_days = archetype["mean_days"]
        
        days_to_pay = max(1, int(random.gauss(mean_days, archetype["std"])))
        
        # silent_then_back: go dark in the middle of their history
        if archetype.get("gap") and 5 <= i <= 7:
            days_to_pay = max(1, int(random.gauss(70, 5)))
        
        paid_date = issue_date + timedelta(days=days_to_pay)
        
        invoices.append({
            "invoice_id": invoice_id,
            "customer_id": customer_id,
            "issue_date": issue_date.strftime("%Y-%m-%d"),
            "due_date": due_date.strftime("%Y-%m-%d"),
            "amount": amount,
            "status": "paid",
            "paid_date": paid_date.strftime("%Y-%m-%d"),
        })
        
        issue_date += timedelta(days=random.randint(28, 33))
    
    # Now add the current open invoices
    if archetype_name == "reliable":
        open_count = random.choice([0, 1])
        days_old_options = [10, 20]
    elif archetype_name == "deteriorating":
        open_count = 1
        days_old_options = [32, 38, 42]
    elif archetype_name == "problematic":
        open_count = random.choice([2, 3])
        days_old_options = [45, 60, 75]
    elif archetype_name == "slow_consistent":
        open_count = 1
        days_old_options = [35, 40]
    elif archetype_name == "erratic":
        open_count = 1
        days_old_options = [15, 30, 50]
    elif archetype_name == "silent_then_back":
        open_count = 1
        days_old_options = [25, 35]
    else:  # new_customer
        open_count = 1
        days_old_options = [20, 35]
    
    for j in range(open_count):
        days_old = random.choice(days_old_options)
        open_issue = TODAY - timedelta(days=days_old)
        open_due = open_issue + timedelta(days=terms)
        invoice_id = f"INV-{open_issue.year}-{1000 + (int(customer_id[1:]) * 100) + max_invoices + j:04d}"
        
        # Status depends on whether due date has passed
        status = "overdue" if (TODAY - open_due).days > 0 else "open"
        
        invoices.append({
            "invoice_id": invoice_id,
            "customer_id": customer_id,
            "issue_date": open_issue.strftime("%Y-%m-%d"),
            "due_date": open_due.strftime("%Y-%m-%d"),
            "amount": round(random.uniform(*open_amount_range), 2),
            "status": status,
            "paid_date": None,
        })
    
    return invoices


def generate_communications(customer_id, archetype_name, open_invoices, terms):
    """Generate prior reminder emails for overdue invoices."""
    comms = []
    
    for inv in open_invoices:
        if inv["status"] != "overdue":
            continue
        
        issue_date = datetime.strptime(inv["issue_date"], "%Y-%m-%d")
        due_date = datetime.strptime(inv["due_date"], "%Y-%m-%d")
        days_past_due = (TODAY - due_date).days
        
        # Send Tier 1 reminder a few days after due date
        if days_past_due >= 3:
            comms.append({
                "log_id": f"L{random.randint(10000, 99999)}",
                "customer_id": customer_id,
                "invoice_id": inv["invoice_id"],
                "date_sent": (due_date + timedelta(days=2)).strftime("%Y-%m-%d"),
                "tier": 1,
                "customer_responded": archetype_name == "silent_then_back",
                "response_summary": "Promised payment by end of week" if archetype_name == "silent_then_back" else None,
            })
        
        # Problematic customers also got a Tier 2 chasing
        if days_past_due >= 20 and archetype_name == "problematic":
            comms.append({
                "log_id": f"L{random.randint(10000, 99999)}",
                "customer_id": customer_id,
                "invoice_id": inv["invoice_id"],
                "date_sent": (due_date + timedelta(days=15)).strftime("%Y-%m-%d"),
                "tier": 2,
                "customer_responded": False,
                "response_summary": None,
            })
    
    return comms


def main():
    DATA_DIR.mkdir(exist_ok=True)
    
    # Set up shuffled company pool so every business gets a unique name
    global _AVAILABLE_COMPANIES
    _AVAILABLE_COMPANIES = COMPANIES.copy()
    random.shuffle(_AVAILABLE_COMPANIES)
    
    all_customers = []
    all_invoices = []
    all_comms = []
    
    business_counter = 0
    individual_counter = 0
    customer_counter = 1
    
    # Build a flat list of (archetype_name, customer_type) tuples to iterate through
    build_plan = []
    for archetype_name, archetype in ARCHETYPES.items():
        for _ in range(archetype["business"]):
            build_plan.append((archetype_name, "business"))
        for _ in range(archetype["individual"]):
            build_plan.append((archetype_name, "individual"))
    
    # Shuffle so customer IDs aren't grouped by archetype
    random.shuffle(build_plan)
    
    for archetype_name, customer_type in build_plan:
        archetype = ARCHETYPES[archetype_name]
        cid = f"C{customer_counter:03d}"
        
        if customer_type == "business":
            customer = generate_business(cid)
            business_counter += 1
        else:
            customer = generate_individual(cid)
            individual_counter += 1
        
        invoices = generate_invoice_history(customer, archetype_name, archetype)
        open_invoices = [inv for inv in invoices if inv["status"] in ("open", "overdue")]
        comms = generate_communications(cid, archetype_name, open_invoices, customer["payment_terms_days"])
        
        all_customers.append(customer)
        all_invoices.extend(invoices)
        all_comms.extend(comms)
        customer_counter += 1
    
    with open(DATA_DIR / "customers.json", "w") as f:
        json.dump(all_customers, f, indent=2)
    
    with open(DATA_DIR / "invoices.json", "w") as f:
        json.dump(all_invoices, f, indent=2)
    
    with open(DATA_DIR / "communications.json", "w") as f:
        json.dump(all_comms, f, indent=2)
    
    open_count = sum(1 for i in all_invoices if i["status"] != "paid")
    print(f"Generated {len(all_customers)} customers ({business_counter} businesses, {individual_counter} individuals)")
    print(f"Generated {len(all_invoices)} invoices ({open_count} currently open/overdue)")
    print(f"Generated {len(all_comms)} prior reminder emails")
    print(f"\nFiles written to {DATA_DIR.resolve()}")


if __name__ == "__main__":
    main()