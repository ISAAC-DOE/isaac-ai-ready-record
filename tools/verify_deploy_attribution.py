#!/usr/bin/env python3
"""Verify the actor_model threading deploy: the gap becomes visible, and NOTHING moves.

The change lets an agent's attestation reach hypothesis / prediction / verdict /
next_experiment events instead of being overwritten by the portal's signature. It is
additive, so the bar is high in a specific way:

  1. the new compliance key is present and populated
  2. the misattribution it reports is REAL (cross-checked against the raw ledger)
  3. every computed confidence is byte-identical to the pre-deploy baseline

(3) is the one that matters. Confidence is computed from verdicts and is the single number
the whole discovery engine turns on; an "additive" change that perturbs it has broken the
science while looking clean. Baseline first, compare after.

  # BEFORE the deploy
  python3 verify_deploy_attribution.py --baseline --out baseline.json
  # AFTER
  python3 verify_deploy_attribution.py --check baseline.json [--write-probe PROJECT_ID]

--write-probe is the only end-to-end proof that the fix works: it creates a hypothesis WITH
an actor_model and asserts the event kept the agent's signature rather than the portal's.
It writes to the named project, so point it at a scratch project, never a real one.
"""
import argparse, json, os, sys, urllib.request, urllib.error

BASE = os.environ.get("ISAAC_API_URL", "https://isaac.slac.stanford.edu/portal/api")
TOKEN = os.environ.get("ISAAC_API_TOKEN") or ""

AGENT_INITIATED = {"hypothesis_created", "prediction_added", "prediction_evaluated",
                   "next_experiment_proposed", "evidence_ingested"}


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or "null"), r.status
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()[:400]}, e.code


def project_ids():
    """/projects returns a bare list today and returned {"projects": [...]} historically.
    Handle both in ONE place so a shape change breaks one function, not every caller."""
    r, _ = call("GET", "/projects")
    rows = r if isinstance(r, list) else (r or {}).get("projects") or []
    return [p.get("project_id") for p in rows if isinstance(p, dict) and p.get("project_id")]


def snapshot():
    """Every project, every hypothesis, its COMPUTED confidence."""
    out = {}
    for pid in project_ids():
        ctx, st = call("GET", f"/projects/{pid}/context")
        if st != 200:
            continue
        out[pid] = {h.get("hypothesis_id"): h.get("confidence")
                    for h in (ctx.get("hypotheses") or [])}
    return out


def audit_misattribution():
    """Count agent decisions signed by the portal, straight from the ledger, so the new
    compliance number is checked against the raw data rather than trusted."""
    tot = bad = 0
    per = {}
    for pid in project_ids():
        ctx, st = call("GET", f"/projects/{pid}/context")
        if st != 200:
            continue
        n = m = 0
        for e in (ctx.get("history") or []):
            if e.get("event_type") in AGENT_INITIATED:
                n += 1
                if (e.get("actor_model") or {}).get("model_id") == "portal":
                    m += 1
        if n:
            per[pid] = (m, n)
        tot += n
        bad += m
    return bad, tot, per


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--check")
    ap.add_argument("--out", default="attribution_baseline.json")
    ap.add_argument("--write-probe", help="scratch PROJECT_ID to write a probe hypothesis to")
    a = ap.parse_args()
    if not TOKEN:
        print("FATAL: set ISAAC_API_TOKEN"); return 2

    if a.baseline:
        bad, tot, per = audit_misattribution()
        json.dump({"confidences": snapshot(), "misattributed": bad, "agent_events": tot},
                  open(a.out, "w"), indent=1)
        print(f"baseline written to {a.out}")
        print(f"  agent decisions signed by portal: {bad}/{tot}")
        return 0

    if not a.check:
        print("FATAL: pass --baseline or --check <file>"); return 2

    base = json.load(open(a.check))
    fails = []

    # (3) nothing moved
    now = snapshot()
    moved = []
    for pid, hyps in base["confidences"].items():
        for hid, conf in hyps.items():
            got = (now.get(pid) or {}).get(hid)
            if got != conf:
                moved.append(f"{pid[-6:]}/{hid[-6:]}: {conf} -> {got}")
    print(f"computed confidences unchanged: {len(moved) == 0} "
          f"({sum(len(v) for v in base['confidences'].values())} checked)")
    if moved:
        fails.append("CONFIDENCE MOVED: " + "; ".join(moved[:8]))

    # (1) the new key exists and (2) agrees with the raw ledger
    bad, tot, per = audit_misattribution()
    print(f"agent decisions signed by portal (raw ledger): {bad}/{tot}")
    checked = 0
    for pid in list(per)[:5]:
        b, st = call("GET", f"/projects/{pid}/briefing")
        if st != 200:
            continue
        mc = b.get("method_compliance") or {}
        if "agent_actions_signed_by_portal_count" not in mc:
            fails.append(f"{pid[-6:]}: briefing missing agent_actions_signed_by_portal_count")
            continue
        checked += 1
        if mc["agent_actions_signed_by_portal_count"] != per[pid][0]:
            fails.append(f"{pid[-6:]}: briefing says "
                         f"{mc['agent_actions_signed_by_portal_count']}, "
                         f"ledger says {per[pid][0]}")
    print(f"briefings reporting the new key, and agreeing with the ledger: {checked}")
    if checked == 0:
        fails.append("no briefing exposed the new compliance key")

    # end-to-end: does an attested write actually keep its signature?
    if a.write_probe:
        pid = a.write_probe
        am = {"provider": "verify", "model_id": "deploy-probe"}
        r, st = call("POST", f"/projects/{pid}/hypotheses",
                     {"statement": "Deploy probe: attestation must survive the write path.",
                      "label": "VERIFY_PROBE", "actor_model": am})
        if st != 201:
            fails.append(f"write-probe POST failed {st}: {str(r)[:200]}")
        else:
            ctx, _ = call("GET", f"/projects/{pid}/context")
            ev = [e for e in (ctx.get("history") or [])
                  if e.get("event_type") == "hypothesis_created"
                  and "VERIFY_PROBE" in (e.get("summary") or "")]
            got = (ev[0].get("actor_model") or {}) if ev else {}
            ok = got.get("model_id") == "deploy-probe"
            print(f"write-probe kept the agent signature: {ok} ({got.get('model_id')})")
            if not ok:
                fails.append("attested hypothesis was STILL signed "
                             f"{got.get('model_id')!r}; the fix did not take")
            # the portal must still refuse to let a client claim a stronger trust tier
            if got.get("identity_trust") != "client_attested":
                fails.append(f"identity_trust was {got.get('identity_trust')!r}")

    print()
    if fails:
        print("FAILED:")
        for f in fails:
            print("  -", f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
