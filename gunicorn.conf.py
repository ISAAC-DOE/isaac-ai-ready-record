"""Gunicorn config for the ISAAC Flask API sidecar.

Gunicorn auto-loads ./gunicorn.conf.py from its working directory (the image's
WORKDIR /app), so these settings apply whether the container is launched via
start.sh or a bare `gunicorn portal.api:app` command in the k8s manifest — no
manifest change required. Command-line flags still override anything here.

WHY THIS EXISTS: the API was running the gunicorn DEFAULT of a single sync
worker, so it processed exactly ONE request at a time. One slow call (a token
revalidation against Authentik, a 60s LLM/wiki call) blocked ALL API traffic;
~30 concurrent summit users queued behind one worker → mass timeouts. Threaded
workers let requests overlap during their (mostly I/O) waits.

All knobs are env-overridable so ops can tune without a rebuild.
"""
import multiprocessing
import os

_cpu = multiprocessing.cpu_count()

# A small pool of gthread workers. Requests here are I/O-bound (DB, Authentik,
# upstream HTTP), so threads give real concurrency without the memory cost of
# many processes. Default: 2..4 workers x 8 threads = up to 16..32 concurrent.
workers = int(os.environ.get("GUNICORN_WORKERS", max(2, min(_cpu, 4))))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "gthread")
threads = int(os.environ.get("GUNICORN_THREADS", 8))

# A slow upstream must not hang a worker forever, but some endpoints legitimately
# take up to ~60s (LLM / literature / wiki). 120s worker timeout with a graceful
# drain covers them without wedging the pool.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 120))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", 30))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", 5))

# Recycle workers periodically to bound any slow leak (e.g. per-request conn
# churn until an app-side pool lands), with jitter so they don't all recycle at
# once.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", 2000))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", 200))

# Import the app ONCE in the master, then fork workers. Workers inherit the
# already-run init latch (database._run_once), so the idempotent DDL init runs
# once per deployment instead of once per worker — and the boot-time migration
# race stays no worse than today (advisory-locking it is a tracked follow-up).
# Also shares code pages copy-on-write (lower memory). We hold no DB connections
# at import time (init opens+closes), so forking is safe.
preload_app = True

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8502")
accesslog = "-"
errorlog = "-"
