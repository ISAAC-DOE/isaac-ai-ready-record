#!/usr/bin/env python3
"""
Post-deploy verification for ISAAC manifest v0.60-provenance.

Compares LIVE production against a pre-deploy baseline and asserts:
  1. the new code is actually serving (manifest version flipped)
  2. everything we added is present and correctly worded
  3. NOTHING regressed: same projects, same hypotheses, same computed confidences,
     same briefing surface (plus the new keys, never minus an old one)
  4. the new columns accept a write and round-trip
  5. identity_trust cannot be self-promoted by a client

Usage:
  python3 tools_verify_deploy.py --baseline <baseline.json>            # read-only
  python3 tools_verify_deploy.py --baseline <baseline.json> --write-probe PROJECT_ID

The write probe is opt-in and writes ONE reasoning_step event to a project you own.
It never touches hypotheses, predictions, verdicts or scores.
"""
import argparse, json, os, sys, urllib.request, urllib.error

BASE = os.environ.get("ISAAC_API_URL", "https://isaac.slac.stanford.edu/portal/api")
TOK = os.environ.get("ISAAC_API_TOKEN")

PASS, FAIL, WARN = [], [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    return ok


def warn(name, detail=""):
    WARN.append(name)
    print("  WARN  " + name + (f"   {detail}" if detail else ""))


def req(path, method="GET", body=None, auth=True):
    h = {"Authorization": "Bearer " + TOK} if auth and TOK else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=90) as resp:
        return json.loads(resp.read() or "null")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--write-probe", default=None,
                    help="project_id you own; writes ONE reasoning_step event")
    ap.add_argument("--expect-version", default="0.60-provenance")
    a = ap.parse_args()
    base = json.load(open(a.baseline))

    print("\n== 1. deployment landed ==")
    health = req("/health", auth=False)
    print(f"  api {base['health']['version']} -> {health.get('version')}")
    m = req("/discovery/manifest")
    check("manifest version is the new one",
          m.get("version") == a.expect_version,
          f"{base['manifest_version']} -> {m.get('version')}")

    print("\n== 2. everything we added is present ==")
    fs = m.get("field_shapes") or {}
    check("field_shapes.actor_model documented", "actor_model" in fs)
    check("field_shapes.decision documented", "decision" in fs)
    check("provenance block present", "provenance" in m)
    prov = m.get("provenance") or {}
    for k in ("cite_to_bind", "content_hash", "evidence_drift", "records_vs_projects"):
        check(f"provenance.{k}", k in prov)
    blob = json.dumps(m)
    check("prime_directive demands actor_model on every event", "SIGN EVERY WRITE" in blob)
    check("prime_directive demands decision on reasoning_step", "SHOW THE ROAD NOT TAKEN" in blob)
    check("identity_trust documented as server-set",
          "client_attested" in json.dumps(fs.get("actor_model") or {}))

    print("\n== 3. nothing regressed ==")
    check("all manifest top-level keys survived",
          set(base["manifest_top_keys"]).issubset(set(m.keys())),
          f"missing: {sorted(set(base['manifest_top_keys']) - set(m.keys()))}")
    projs = req("/projects")
    projs = projs if isinstance(projs, list) else projs.get("projects", [])
    ids = sorted(p.get("project_id") for p in projs)
    check("same number of projects", len(ids) == base["n_projects"],
          f"{base['n_projects']} -> {len(ids)}")
    check("no project disappeared",
          set(base["project_ids"]).issubset(set(ids)),
          f"missing: {sorted(set(base['project_ids']) - set(ids))}")

    for pid, want in (base.get("probe") or {}).items():
        try:
            ctx = req(f"/projects/{pid}/context")
        except Exception as e:
            check(f"{pid} context readable", False, str(e)); continue
        hyps = ctx.get("hypotheses") or []
        got_conf = sorted(round(h.get("confidence") or 0, 6) for h in hyps)
        check(f"{pid} hypothesis count unchanged", len(hyps) == want["n_hyp"],
              f"{want['n_hyp']} -> {len(hyps)}")
        check(f"{pid} COMPUTED CONFIDENCES UNCHANGED", got_conf == want["confidences"],
              f"{want['confidences']} -> {got_conf}")
        b = req(f"/projects/{pid}/briefing")
        check(f"{pid} briefing keys are a superset",
              set(want["briefing_keys"]).issubset(set(b.keys())),
              f"lost: {sorted(set(want['briefing_keys']) - set(b.keys()))}")
        mc = set((b.get("method_compliance") or {}).keys())
        check(f"{pid} method_compliance kept every old key",
              set(want["mc_keys"]).issubset(mc),
              f"lost: {sorted(set(want['mc_keys']) - mc)}")
        for k in ("unattributed_belief_changing_events",
                  "unattributed_belief_changing_count",
                  "reasoning_steps_with_incomplete_decision",
                  "models_seen", "trace_audit_window"):
            check(f"{pid} method_compliance.{k} added", k in mc)

    if a.write_probe:
        print("\n== 4. write probe (one reasoning_step) ==")
        pid = a.write_probe
        payload = {
            "event_type": "reasoning_step",
            "summary": "deploy verification probe (v0.60-provenance)",
            "detail": "Automated post-deploy check. No scientific content.",
            "actor_model": {"provider": "anthropic", "model_id": "claude-opus-5",
                            "identity_trust": "gateway_stamped"},  # must be overridden
            "decision": {"chose": "verify the round-trip",
                         "rejected": ["assume the deploy worked"],
                         "because": ["a schema change is not verified until it round-trips"]},
        }
        try:
            r = req(f"/projects/{pid}/events", method="POST", body=payload)
            eid = r.get("event_id")
            check("event accepted with actor_model + decision", bool(eid), f"event_id={eid}")
            ctx = req(f"/projects/{pid}/context")
            evs = ctx.get("events") or ctx.get("journal") or []
            mine = [e for e in evs if e.get("id") == eid]
            if mine:
                e = mine[0]
                am = e.get("actor_model") or {}
                dec = e.get("decision") or {}
                check("actor_model round-tripped", am.get("model_id") == "claude-opus-5", str(am))
                check("IDENTITY_TRUST CANNOT BE SELF-PROMOTED",
                      am.get("identity_trust") == "client_attested",
                      f"got {am.get('identity_trust')}")
                check("decision round-tripped", dec.get("chose") == "verify the round-trip",
                      str(dec))
            else:
                warn("could not read the probe event back from /context",
                     "check the context serializer exposes actor_model/decision")
        except urllib.error.HTTPError as e:
            check("write probe", False, f"HTTP {e.code}: {e.read()[:200]}")
        except Exception as e:
            check("write probe", False, str(e))

    print("\n" + "=" * 60)
    print(f"PASS {len(PASS)}   FAIL {len(FAIL)}   WARN {len(WARN)}")
    if FAIL:
        print("FAILURES:")
        for f in FAIL:
            print("  - " + f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    if not TOK:
        print("ISAAC_API_TOKEN not set"); sys.exit(2)
    sys.exit(main())
