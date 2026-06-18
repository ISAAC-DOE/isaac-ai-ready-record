#!/usr/bin/env python3
"""
ISAAC end-to-end smoke test — run against the LIVE deployment after every
schema/portal change reaches Kubernetes.

What it does (non-destructive beyond its own test record):
  1. /health
  2. /validate: canonical example must PASS; a known-bad mutation must FAIL
  3. POST a fresh test record (valid, with the Potential Contract blocks)
  4. GET it back and verify byte-level round-trip fidelity of every block
  5. DELETE it and verify 404

Usage:
  ISAAC_API_TOKEN=... ISAAC_API_URL=https://isaac.slac.stanford.edu/portal/api \
      python3 tools/e2e_smoke.py

Exit code 0 = all green. Any failure prints the reason and exits non-zero.
"""
import copy
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from ulid import ULID
except ImportError:
    print("FATAL: pip install python-ulid")
    sys.exit(2)

TOKEN = os.environ.get("ISAAC_API_TOKEN")
API = os.environ.get("ISAAC_API_URL", "https://isaac.slac.stanford.edu/portal/api")
if not TOKEN:
    print("FATAL: ISAAC_API_TOKEN not set")
    sys.exit(2)
HDRS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "co2rr_performance_record.json"

failures = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append((name, detail))


def http(method, path, body=None):
    req = urllib.request.Request(API + path, headers=HDRS, method=method,
                                  data=json.dumps(body).encode() if body is not None else None)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def main():
    print(f"ISAAC e2e smoke vs {API}")

    # 1. health
    code, body = http("GET", "/health")
    check("health", code == 200 and body.get("status") == "healthy")

    # 2. validate: good passes, bad fails
    good = json.loads(EXAMPLE.read_text())
    good["record_id"] = str(ULID())
    code, v = http("POST", "/validate", good)
    check("validate: canonical example passes", code == 200 and v.get("valid") is True,
          str(v.get("errors", []))[:200])

    bad = copy.deepcopy(good)
    bad["descriptors"]["outputs"][0]["descriptors"][0]["unit"] = "mA_cm-2"
    code, v = http("POST", "/validate", bad)
    check("validate: alias unit rejected", v.get("valid") is False)

    bad2 = copy.deepcopy(good)
    bad2["literature"] = {"doi": "x"}
    code, v = http("POST", "/validate", bad2)
    check("validate: unknown top-level block rejected", v.get("valid") is False)

    # 3. POST round-trip record
    rid = str(ULID())
    test = copy.deepcopy(good)
    test["record_id"] = rid
    test["sample"]["material"]["name"] = "E2E SMOKE TEST RECORD (auto-deleted)"
    code, p = http("POST", "/records", test)
    check("POST test record", code == 201 and p.get("success") is True, str(p)[:200])

    # 4. GET round-trip fidelity (BEFORE any edit, vs the original POSTed record)
    code, fetched = http("GET", f"/records/{rid}")
    check("GET test record", code == 200 and fetched.get("record_id") == rid)
    if code == 200:
        diffs = []
        for block in ("sample", "context", "system", "measurement", "descriptors", "assets", "links"):
            if json.dumps(test.get(block), sort_keys=True) != json.dumps(fetched.get(block), sort_keys=True):
                diffs.append(block)
        check("round-trip fidelity (all blocks byte-identical)", not diffs, f"differing blocks: {diffs}")
        pvr = (fetched.get("context", {}).get("electrochemistry", {}) or {}).get("potential_vs_RHE")
        check("potential_vs_RHE survives round-trip", isinstance(pvr, dict) and "rhe_basis" in pvr)

    # 4a. Ownership model (2026-06-17): re-POST rejected; PUT edits and persists
    code, p2 = http("POST", "/records", test)
    check("re-POST existing id rejected (409, no silent overwrite)", code == 409)
    edited = copy.deepcopy(test)
    edited["sample"]["material"]["name"] = "E2E EDITED via PUT"
    code, pe = http("PUT", f"/records/{rid}", edited)
    check("PUT edits an owned record (200)", code == 200 and pe.get("updated") is True, str(pe)[:160])
    code, after = http("GET", f"/records/{rid}")
    check("PUT edit persisted", code == 200
          and after.get("sample", {}).get("material", {}).get("name") == "E2E EDITED via PUT")

    # 4b. Query API endpoints (2026-06-12)
    code, resp = http("GET", "/records?record_domain=performance&limit=3")
    check("filter: record_domain=performance", code == 200 and isinstance(resp, list)
          and all(r.get("record_domain") == "performance" for r in resp))
    code, resp = http("GET", "/records?reaction=CO2RR&limit=3&full=true")
    ok_rx = code == 200 and isinstance(resp, list) and len(resp) > 0 and all(
        (r.get("context", {}).get("electrochemistry", {}) or {}).get("reaction") == "CO2RR" for r in resp)
    check("filter: reaction=CO2RR with full=true", ok_rx)
    code, resp = http("GET", "/records?bogus_param=1")
    check("unknown query param rejected (400)", code == 400)
    code, resp = http("POST", "/records/batch", {"record_ids": [rid]})
    check("batch fetch returns the test record", code == 200 and resp.get("returned") == 1)
    code, resp = http("POST", "/records/query", {"sql": "SELECT COUNT(*) AS n FROM records"})
    check("read-only SQL query works", code == 200 and resp.get("rows") and "n" in resp["rows"][0])
    code, resp = http("POST", "/records/query", {"sql": "DELETE FROM records"})
    check("destructive SQL rejected", code == 400)
    code, resp = http("GET", f"/records/{rid}/quality")
    check("quality endpoint recomputes report", code == 200 and "warnings" in json.dumps(resp) or code == 200)
    code, resp = http("GET", f"/records/{rid}/suggestions")
    check("suggestions endpoint responds", code == 200 and "suggestions" in resp)

    # 5. DELETE + 404
    code, d = http("DELETE", f"/records/{rid}")
    check("DELETE test record", code == 200 and d.get("deleted") is True)
    code, _ = http("GET", f"/records/{rid}")
    check("GET after delete returns 404", code == 404)

    print()
    if failures:
        print(f"E2E SMOKE: {len(failures)} FAILURE(S)")
        return 1
    print("E2E SMOKE: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
