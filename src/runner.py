"""
runner.py — Background batch runner for the Streamlit UI.

Streamlit Community Cloud will drop a request that takes too long, but a full
triage batch over a real ledger takes several minutes. So we run the batch on a
daemon thread and keep its progress in a process-global dict. The Streamlit
script (which reruns constantly) just polls that dict and redraws a progress
bar — each rerun is sub-second, so nothing ever times out.

Why a module global and not st.session_state: the worker thread has no Streamlit
ScriptRunContext, so it cannot touch session_state or any st.* API. A plain
module-level dict guarded by a lock is shared across the single Streamlit
process (main thread + worker thread + every rerun), which is exactly what we
need. Results are written to SQLite by the orchestrator as each customer
completes, so the UI sees them appear progressively.
"""

import threading
from datetime import datetime

_LOCK = threading.Lock()
_STATE = {
    "running": False,      # is a batch currently in flight?
    "done": 0,             # customers completed so far
    "total": 0,            # total customers in this run
    "current": "",         # label of the customer being analysed
    "error": None,         # error string if the run failed
    "finished_at": None,   # datetime the last run ended
}


def get_state() -> dict:
    """Return a snapshot copy of the current run state (safe to read freely)."""
    with _LOCK:
        return dict(_STATE)


def is_running() -> bool:
    with _LOCK:
        return _STATE["running"]


def _set_progress(done, total, label):
    with _LOCK:
        _STATE["done"] = done
        _STATE["total"] = total
        _STATE["current"] = label


def _worker(user_id="default"):
    # Imported lazily so importing this module stays cheap and side-effect free.
    from src import database

    # A new thread starts with a fresh contextvars context, so set the user here
    # — every database call in this run then resolves to that user's file.
    database.set_current_user_id(user_id)
    database.ensure_initialized()

    from src.orchestrator import run_batch
    try:
        run_batch(verbose=False, progress_callback=_set_progress)
    except Exception as exc:  # surfaced to the UI via get_state()["error"]
        with _LOCK:
            _STATE["error"] = str(exc)
    finally:
        with _LOCK:
            _STATE["running"] = False
            _STATE["finished_at"] = datetime.now()


def start_run(user_id: str = "default") -> bool:
    """Start a batch on a background thread for the given user.

    Returns True if a new run was started, False if one was already running
    (so we never launch two batches at once).
    """
    with _LOCK:
        if _STATE["running"]:
            return False
        _STATE.update({
            "running": True,
            "done": 0,
            "total": 0,
            "current": "",
            "error": None,
            "finished_at": None,
        })
    thread = threading.Thread(
        target=_worker, args=(user_id,), name="triage-batch", daemon=True
    )
    thread.start()
    return True
