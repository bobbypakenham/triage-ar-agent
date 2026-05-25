# Triage - AR Intelligence Platform

An AI agent that replaces the manual morning triage a credit controller does every day. Instead of spending 2-3 hours pulling up accounts, checking payment history, deciding who to chase, and drafting reminder emails — the agent does it overnight and delivers a prioritised briefing at the start of the day.

Built from a credit control background. The morning triage was always the worst part of the job — not because the judgments were hard, but because they were slow, repetitive, and easy to do badly across 50-200 accounts. This is the tool that would have made that job better.

![Triage Dashboard](https://github.com/bobbypakenham/triage-ar-agent/raw/main/docs/screenshot.png)

---

## What it does

Each morning, the system:

1. Loads the customer ledger and computes behavioural statistics for every account from their full payment history
2. Pre-filters obvious cases — customers with nothing overdue and no unanswered reminders are auto-classified green without an API call (~40% of accounts)
3. Runs the remaining customers through Claude Sonnet — the agent calls four tools per customer (profile, open invoices, payment stats, communications log), reasons over the combined picture, and makes a judgment call
4. Produces a structured recommendation: no action / Tier 1 reminder / Tier 2 follow-up / Tier 3 final notice / escalate to human — with a drafted email where appropriate
5. Saves dated results to JSON and serves a morning briefing through a web UI

The human approves everything. The agent compresses the work.

---

## What makes it interesting

**Pattern recognition across the full ledger.** The system computes `current_deviation_sigmas` for each customer — how many standard deviations their current invoice is from their own historical payment pattern. A customer who normally pays 6 days *early* sitting at 15 days overdue is a 2.6-sigma event. That gets flagged. A customer who is always 10 days late sitting at 10 days late gets nothing. Same number, completely different response.

**Behavioural classification, not just rules.** Every customer gets a label derived from their payment statistics: `reliable`, `deteriorating_reliable`, `slightly_late`, `moderately_late`, `high_risk`, `erratic`, `slow_but_consistent`, `insufficient_data`. The agent uses these to calibrate tone and urgency. A `deteriorating_reliable` customer (was great, now slipping) gets handled differently from a `high_risk` customer (always been a problem).

**Conservative by design.** The agent escalates when uncertain rather than acting confidently on incomplete data. Fewer than 3 paid invoices → insufficient history → escalate to human. Contradictory signals → escalate. The system prompt says "overconfidence is worse than abstaining." This is intentional for a finance use case where a wrong automated action has real consequences.

**Model-agnostic architecture.** Swap one config line to run on a different model. There's a local Ollama version (`src/agent.py`) that runs the same logic free on a laptop using Qwen 2.5 — useful for development and privacy-sensitive deployments.

---

## Architecture

```
data/          → JSON files (customers, invoices, communications)
src/
  stats.py     → Pure Python payment behaviour statistics
  tools.py     → Five functions the agent calls (profile, invoices, stats, comms, record)
  prompts.py   → System prompt encoding the decision logic
  agent_claude.py → Claude Sonnet agent loop (~150 lines)
  agent.py     → Local Ollama version
  orchestrator.py → Batch runner with pre-filter and result saving
  briefing.py  → Generates dated markdown briefing from results
app.py         → Streamlit UI (Daily Briefing · Action Queue · Customer History)
run.py         → Entry point: runs batch and generates briefing
```

The stats layer is pure deterministic Python - no LLM involved. Average days to pay, standard deviation, trend, lateness relative to payment terms, deviation from normal pattern. The LLM does language and judgment; Python does maths.

---

## Demo data

The repo includes synthetic data for 50 customers (35 businesses, 15 individuals) with 12-24 months of payment history per customer, across seven behavioural archetypes: reliable, slow but consistent, deteriorating, erratic, silent then back, problematic, and new customer. Realistic enough to produce meaningful agent reasoning.

From the latest run:
- 7 urgent (escalations + final notices)
- 14 reminders with drafted emails
- 29 no action needed
- £98,957.95 total overdue value
- Full batch: ~7 minutes, ~$0.50 in API costs

---

## Stack

- **Language**: Python 3.13
- **AI model**: Claude Sonnet 4.5 (Anthropic API)
- **Agent framework**: Raw Anthropic SDK — loop built from scratch
- **Statistics**: Python standard library
- **UI**: Streamlit

---

## Setup

```bash
git clone https://github.com/bobbypakenham/triage-ar-agent.git
cd triage-ar-agent

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

Create a `.env` file:

```
ANTHROPIC_API_KEY=your_key_here
```

Run the agent and generate a briefing:
```bash
python run.py
```

Open the UI:
```bash
streamlit run app.py
```

To regenerate the briefing from existing results without re-running the agent:
```bash
python -m src.briefing
```

---

## How it works in production

In production, `data/` would be replaced by live database connections to the company's billing or ERP system. `TODAY` becomes `datetime.now()` so each run reflects the actual current date. The batch runs automatically at 6am via a scheduler. Email sending integrates with Gmail or SendGrid so "Approve & Copy" becomes "Approve & Send."