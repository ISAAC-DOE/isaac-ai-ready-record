"""
Literature gateway — proxies Edison Scientific (FutureHouse PaperQA3) so ANY
agent gets cited-literature search via its normal portal Bearer token and never
touches the Edison key (held server-side as EDISON_PLATFORM_API_KEY, same
secret-handling as the DB creds — never returned to clients).

Edison migrated: the old `api.edisonsci.com` host is DECOMMISSIONED. The live API
is `api.platform.edisonscientific.com` with an api_key -> JWT exchange:
  POST /auth/login {api_key}                  -> {access_token}   (JWT ~300 s)
  POST /v0.1/crows {name, query}  (Bearer JWT) -> task_id
  GET  /v0.1/trajectories/{id}    (Bearer JWT) -> status + answer

This is built to the flow the S3DF practitioner verified against edison-client
0.15.0. Response field names are looked up defensively (they vary across
versions); confirm once a live key is configured.
"""
import os
import time

import requests as http_requests

EDISON_API = "https://api.platform.edisonscientific.com"

# Friendly job -> Edison crow id.
JOB_MAP = {
    "literature": "job-futurehouse-paperqa3",
    "literature_high": "job-futurehouse-paperqa3-high",
    "precedent": "job-futurehouse-paperqa3-precedent",
    "analysis": "job-futurehouse-data-analysis-crow-high",
}

_jwt = {"token": None, "exp": 0.0}


def _key():
    return os.environ.get("EDISON_PLATFORM_API_KEY") or os.environ.get("EDISON_API_KEY")


def is_configured() -> bool:
    return bool(_key())


def _login(force=False) -> str:
    now = time.time()
    if not force and _jwt["token"] and now < _jwt["exp"]:
        return _jwt["token"]
    r = http_requests.post(f"{EDISON_API}/auth/login",
                           json={"api_key": _key()}, timeout=30)
    r.raise_for_status()
    _jwt["token"] = r.json().get("access_token")
    _jwt["exp"] = now + 250  # refresh before the ~300 s expiry
    return _jwt["token"]


def _hdr(force=False):
    return {"Authorization": f"Bearer {_login(force)}",
            "Content-Type": "application/json"}


def _with_relogin(call):
    """Run an Edison call; on 401/403 (stale JWT) re-login once and retry."""
    r = call(_hdr())
    if r.status_code in (401, 403):
        r = call(_hdr(force=True))
    r.raise_for_status()
    return r.json()


def submit(query: str, job: str = "literature") -> str | None:
    name = JOB_MAP.get(job, JOB_MAP["literature"])
    d = _with_relogin(lambda h: http_requests.post(
        f"{EDISON_API}/v0.1/crows", headers=h,
        json={"name": name, "query": query}, timeout=60))
    return d.get("task_id") or d.get("id") or d.get("trajectory_id")


def _deep_find_str(obj, keys, minlen=60, depth=0):
    """Find the first long string under any of `keys` at any depth — PaperQA3 puts
    the answer inside environment_frame, not at the top level."""
    if depth > 7:
        return None
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if isinstance(v, str) and len(v) >= minlen:
                return v
        for v in obj.values():
            r = _deep_find_str(v, keys, minlen, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _deep_find_str(v, keys, minlen, depth + 1)
            if r:
                return r
    return None


def _deep_find_list(obj, keys, depth=0):
    if depth > 7:
        return None
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if isinstance(v, list) and v:
                return v
        for v in obj.values():
            r = _deep_find_list(v, keys, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                r = _deep_find_list(v, keys, depth + 1)
                if r:
                    return r
    return None


def poll(task_id: str) -> dict:
    d = _with_relogin(lambda h: http_requests.get(
        f"{EDISON_API}/v0.1/trajectories/{task_id}", headers=h, timeout=60))
    status = str(d.get("status") or "").lower()
    done = status in ("success", "completed", "complete", "done", "failed", "error")
    # The answer lives deep in environment_frame (or task_summary as a short form).
    answer = (_deep_find_str(d, ["formatted_answer", "answer", "response"])
              or (d.get("task_summary") if isinstance(d.get("task_summary"), str) else None))
    sources = _deep_find_list(d, ["references", "sources", "contexts", "bib_entries"])
    return {"task_id": task_id, "status": status, "done": done,
            "answer": answer,
            "task_summary": d.get("task_summary") if isinstance(d.get("task_summary"), str) else None,
            "sources": sources,
            "raw_keys": sorted(d.keys())}  # to confirm field names on first live run
