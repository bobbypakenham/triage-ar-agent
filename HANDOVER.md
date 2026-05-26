# Triage — AR Intelligence Platform: Handover

## What this is

Triage is a Streamlit app that analyses an accounts receivable ledger and classifies each customer as red / amber / green. It uses the Claude API (claude-sonnet-4-5) as an agent to read invoice, payment stats, and communications data, then recommends an action (escalate, send reminder tier 1/2/3, or no action) and drafts an email where needed.

**Run it:** `streamlit run app.py`

---

## Repo structure

```
app.py                  — main Streamlit UI (single file, ~1300 lines)
src/
  agent_claude.py       — Claude agent loop (tool use)
  orchestrator.py       — batch runner, calls agent per customer
  tools.py              — data access functions used by the agent
  csv_handler.py        — parses uploaded aged debtors CSV
  stats.py              — computes payment behaviour stats from history
data/
  customers.json        — customer records (written by CSV upload)
  invoices.json         — invoice records (written by CSV upload)
  communications.json   — comms log (empty [] after CSV upload)
briefings/
  results_YYYY-MM-DD.json  — batch results saved after each run
sample_ledger.csv       — sample Irish business ledger for testing
```

---

## Data flow

1. **Upload Ledger page** — user uploads a CSV aged debtors export
   - `csv_handler.py` auto-detects column names, normalises to `customers.json` / `invoices.json`
   - All stale `briefings/results_*.json` files are deleted
   - `tools.reload_data()` and `st.cache_data.clear()` are called

2. **Daily Briefing page** — if no results file exists, shows "Run Triage" button
   - Button LAZY-IMPORTS `from src.orchestrator import run_batch` (see critical rule below)
   - `run_batch()` loops customers, pre-filters obvious greens, calls agent on the rest
   - Results saved to `briefings/results_YYYY-MM-DD.json`

3. **app.py** loads results via `load_results()` which falls back to the most recent results file if today's doesn't exist yet

---

## Critical rules — do not break these

### 1. Never import `orchestrator` or `agent_claude` at the top of `app.py`

`agent_claude.py` reads `st.secrets.get("ANTHROPIC_API_KEY")` **at module import time** (line 29). When running locally without a `.streamlit/secrets.toml`, this causes `StreamlitSecretNotFoundError` on startup.

The import must stay lazy, inside the button handler only:

```python
if st.button("Run Triage", type="primary"):
    with st.spinner("Analysing accounts…"):
        from src.orchestrator import run_batch   # lazy — do NOT move this to top
        run_batch(verbose=False)
```

### 2. All four cached data getters must guard against error dicts

`tools.py` returns `{"error": "Customer X not found"}` (a dict, not a list/None) when a customer is missing. All callers guard against this:

```python
@st.cache_data
def get_profile(cid):
    r = tools.get_customer_profile(cid)
    return r if isinstance(r, dict) and "error" not in r else {}

@st.cache_data
def get_invoices(cid):
    r = tools.get_open_invoices(cid)
    return r if isinstance(r, list) else []

@st.cache_data
def get_stats(cid):
    r = tools.get_payment_stats(cid)
    return r if isinstance(r, dict) and "error" not in r else {}

@st.cache_data
def get_comms(cid):
    r = tools.get_communications_log(cid)
    return r if isinstance(r, list) else []
```

If you add any new calls to tools functions, apply the same guard. Ghost customers (old results referencing customers no longer in data) are skipped in all loops with `if get_profile(cid) == {}: continue`.

### 3. Stats fields use `is not None` checks, not truthiness

`stats.get('avg_days_to_pay', default)` returns `None` (not default) when the key exists with value `None`. Use explicit `is not None`:

```python
_dtp = stats.get('avg_days_to_pay')
f"{_dtp:.1f}d" if _dtp is not None else "—"   # correct
f"{_dtp:.1f}d" if _dtp else "—"               # WRONG — shows "—" when value is 0
```

### 4. Pre-compute all variables before f-strings used in `st.markdown`

Nested `.format()` or complex expressions inside f-strings passed to `st.markdown(..., unsafe_allow_html=True)` can cause Streamlit to render the HTML as a code block. Always pre-compute:

```python
# Correct pattern
_credit = f"€{profile['credit_limit']:,}" if profile.get('credit_limit') else '—'
st.markdown(f"<div>{_credit}</div>", unsafe_allow_html=True)

# Wrong — can break HTML rendering
st.markdown(f"<div>{'€{:,}'.format(profile['credit_limit']) if profile.get('credit_limit') else '—'}</div>", unsafe_allow_html=True)
```

---

## CSV data limitation

Aged debtors CSV exports contain **only open invoices** — no payment history. This means `stats.py` always returns `behavior_classification: "insufficient_data"` for CSV-uploaded customers. The agent can still analyze based on days overdue, invoice amounts, and communication history. This is expected and accepted.

---

## Navigation structure

Two `st.radio` groups in the sidebar with mutual exclusion via `on_change` callbacks:

- **Workspace**: Daily Briefing · Action Queue · Customers · Upload Ledger (key: `"nav"`)
- **Reports**: AR Analytics · Run History (key: `"reports"`, index=None)

When a Reports item is selected it sets `page = report` and clears the nav state. When a nav item is selected it clears the reports state.

---

## Secrets / API key

- **Local:** create `.streamlit/secrets.toml` with `ANTHROPIC_API_KEY = "sk-ant-..."`
- **Streamlit Cloud:** set `ANTHROPIC_API_KEY` in the app's Secrets panel

---

## Smoke test before committing

```bash
python3 -c "import ast; ast.parse(open('app.py').read()); print('app.py: OK')"
python3 -c "from src import tools; print('tools: OK')"
python3 -c "from src import csv_handler; print('csv_handler: OK')"
python3 -c "from src import orchestrator; print('orchestrator: OK')"
```

---

## Key bugs fixed (context for future work)

| Symptom | Root cause |
|---|---|
| `StreamlitSecretNotFoundError` on startup | `orchestrator` imported at top of `app.py`, triggered `agent_claude` at module load time |
| `TypeError: string indices must be integers` | `tools.get_open_invoices()` returning error dict; callers not guarded |
| `</div>` rendered as code block | Ghost customers causing unmatched HTML tags; Streamlit renders unmatched tags as code |
| `"—d"` in stats display | `if _val` check fails when value is `0.0`; fixed with `is not None` |
| HTML rendered as code block | Nested `.format()` inside f-string in `st.markdown` |
| Double "Clear" tag on green cards | `cls_tag` + `action_tag` both emitted for green/no_action; suppressed `action_tag` when action is `no_action` |
| Contact email mapped to contact name column | `"contact"` pattern in `contact_name` matched "Contact Email"; fixed by reordering `FIELD_PATTERNS` |
