"""
auth.py — Simple password gate for Triage.

Triage holds a customer's full aged-debtors ledger, so a public URL is a
non-starter for any pilot. This implements Streamlit's recommended
username/password pattern on top of st.secrets — credentials live in
.streamlit/secrets.toml (or the deployment's Secrets panel on Streamlit
Community Cloud), never in the repo.

Two configuration shapes are supported in secrets.toml:

    # multi-user
    [passwords]
    sarah = "her-password"
    director = "another-password"

    # or single shared password
    app_password = "one-shared-password"

If neither is configured (e.g. local dev with no secrets file) the gate is
open, so development isn't blocked. Every real deployment should set one.
"""

import hmac
import streamlit as st


def _secret(key):
    # st.secrets raises if there is no secrets.toml at all; treat that as "unset".
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return None


def _configured_credentials():
    """Return (mode, creds): ('multi', {user: pw}) | ('single', pw) | (None, None)."""
    passwords = _secret("passwords")
    if passwords:
        try:
            return "multi", {str(u): str(p) for u, p in dict(passwords).items()}
        except Exception:
            return None, None
    single = _secret("app_password")
    if single:
        return "single", str(single)
    return None, None


def _check(mode, creds, username, password):
    """Constant-time credential check. Returns the resolved username or None."""
    if mode == "single":
        return "user" if hmac.compare_digest(creds, password) else None
    # multi
    stored = creds.get(username)
    if stored is not None and hmac.compare_digest(stored, password):
        return username
    return None


def _render_login(mode):
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown(
            '<div style="text-align:center;margin:48px 0 8px;">'
            '<div style="display:inline-flex;width:44px;height:44px;background:#1D9E75;'
            'border-radius:11px;align-items:center;justify-content:center;font-size:20px;'
            'font-weight:700;color:#fff;">T</div>'
            '<div style="font-size:1.4rem;font-weight:600;color:#0A2E28;margin-top:12px;">Triage</div>'
            '<div style="font-size:0.8rem;color:#6B9E94;margin-top:2px;">Sign in to continue</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        with st.form("login"):
            username = ""
            if mode == "multi":
                username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)

        if submitted:
            mode2, creds = _configured_credentials()
            resolved = _check(mode2, creds, username, password)
            if resolved:
                st.session_state["_authenticated"] = True
                st.session_state["_auth_user"] = resolved
                st.rerun()
            else:
                st.error("Incorrect username or password.")


def require_login():
    """Block the app until the user authenticates. Call once, early in app.py.

    No-op if no credentials are configured (local dev) or the user is already
    signed in for this session.
    """
    mode, _creds = _configured_credentials()
    if mode is None:
        return  # auth not configured — open
    if st.session_state.get("_authenticated"):
        return
    _render_login(mode)
    st.stop()


def current_user():
    return st.session_state.get("_auth_user")


def logout_button(container=st):
    """Render a logout button (only if auth is configured and the user is in)."""
    mode, _creds = _configured_credentials()
    if mode is None or not st.session_state.get("_authenticated"):
        return
    if container.button("Sign out", key="_logout", use_container_width=True):
        st.session_state.pop("_authenticated", None)
        st.session_state.pop("_auth_user", None)
        st.rerun()
