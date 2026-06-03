"""
scheduler.py — Daily auto-run scheduler for Triage.

Runs the triage batch automatically once a day at a configurable time using
APScheduler's BackgroundScheduler. The scheduler lives inside the Streamlit
process (a daemon thread), so it must be started exactly once per process and
must survive Streamlit's constant top-to-bottom script reruns.

Design notes:
- start_scheduler() is idempotent at the *process* level: callers guard with
  st.session_state (which is per-session), but we also keep a module-level
  handle so even multiple browser sessions in one process can never spin up a
  second scheduler.
- The job delegates to runner.start_run(), which already runs the batch on its
  own daemon thread and refuses to launch a second run while one is in flight.
  If a manual run (or a previous scheduled run) is still going, the job logs
  and skips rather than queueing a duplicate.
- We never run on an empty database — a scheduled batch over zero customers is
  pointless and avoids churn before the first ledger upload.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from src import runner, database

logger = logging.getLogger("triage.scheduler")

DEFAULT_RUN_TIME = "06:00"

# Off by default: the scheduler runs only when SCHEDULER_ENABLED is explicitly
# set to true in secrets. This avoids any deployment silently incurring a daily
# API batch (~$0.50/run) that nobody asked for.
DEFAULT_ENABLED = False

_JOB_ID = "daily_triage_batch"

# Module-level handle so we never build more than one scheduler per process,
# regardless of how many Streamlit sessions call start_scheduler().
_scheduler = None


def parse_time(value):
    """Parse an 'HH:MM' string into (hour, minute), falling back to the default
    run time on anything malformed."""
    try:
        hh, mm = str(value).strip().split(":")
        hour, minute = int(hh), int(mm)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, AttributeError):
        pass
    h, m = DEFAULT_RUN_TIME.split(":")
    return int(h), int(m)


def _run_daily_batch():
    """Scheduler job target: start the batch unless there's nothing to do or a
    run is already in progress. Never raises — failures are logged so a bad job
    can't take down the scheduler thread."""
    try:
        if not database.get_all_customers():
            logger.info("Scheduled run skipped: no customers in the database.")
            return
        if runner.is_running():
            logger.info("Scheduled run skipped: a batch is already in progress.")
            return
        if runner.start_run():
            logger.info("Scheduled run started.")
        else:
            # start_run() returns False if a run was launched in the gap between
            # the is_running() check above and this call — same outcome: skip.
            logger.info("Scheduled run skipped: a batch is already in progress.")
    except Exception:
        logger.exception("Scheduled run failed to start.")


def start_scheduler(run_time=DEFAULT_RUN_TIME):
    """Create and start the BackgroundScheduler with a daily cron job at
    run_time ('HH:MM'). Idempotent: if a scheduler is already running in this
    process, return it unchanged instead of starting a second one."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    hour, minute = parse_time(run_time)
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        _run_daily_batch,
        trigger="cron",
        hour=hour,
        minute=minute,
        id=_JOB_ID,
        replace_existing=True,
        coalesce=True,           # collapse multiple missed runs into a single one
        misfire_grace_time=3600,  # still run if the process was busy at the exact minute
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Scheduler started; daily run at %02d:%02d.", hour, minute)
    return scheduler


def is_started():
    """True if a scheduler is running in this process."""
    return _scheduler is not None and _scheduler.running
