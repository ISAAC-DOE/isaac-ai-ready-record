"""Availability / summit-scale regression tests.

Two root causes of the 30-user summit failure:
  1. The API ran a single sync gunicorn worker (serialized every request).
  2. Streamlit re-ran init_tables() (~27 DDL statements) on EVERY rerun.

These pin (a) the gunicorn config is multi-worker/threaded, and (b) the
per-process init latch runs the DDL once and only latches on success.
"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "portal"))
import database  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


# --- (1) gunicorn concurrency config -----------------------------------------

def _load_gunicorn_conf():
    spec = importlib.util.spec_from_file_location("gunicorn_conf", REPO / "gunicorn.conf.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gunicorn_is_not_single_sync_worker():
    conf = _load_gunicorn_conf()
    # The whole point: NOT the gunicorn default of 1 sync worker.
    assert conf.worker_class == "gthread", conf.worker_class
    assert conf.workers >= 2, conf.workers
    assert conf.threads >= 2, conf.threads
    # Real concurrency = workers * threads must comfortably exceed a summit crowd.
    assert conf.workers * conf.threads >= 16
    assert conf.timeout >= 60  # long enough for the 60s LLM/wiki endpoints


# --- (2) run-once init latch --------------------------------------------------

def test_run_once_latches_on_success():
    calls = []

    @database._run_once
    def f():
        calls.append(1)
        return True

    assert f() is True
    assert f() is True
    assert len(calls) == 1, "a successful initializer must run only once per process"


def test_run_once_retries_until_success():
    calls = []
    outcomes = iter([False, False, True])

    @database._run_once
    def f():
        calls.append(1)
        return next(outcomes)

    assert f() is False   # failed — must retry
    assert f() is False   # failed — must retry
    assert f() is True    # succeeded — now latched
    assert f() is True    # latched: no re-run
    assert len(calls) == 3, "must retry on failure, then latch on the first success"


def test_init_functions_are_guarded():
    # Proves the decorator is actually applied to the DDL initializers — so a
    # Streamlit rerun cannot re-issue the 27-statement DDL storm.
    assert hasattr(database.init_tables, "_once_state")
    assert hasattr(database.init_discovery_tables, "_once_state")
