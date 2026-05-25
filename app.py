"""
app.py — Triage: AR Intelligence Platform
3-page Streamlit app: Daily Briefing · Action Queue · Customer History

Run: streamlit run app.py
Refresh data: python run.py (re-runs the batch), then reload browser
"""

import json
from datetime import datetime
from pathlib import Path
import streamlit as st
from src import tools

# ─────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Triage — AR Intelligence",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────
TODAY    = datetime(2026, 5, 17)
RESULTS  = Path("briefings") / f"results_{TODAY.strftime('%Y-%m-%d')}.json"
RUN_TIME = "06:14"
DATE_LONG  = TODAY.strftime("%A %d %B %Y")
DATE_SHORT = TODAY.strftime("%Y-%m-%d")

# ─────────────────────────────────────────────────────────────────────
# GLOBAL CSS
# ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,400&family=DM+Mono:wght@400;500&display=swap');

/* ── RESET ── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

/* ── BASE ── */
html, body,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
section.main {
    background: #F0F7F5 !important;
    font-family: 'DM Sans', sans-serif !important;
}

/* ── HIDE STREAMLIT CHROME ── */
#MainMenu, footer, header,
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="collapsedControl"],
[data-testid="stStatusWidget"]      { display: none !important; }

/* ── SIDEBAR BASE ── */
[data-testid="stSidebar"] {
    background: #0A4A42 !important;
    width: 210px !important;
    min-width: 210px !important;
    border-right: none !important;
}
[data-testid="stSidebar"] > div:first-child {
    padding: 0 !important;
    height: 100vh;
    display: flex;
    flex-direction: column;
}

/* ── MAIN BLOCK ── */
.main .block-container {
    padding: 0 !important;
    max-width: 100% !important;
}

/* ── RADIO (nav) ── */
div[data-testid="stRadio"] { width: 100%; }
div[data-testid="stRadio"] > label { display: none !important; }
div[data-testid="stRadio"] > div {
    display: flex !important;
    flex-direction: column !important;
    gap: 1px !important;
    padding: 0 10px !important;
    width: 100%;
}
div[data-testid="stRadio"] > div > label {
    display: flex !important;
    align-items: center !important;
    padding: 8px 10px !important;
    border-radius: 6px !important;
    cursor: pointer !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.84rem !important;
    font-weight: 400 !important;
    color: #9FE1CB !important;
    transition: background 0.12s ease !important;
    width: 100%;
}
div[data-testid="stRadio"] > div > label:hover {
    background: rgba(255,255,255,0.07) !important;
    color: #fff !important;
}
div[data-testid="stRadio"] > div > label[data-checked="true"] {
    background: rgba(255,255,255,0.11) !important;
    color: #fff !important;
    font-weight: 500 !important;
    border-right: 2px solid #1D9E75 !important;
}
div[data-testid="stRadio"] > div > label > span:first-child { display: none !important; }

/* ── TABS ── */
[data-testid="stTabs"] [data-testid="stTabBar"] {
    background: transparent !important;
    border-bottom: 1px solid #D6E8E4 !important;
    gap: 0 !important;
    padding: 0 !important;
}
button[data-testid="stTab"] {
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.82rem !important;
    font-weight: 500 !important;
    color: #6B9E94 !important;
    border: none !important;
    background: transparent !important;
    padding: 10px 16px !important;
    border-radius: 0 !important;
}
button[data-testid="stTab"][aria-selected="true"] {
    color: #0A4A42 !important;
    border-bottom: 2px solid #1D9E75 !important;
    font-weight: 600 !important;
}
[data-testid="stTabPanel"] { padding: 20px 0 0 !important; }

/* ── EXPANDERS (email sections) ── */
[data-testid="stExpander"] {
    background: #F8FFFE !important;
    border: 1px solid #C5DDD9 !important;
    border-radius: 6px !important;
    margin-top: 8px !important;
}
[data-testid="stExpander"] summary {
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    color: #0A4A42 !important;
    padding: 8px 12px !important;
}

/* ── TEXTAREA ── */
textarea {
    font-family: 'DM Mono', monospace !important;
    font-size: 0.8rem !important;
    background: #fff !important;
    border: 1px solid #C5DDD9 !important;
    border-radius: 6px !important;
    color: #1A2E2B !important;
    line-height: 1.55 !important;
}

/* ── BUTTONS ── */
[data-testid="stButton"] > button {
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    border-radius: 6px !important;
    padding: 6px 14px !important;
    transition: all 0.15s ease !important;
    border: none !important;
}
[data-testid="stButton"] > button[kind="primary"] {
    background: #1D9E75 !important;
    color: #fff !important;
}
[data-testid="stButton"] > button[kind="primary"]:hover {
    background: #178A64 !important;
}
[data-testid="stButton"] > button[kind="secondary"] {
    background: #fff !important;
    color: #0A4A42 !important;
    border: 1px solid #C5DDD9 !important;
}

/* ── DATAFRAME ── */
[data-testid="stDataFrame"] {
    border: 0.5px solid #D6E8E4 !important;
    border-radius: 8px !important;
    overflow: hidden !important;
}
[data-testid="stDataFrame"] table { font-family: 'DM Sans', sans-serif !important; }

/* ── SELECTBOX ── */
[data-testid="stSelectbox"] > div > div {
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.84rem !important;
    border: 1px solid #C5DDD9 !important;
    border-radius: 6px !important;
    background: #fff !important;
}

/* ── SCROLLBAR ── */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-thumb { background: #9FE1CB; border-radius: 2px; }
::-webkit-scrollbar-track { background: transparent; }

/* ── DIVIDER ── */
hr { border-color: #D6E8E4 !important; margin: 16px 0 !important; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────────────────────────────
@st.cache_data
def load_results():
    if not RESULTS.exists():
        return None
    with open(RESULTS) as f:
        return json.load(f)

@st.cache_data
def get_name(cid):
    p = tools.get_customer_profile(cid)
    return p.get("company_name") or p.get("contact_name") or cid

@st.cache_data
def get_overdue(cid):
    return sum(i["amount"] for i in tools.get_open_invoices(cid) if i["days_past_due"] > 0)

@st.cache_data
def get_profile(cid):  return tools.get_customer_profile(cid)
@st.cache_data
def get_invoices(cid): return tools.get_open_invoices(cid)
@st.cache_data
def get_stats(cid):    return tools.get_payment_stats(cid)
@st.cache_data
def get_comms(cid):    return tools.get_communications_log(cid)

data = load_results()
if data is None:
    st.error("No results file found. Run `python run.py` first.")
    st.stop()

red   = [r for r in data if r["classification"] == "red"]
amber = [r for r in data if r["classification"] == "amber"]
green = [r for r in data if r["classification"] == "green"]
total_risk = sum(get_overdue(r["customer_id"]) for r in red + amber)


# ─────────────────────────────────────────────────────────────────────
# DESIGN HELPERS
# ─────────────────────────────────────────────────────────────────────
BORDERS = {"red": "#D85A30", "amber": "#EF9F27", "green": "#3B915A"}
AMT_COL = {"red": "#993C1D", "amber": "#854F0B", "green": "#3B6D11"}

TAGS = {
    "escalate_to_human": ("Escalate",     "#FAECE7", "#993C1D"),
    "send_tier_3":        ("Final Notice", "#FAECE7", "#993C1D"),
    "send_tier_2":        ("Follow-up",    "#FAEEDA", "#854F0B"),
    "send_tier_1":        ("Reminder",     "#FEF6E4", "#9E6B00"),
    "no_action":          ("Clear",        "#EDFAF4", "#3B6D11"),
}

CLS_TAGS = {
    "red":   ("Urgent",   "#FAECE7", "#993C1D"),
    "amber": ("Reminder", "#FAEEDA", "#854F0B"),
    "green": ("Clear",    "#EDFAF4", "#3B6D11"),
}

def tag(label, bg, color, extra=""):
    return f"""<span style="display:inline-flex;align-items:center;padding:2px 8px;
        border-radius:4px;font-size:0.68rem;font-weight:600;letter-spacing:0.03em;
        background:{bg};color:{color};{extra}">{label}</span>"""

def action_tag(action):
    return tag(*TAGS.get(action, ("—","#eee","#666")))

def cls_tag(cls):
    return tag(*CLS_TAGS.get(cls, ("—","#eee","#666")))

def behavior_tag(b):
    colors = {
        "high_risk":            ("#FAECE7","#993C1D"),
        "erratic":              ("#FEF3E2","#9E6B00"),
        "moderately_late":      ("#FAEEDA","#854F0B"),
        "slightly_late":        ("#FEF6E4","#9E6B00"),
        "deteriorating_reliable":("#FEF3E2","#7A5200"),
        "reliable":             ("#EDFAF4","#3B6D11"),
        "slow_but_consistent":  ("#F0F0FA","#4A4A9A"),
        "insufficient_data":    ("#F0F0F0","#555"),
    }
    bg, color = colors.get(b, ("#F0F0F0","#555"))
    label = b.replace("_", " ").title()
    return tag(label, bg, color)


# ─────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────
with st.sidebar:
    # ── Logo ──
    st.markdown(f"""
    <div style="padding:22px 16px 18px;border-bottom:1px solid rgba(255,255,255,0.08);">
        <div style="display:flex;align-items:center;gap:10px;">
            <div style="width:34px;height:34px;background:#1D9E75;border-radius:9px;
                        display:flex;align-items:center;justify-content:center;
                        font-size:15px;font-weight:700;color:#fff;
                        font-family:'DM Sans',sans-serif;flex-shrink:0;">T</div>
            <div>
                <div style="font-size:1rem;font-weight:600;color:#fff;
                            font-family:'DM Sans',sans-serif;line-height:1.1;">Triage</div>
                <div style="font-size:0.65rem;color:#5DCAA5;letter-spacing:0.04em;">
                    AR Intelligence
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Workspace nav ──
    st.markdown("""
    <div style="padding:16px 16px 4px;">
        <div style="font-size:0.62rem;font-weight:600;color:#5DCAA5;
                    letter-spacing:0.1em;text-transform:uppercase;">Workspace</div>
    </div>
    """, unsafe_allow_html=True)

    page = st.radio(
        "nav",
        ["Daily Briefing", "Action Queue", "Customers"],
        label_visibility="collapsed",
    )

    # ── Reports nav ──
    st.markdown("""
    <div style="padding:16px 16px 4px;margin-top:8px;">
        <div style="font-size:0.62rem;font-weight:600;color:#5DCAA5;
                    letter-spacing:0.1em;text-transform:uppercase;">Reports</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="padding:0 10px;">
        <div style="padding:8px 10px;border-radius:6px;cursor:not-allowed;opacity:0.5;">
            <span style="font-size:0.84rem;color:#9FE1CB;">AR Analytics</span>
        </div>
        <div style="padding:8px 10px;border-radius:6px;cursor:not-allowed;opacity:0.5;">
            <span style="font-size:0.84rem;color:#9FE1CB;">Run History</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Today's snapshot ──
    st.markdown(f"""
    <div style="margin:16px 12px 0;background:rgba(255,255,255,0.06);
                border-radius:8px;padding:12px 14px;">
        <div style="font-size:0.65rem;color:#9FE1CB;margin-bottom:10px;
                    letter-spacing:0.04em;font-weight:500;">TODAY'S SNAPSHOT</div>
        <div style="display:flex;justify-content:space-between;margin-bottom:7px;">
            <span style="font-size:0.78rem;color:rgba(255,255,255,0.7);">Urgent</span>
            <span style="font-size:0.78rem;font-weight:600;color:#E8856B;">{len(red)}</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-bottom:7px;">
            <span style="font-size:0.78rem;color:rgba(255,255,255,0.7);">Reminders</span>
            <span style="font-size:0.78rem;font-weight:600;color:#E8C05A;">{len(amber)}</span>
        </div>
        <div style="display:flex;justify-content:space-between;padding-bottom:2px;
                    border-top:1px solid rgba(255,255,255,0.08);padding-top:7px;margin-top:2px;">
            <span style="font-size:0.78rem;color:rgba(255,255,255,0.7);">Clear</span>
            <span style="font-size:0.78rem;font-weight:600;color:#5DCAA5;">{len(green)}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Spacer
    st.markdown("<div style='flex:1'></div>", unsafe_allow_html=True)

    # ── User avatar at bottom ──
    st.markdown("""
    <div style="padding:14px 16px;border-top:1px solid rgba(255,255,255,0.08);
                margin-top:auto;display:flex;align-items:center;gap:10px;">
        <div style="width:30px;height:30px;background:#1D9E75;border-radius:50%;
                    display:flex;align-items:center;justify-content:center;
                    font-size:0.72rem;font-weight:700;color:#fff;flex-shrink:0;">SC</div>
        <div>
            <div style="font-size:0.78rem;font-weight:500;color:#fff;">Sarah C.</div>
            <div style="font-size:0.65rem;color:#9FE1CB;">Credit controller</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Radio CSS fix ──
    st.markdown("""
    <style>
    div[data-testid="stRadio"] > div > label > div > p {
        font-size: 0.84rem !important;
        line-height: 1 !important;
    }
    </style>
    """, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────
# TOPBAR
# ─────────────────────────────────────────────────────────────────────
def topbar():
    col1, col2 = st.columns([0.85, 0.15])
    with col1:
        st.markdown(f"""
        <div style="background:#fff;border-bottom:1px solid #D6E8E4;
                    padding:14px 32px;display:flex;align-items:center;">
            <div style="font-size:0.8rem;color:#6B9E94;font-family:'DM Sans',sans-serif;">
                {DATE_LONG} &nbsp;·&nbsp; Run completed {RUN_TIME}
            </div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        briefing_path = Path("briefings") / f"briefing_{DATE_SHORT}.md"
        if briefing_path.exists():
            with open(briefing_path) as f:
                briefing_text = f.read()
            st.download_button(
                label="Export Briefing",
                data=briefing_text,
                file_name=f"triage_briefing_{DATE_SHORT}.md",
                mime="text/markdown",
            )


# ─────────────────────────────────────────────────────────────────────
# CUSTOMER CARD COMPONENT
# ─────────────────────────────────────────────────────────────────────
def customer_card(r, idx=0):
    cid     = r["customer_id"]
    name    = get_name(cid)
    overdue = get_overdue(cid)
    cls     = r["classification"]
    action  = r["recommended_action"]
    border  = BORDERS[cls]
    amt_c   = AMT_COL[cls]

    profile  = get_profile(cid)
    invoices = get_invoices(cid)
    stats    = get_stats(cid)

    max_past_due = max((i["days_past_due"] for i in invoices), default=0)
    unanswered   = sum(1 for c in get_comms(cid) if not c["customer_responded"])
    behavior     = stats.get("behavior_classification", "—")
    terms        = profile.get("payment_terms_days", 30)
    days_display = f"+{max_past_due}d" if max_past_due > 0 else "Within terms"

    meta_parts = [f"Net {terms}"]
    if unanswered > 0:
        meta_parts.append(f"{unanswered} unanswered reminder{'s' if unanswered>1 else ''}")
    if max_past_due > 0:
        meta_parts.append(f"{max_past_due} days past due")
    meta_line = " · ".join(meta_parts)

    # Build tag HTML strings first, then embed
    atag = action_tag(action)
    btag = behavior_tag(behavior)
    amt_color = amt_c

    card_html = (
        f'<div style="background:#fff;border:0.5px solid #D6E8E4;'
        f'border-left:3px solid {border};border-radius:8px;'
        f'padding:16px 18px;margin-bottom:8px;">'

        # Row 1: name + tags | amount + days
        f'<div style="display:flex;justify-content:space-between;'
        f'align-items:flex-start;margin-bottom:8px;">'
        f'<div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap;">'
        f'<span style="font-size:0.92rem;font-weight:600;color:#0A2E28;'
        f'font-family:\'DM Sans\',sans-serif;">{name}</span>'
        f'<span style="font-size:0.7rem;color:#8AADA8;'
        f'font-family:\'DM Mono\',monospace;">{cid}</span>'
        f'{atag}{btag}'
        f'</div>'
        f'<div style="text-align:right;flex-shrink:0;margin-left:16px;">'
        f'<div style="font-size:0.95rem;font-weight:700;color:{amt_color};'
        f'font-family:\'DM Mono\',monospace;">£{overdue:,.2f}</div>'
        f'<div style="font-size:0.7rem;font-weight:600;color:{amt_color};'
        f'margin-top:1px;">{days_display}</div>'
        f'</div></div>'

        # Row 2: meta
        f'<div style="font-size:0.74rem;color:#6B9E94;margin-bottom:10px;">'
        f'{meta_line}</div>'

        # Row 3: pattern noticed
        f'<div style="font-size:0.8rem;color:#3A5A54;line-height:1.55;">'
        f'{r["pattern_noticed"]}</div>'

        f'</div>'
    )

    st.markdown(card_html, unsafe_allow_html=True)

    # Email expander — below the card
    if r.get("drafted_email"):
        email = r["drafted_email"]
        contact_email = profile.get("contact_email", "—")
        with st.expander(f"View & approve email — {email['subject']}", expanded=False):
            st.markdown(
                f'<div style="font-size:0.7rem;color:#6B9E94;margin-bottom:6px;'
                f'font-family:\'DM Mono\',monospace;">'
                f'To: {contact_email} &nbsp;|&nbsp; Subject: {email["subject"]}'
                f'</div>'
                f'<div style="font-size:0.74rem;color:#5A7A74;margin-bottom:10px;'
                f'font-style:italic;line-height:1.5;">{r["reasoning"]}</div>',
                unsafe_allow_html=True
            )
            edited = st.text_area(
                "email_body",
                value=email["body"],
                height=160,
                key=f"email_{cid}_{idx}",
                label_visibility="collapsed",
            )
            c1, c2, _ = st.columns([1.4, 1, 4.6])
            with c1:
                if st.button("Approve & Copy",
                             key=f"approve_{cid}_{idx}",
                             type="primary"):
                    st.session_state[f"done_{cid}_{idx}"] = edited
                    st.success("✅ Ready to send")
            with c2:
                if st.button("Skip", key=f"skip_{cid}_{idx}"):
                    pass
            if st.session_state.get(f"done_{cid}_{idx}"):
                st.code(st.session_state[f"done_{cid}_{idx}"], language=None)

    elif cls in ("red", "amber"):
        st.markdown(
            '<div style="background:#FAECE7;border-radius:6px;padding:9px 12px;'
            'font-size:0.78rem;color:#993C1D;margin-bottom:10px;">'
            'Escalated for human review — no automated email drafted.</div>',
            unsafe_allow_html=True
        )


# ─────────────────────────────────────────────────────────────────────
# PAGE 1 — DAILY BRIEFING
# ─────────────────────────────────────────────────────────────────────
if "Daily" in page:
    topbar()

    _, main, _ = st.columns([0.03, 0.94, 0.03])
    with main:

        # ── STAT CARDS ──
        st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)

        with c1:
            st.markdown(f"""
            <div style="background:#0A4A42;border-radius:10px;padding:20px 22px;height:110px;">
                <div style="font-size:0.66rem;font-weight:600;color:#5DCAA5;
                            letter-spacing:0.08em;text-transform:uppercase;
                            margin-bottom:10px;">Total at risk</div>
                <div style="font-size:1.55rem;font-weight:700;color:#fff;
                            font-family:'DM Mono',monospace;line-height:1.1;">
                    £{total_risk:,.0f}
                </div>
                <div style="font-size:0.68rem;color:#9FE1CB;margin-top:6px;">
                    across {len(red)+len(amber)} accounts
                </div>
            </div>""", unsafe_allow_html=True)

        with c2:
            st.markdown(f"""
            <div style="background:#fff;border:0.5px solid #D6E8E4;border-radius:10px;
                        padding:20px 22px;height:110px;">
                <div style="font-size:0.66rem;font-weight:600;color:#9B6B5A;
                            letter-spacing:0.08em;text-transform:uppercase;
                            margin-bottom:10px;">Urgent / escalate</div>
                <div style="font-size:1.55rem;font-weight:700;color:#993C1D;line-height:1.1;">
                    {len(red)}
                </div>
                <div style="font-size:0.68rem;color:#B07060;margin-top:6px;">
                    accounts need action
                </div>
            </div>""", unsafe_allow_html=True)

        with c3:
            st.markdown(f"""
            <div style="background:#fff;border:0.5px solid #D6E8E4;border-radius:10px;
                        padding:20px 22px;height:110px;">
                <div style="font-size:0.66rem;font-weight:600;color:#9B8A5A;
                            letter-spacing:0.08em;text-transform:uppercase;
                            margin-bottom:10px;">Reminders queued</div>
                <div style="font-size:1.55rem;font-weight:700;color:#854F0B;line-height:1.1;">
                    {len(amber)}
                </div>
                <div style="font-size:0.68rem;color:#A08060;margin-top:6px;">
                    emails drafted
                </div>
            </div>""", unsafe_allow_html=True)

        with c4:
            st.markdown(f"""
            <div style="background:#fff;border:0.5px solid #D6E8E4;border-radius:10px;
                        padding:20px 22px;height:110px;">
                <div style="font-size:0.66rem;font-weight:600;color:#5A8A6A;
                            letter-spacing:0.08em;text-transform:uppercase;
                            margin-bottom:10px;">No action needed</div>
                <div style="font-size:1.55rem;font-weight:700;color:#3B6D11;line-height:1.1;">
                    {len(green)}
                </div>
                <div style="font-size:0.68rem;color:#6B9060;margin-top:6px;">
                    accounts healthy
                </div>
            </div>""", unsafe_allow_html=True)

        st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

        # ── TABS ──
        t_overview, t_red, t_amber, t_green = st.tabs([
            "Overview",
            f"Red · {len(red)}",
            f"Amber · {len(amber)}",
            f"Green · {len(green)}",
        ])

        # ── OVERVIEW ──
        with t_overview:
            # Top exposures
            top5 = sorted(red + amber, key=lambda r: get_overdue(r["customer_id"]), reverse=True)[:5]
            if top5:
                st.markdown("""
                <div style="font-size:0.72rem;font-weight:600;color:#0A4A42;
                            letter-spacing:0.04em;text-transform:uppercase;
                            margin-bottom:12px;">Top Accounts by Exposure</div>
                """, unsafe_allow_html=True)
                for r in top5:
                    cid = r["customer_id"]
                    nm  = get_name(cid)
                    ov  = get_overdue(cid)
                    cls = r["classification"]
                    invs = get_invoices(cid)
                    dpd  = max((i["days_past_due"] for i in invs), default=0)
                    st.markdown(f"""
                    <div style="background:#fff;border:0.5px solid #D6E8E4;
                                border-left:3px solid {BORDERS[cls]};
                                border-radius:8px;padding:12px 16px;margin-bottom:6px;
                                display:flex;justify-content:space-between;align-items:center;">
                        <div>
                            <span style="font-size:0.88rem;font-weight:600;
                                         color:#0A2E28;">{nm}</span>
                            <span style="font-size:0.7rem;color:#8AADA8;
                                         font-family:'DM Mono',monospace;
                                         margin-left:7px;">{cid}</span>
                            <div style="font-size:0.74rem;color:#5A7A74;margin-top:4px;">
                                {r['pattern_noticed'][:90]}{'…' if len(r['pattern_noticed'])>90 else ''}
                            </div>
                        </div>
                        <div style="text-align:right;flex-shrink:0;margin-left:16px;">
                            <div style="font-size:0.95rem;font-weight:700;
                                        color:{AMT_COL[cls]};
                                        font-family:'DM Mono',monospace;">
                                £{ov:,.2f}
                            </div>
                            {f'<div style="font-size:0.7rem;color:{AMT_COL[cls]};margin-top:2px;">+{dpd}d</div>' if dpd>0 else ''}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

            st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

            # Green table
            st.markdown(f"""
            <div style="font-size:0.72rem;font-weight:600;color:#0A4A42;
                        letter-spacing:0.04em;text-transform:uppercase;
                        margin-bottom:12px;">No Action Needed — {len(green)} accounts</div>
            """, unsafe_allow_html=True)
            rows = [{"Customer": get_name(r["customer_id"]),
                     "ID": r["customer_id"],
                     "Note": r["pattern_noticed"]} for r in green]
            st.dataframe(rows, use_container_width=True, hide_index=True,
                column_config={
                    "Customer": st.column_config.TextColumn(width="medium"),
                    "ID":       st.column_config.TextColumn(width="small"),
                    "Note":     st.column_config.TextColumn(width="large"),
                })

        # ── RED TAB ──
        with t_red:
            if not red:
                st.success("No urgent cases today.")
            else:
                # Section header
                st.markdown("""
                <div style="font-size:0.68rem;font-weight:700;color:#993C1D;
                            letter-spacing:0.1em;text-transform:uppercase;
                            padding:4px 0 12px;border-bottom:1px solid #FAECE7;
                            margin-bottom:14px;">Escalations</div>
                """, unsafe_allow_html=True)
                for i, r in enumerate(red):
                    customer_card(r, idx=i)

        # ── AMBER TAB ──
        with t_amber:
            if not amber:
                st.info("No reminders to send today.")
            else:
                st.markdown("""
                <div style="font-size:0.68rem;font-weight:700;color:#854F0B;
                            letter-spacing:0.1em;text-transform:uppercase;
                            padding:4px 0 12px;border-bottom:1px solid #FAEEDA;
                            margin-bottom:14px;">Reminders</div>
                """, unsafe_allow_html=True)
                for i, r in enumerate(amber):
                    customer_card(r, idx=100 + i)

        # ── GREEN TAB ──
        with t_green:
            rows = [{"Customer": get_name(r["customer_id"]),
                     "ID": r["customer_id"],
                     "Note": r["pattern_noticed"],
                     "Source": "Agent" if not r.get("_source") else "Pre-filter"
                     } for r in green]
            st.dataframe(rows, use_container_width=True, hide_index=True,
                column_config={
                    "Customer": st.column_config.TextColumn(width="medium"),
                    "ID":       st.column_config.TextColumn(width="small"),
                    "Note":     st.column_config.TextColumn(width="large"),
                    "Source":   st.column_config.TextColumn(width="small"),
                })


# ─────────────────────────────────────────────────────────────────────
# PAGE 2 — ACTION QUEUE
# ─────────────────────────────────────────────────────────────────────
elif "Action" in page:
    topbar()

    _, main, _ = st.columns([0.03, 0.94, 0.03])
    with main:
        st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

        # Filter
        f_col, _, __ = st.columns([2, 3, 3])
        with f_col:
            flt = st.selectbox("filter", ["All", "Urgent only", "Reminders only"],
                               label_visibility="collapsed")

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        show_red   = flt in ("All", "Urgent only")
        show_amber = flt in ("All", "Reminders only")

        if show_red and red:
            st.markdown("""
            <div style="font-size:0.68rem;font-weight:700;color:#993C1D;
                        letter-spacing:0.1em;text-transform:uppercase;
                        padding:4px 0 12px;border-bottom:1px solid #FAECE7;
                        margin-bottom:14px;">Escalations</div>
            """, unsafe_allow_html=True)
            for i, r in enumerate(red):
                customer_card(r, idx=200 + i)

        if show_amber and amber:
            st.markdown(f"""
            <div style="font-size:0.68rem;font-weight:700;color:#854F0B;
                        letter-spacing:0.1em;text-transform:uppercase;
                        padding:4px 0 12px;border-bottom:1px solid #FAEEDA;
                        margin-bottom:14px;margin-top:{'24px' if show_red and red else '0'};">
                Reminders
            </div>
            """, unsafe_allow_html=True)
            for i, r in enumerate(amber):
                customer_card(r, idx=300 + i)

        if not red and not amber:
            st.success("Queue is empty — all accounts are clear.")


# ─────────────────────────────────────────────────────────────────────
# PAGE 3 — CUSTOMERS
# ─────────────────────────────────────────────────────────────────────
elif "Customer" in page:
    topbar()

    _, main, _ = st.columns([0.03, 0.94, 0.03])
    with main:
        st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

        # Search
        all_c = tools.get_all_customers()
        options = {f"{get_name(c['customer_id'])} ({c['customer_id']})": c["customer_id"]
                   for c in all_c}
        sel_label = st.selectbox("search", list(options.keys()),
                                 label_visibility="collapsed",
                                 placeholder="Search by name or ID…")
        cid = options[sel_label]

        st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

        profile  = get_profile(cid)
        stats    = get_stats(cid)
        invoices = get_invoices(cid)
        comms    = get_comms(cid)
        rec      = next((r for r in data if r["customer_id"] == cid), None)
        cls      = rec["classification"] if rec else "green"
        name     = get_name(cid)

        # ── PROFILE HEADER ──
        st.markdown(f"""
        <div style="background:#fff;border:0.5px solid #D6E8E4;
                    border-left:4px solid {BORDERS[cls]};border-radius:10px;
                    padding:20px 24px;margin-bottom:20px;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <div>
                    <div style="display:flex;align-items:center;gap:10px;margin-bottom:7px;">
                        <span style="font-size:1.1rem;font-weight:700;color:#0A2E28;">{name}</span>
                        <span style="font-size:0.72rem;color:#8AADA8;
                                     font-family:'DM Mono',monospace;">{cid}</span>
                        {cls_tag(cls) if rec else ''}
                        {action_tag(rec['recommended_action']) if rec else ''}
                    </div>
                    <div style="font-size:0.78rem;color:#4A6B65;line-height:1.7;">
                        {profile.get('customer_type','').title()} ·
                        {profile.get('contact_email','—')} ·
                        Net {profile.get('payment_terms_days',30)} terms ·
                        £{profile.get('credit_limit',0):,} credit limit ·
                        Account open {profile.get('account_age_months',0):.0f} months
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ── STATS ROW ──
        scols = st.columns(5)
        s_items = [
            ("Avg Days to Pay",  f"{stats.get('avg_days_to_pay','—')}d",  "#0A4A42"),
            ("Avg Days Late",    f"{stats.get('avg_days_late','—')}d",
             "#854F0B" if stats.get('avg_days_late',0)>0 else "#3B6D11"),
            ("Reliability",      f"{stats.get('reliability_score',0):.0%}",
             "#3B6D11" if stats.get('reliability_score',0)>0.7 else "#993C1D"),
            ("Trend",            stats.get('trend','—').title(),
             "#993C1D" if stats.get('trend')=='deteriorating' else
             "#854F0B" if stats.get('trend')=='stable' else "#3B6D11"),
            ("Classification",   stats.get('behavior_classification','—').replace('_',' ').title(),
             "#0A4A42"),
        ]
        for col, (lbl, val, color) in zip(scols, s_items):
            with col:
                st.markdown(f"""
                <div style="background:#fff;border:0.5px solid #D6E8E4;border-radius:8px;
                            padding:14px 16px;text-align:center;margin-bottom:20px;">
                    <div style="font-size:0.62rem;font-weight:600;color:#6B9E94;
                                letter-spacing:0.07em;text-transform:uppercase;
                                margin-bottom:6px;">{lbl}</div>
                    <div style="font-size:0.95rem;font-weight:700;color:{color};
                                font-family:'DM Mono',monospace;">{val}</div>
                </div>
                """, unsafe_allow_html=True)

        # ── HISTORY TABS ──
        ht1, ht2, ht3 = st.tabs(["Open Invoices", "Communications", "AI Recommendation"])

        with ht1:
            if not invoices:
                st.success("No open invoices.")
            else:
                for inv in invoices:
                    dpd = inv.get("days_past_due", 0)
                    sc  = "#993C1D" if dpd > 0 else "#3B6D11"
                    sl  = f"+{dpd}d overdue" if dpd > 0 else "Within terms"
                    st.markdown(f"""
                    <div style="background:#fff;border:0.5px solid #D6E8E4;border-radius:8px;
                                padding:14px 18px;margin-bottom:8px;
                                display:flex;justify-content:space-between;align-items:center;">
                        <div>
                            <div style="font-size:0.84rem;font-weight:600;color:#0A2E28;
                                        font-family:'DM Mono',monospace;">{inv['invoice_id']}</div>
                            <div style="font-size:0.73rem;color:#5A7A74;margin-top:3px;">
                                Issued {inv['issue_date']} · Due {inv['due_date']} ·
                                {inv['days_outstanding']}d outstanding
                            </div>
                        </div>
                        <div style="text-align:right;">
                            <div style="font-size:0.95rem;font-weight:700;color:#0A4A42;
                                        font-family:'DM Mono',monospace;">
                                £{inv['amount']:,.2f}
                            </div>
                            <div style="font-size:0.7rem;font-weight:600;
                                        color:{sc};margin-top:2px;">{sl}</div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

        with ht2:
            if not comms:
                st.info("No prior communications on record.")
            else:
                for c in comms:
                    tc = {1:("#FEF6E4","#9E6B00"),2:("#FAEEDA","#854F0B"),3:("#FAECE7","#993C1D")}
                    bg, color = tc.get(c["tier"], ("#f0f0f0","#666"))
                    resp = "Responded" if c["customer_responded"] else "No response"
                    rc   = "#3B6D11" if c["customer_responded"] else "#993C1D"
                    st.markdown(f"""
                    <div style="background:#fff;border:0.5px solid #D6E8E4;border-radius:8px;
                                padding:14px 18px;margin-bottom:8px;">
                        <div style="display:flex;justify-content:space-between;
                                    align-items:center;margin-bottom:6px;">
                            <div style="display:flex;align-items:center;gap:8px;">
                                <span style="background:{bg};color:{color};padding:2px 8px;
                                             border-radius:4px;font-size:0.68rem;
                                             font-weight:600;">Tier {c['tier']}</span>
                                <span style="font-size:0.73rem;color:#5A7A74;">{c['date_sent']}</span>
                            </div>
                            <span style="font-size:0.7rem;font-weight:600;color:{rc};">{resp}</span>
                        </div>
                        {f'<div style="font-size:0.77rem;color:#3A5A54;background:#F0F7F5;border-radius:4px;padding:6px 8px;">{c["response_summary"]}</div>' if c.get("response_summary") else ""}
                    </div>
                    """, unsafe_allow_html=True)

        with ht3:
            if not rec:
                st.info("Auto-classified — no agent recommendation generated.")
            else:
                st.markdown(f"""
                <div style="background:#fff;border:0.5px solid #D6E8E4;
                            border-left:4px solid {BORDERS[cls]};border-radius:10px;
                            padding:20px 24px;margin-bottom:16px;">
                    <div style="font-size:0.72rem;font-weight:600;color:#6B9E94;
                                text-transform:uppercase;letter-spacing:0.06em;
                                margin-bottom:8px;">Pattern Noticed</div>
                    <div style="font-size:0.85rem;color:#1A3A34;background:#F0F7F5;
                                border-radius:6px;padding:10px 14px;margin-bottom:16px;
                                line-height:1.55;">{rec['pattern_noticed']}</div>
                    <div style="font-size:0.72rem;font-weight:600;color:#6B9E94;
                                text-transform:uppercase;letter-spacing:0.06em;
                                margin-bottom:8px;">Reasoning</div>
                    <div style="font-size:0.82rem;color:#3A5A54;line-height:1.6;
                                margin-bottom:16px;">{rec['reasoning']}</div>
                    <div style="display:flex;gap:8px;">
                        {action_tag(rec['recommended_action'])}
                        {cls_tag(rec['classification'])}
                    </div>
                </div>
                """, unsafe_allow_html=True)

                if rec.get("drafted_email"):
                    email = rec["drafted_email"]
                    st.markdown(f"""
                    <div style="font-size:0.72rem;font-weight:600;color:#0A4A42;margin-bottom:6px;">
                        Drafted Email
                    </div>
                    <div style="font-size:0.7rem;color:#6B9E94;margin-bottom:8px;
                                font-family:'DM Mono',monospace;">
                        Subject: {email['subject']}
                    </div>
                    """, unsafe_allow_html=True)
                    edited_h = st.text_area("hist_email", value=email["body"],
                                            height=160, key=f"hist_{cid}",
                                            label_visibility="collapsed")
                    if st.button("Approve & Copy", key=f"happrove_{cid}", type="primary"):
                        st.session_state[f"hdone_{cid}"] = edited_h
                        st.success("Ready to send")
                    if st.session_state.get(f"hdone_{cid}"):
                        st.code(st.session_state[f"hdone_{cid}"], language=None)