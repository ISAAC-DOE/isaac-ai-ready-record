#!/usr/bin/env python3
"""
Benchmark N independent blinded discovery runs of the SAME question.

THE POINT OF THIS SCRIPT IS NOT THE SCIENCE. It measures how well the manifest
CONSTRAINED the agents. Five models answering one question produce two kinds of
divergence, and the whole exercise is sorting them:

  (a) GENUINE SCIENTIFIC AMBIGUITY — the evidence really does admit more than one
      reading. Nothing to fix; this is what open science looks like.
  (b) A HOLE IN THE CONTRACT — the manifest let an agent wander, skip a step, assert
      without citing, or invent structure. Every one of these is a manifest edit.

Only (b) is actionable, and telling them apart is a judgement that must be EVIDENCED.
So this reports the raw material for that judgement rather than a single score.

Three families of measure:

  STEERING     did the agent do what the contract told it to?  (compliance, per run)
  CONVERGENCE  did independent runs land in the same place?    (agreement, across runs)
  COST         what did it take to get there?                  (effort, per run)

Usage:
  python3 benchmark_replicates.py --assignments round1_assignments.json \\
      --keys /secure/path/replicate_keys.json --out round1_report.json
"""
import argparse, json, os, sys, urllib.request, urllib.error, collections, datetime

BASE = os.environ.get("ISAAC_API_URL", "https://isaac.slac.stanford.edu/portal/api")


def get(tok, path):
    r = urllib.request.Request(BASE + path, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read() or "null")


# --------------------------------------------------------------------------
# STEERING: how well did the contract hold this run to the method?
# --------------------------------------------------------------------------
def steering(ctx, briefing):
    """Per-run compliance. Every field here maps to a specific manifest instruction,
    so a low score points at the exact clause that failed to land."""
    hyps = ctx.get("hypotheses") or []
    hist = ctx.get("history") or []
    mc = briefing.get("method_compliance") or {}
    preds = [p for h in hyps for p in (h.get("predictions") or [])]
    decisive = [p for p in preds if p.get("verdict") in ("supports", "contradicts")]

    def frac(n, d):
        return round(n / d, 3) if d else None

    return {
        # method.loop step 1 — competing rivals, and a residual/null competitor
        "n_hypotheses": len(hyps),
        "has_two_or_more_rivals": len(hyps) >= 2,
        "has_residual_null_hypothesis": any(
            (h.get("hypothesis_type") or "").lower() == "residual" or
            "residual" in (h.get("label") or "").lower() or
            "artifact" in ((h.get("statement") or "") + (h.get("mechanism") or "")).lower()
            for h in hyps),
        "grounding_declared": frac(sum(1 for h in hyps if h.get("grounding")), len(hyps)),
        # step 2 — a SET of falsifiers per hypothesis, each with a criterion
        "n_predictions": len(preds),
        "predictions_per_hypothesis": frac(len(preds), len(hyps)),
        "hypotheses_below_min_predictions": len(mc.get("hypotheses_below_min_predictions") or []),
        "predictions_with_falsification_criterion":
            frac(sum(1 for p in preds if p.get("falsification_criterion")), len(preds)),
        # step 3 — provenance on every prediction
        "predictions_with_origin": frac(sum(1 for p in preds if p.get("origin")), len(preds)),
        # step 4 — designed to DISCRIMINATE
        "predictions_with_discriminates":
            frac(sum(1 for p in preds if p.get("discriminates")), len(preds)),
        # step 6 — cited-to-data. The single most gameable instruction in the manifest.
        "decisive_verdicts": len(decisive),
        "decisive_cited": frac(sum(1 for p in decisive
                                   if p.get("evidence_record_ids") or p.get("mlflow_run_url")),
                               len(decisive)),
        "decisive_pinned": frac(sum(1 for p in decisive if p.get("evidence_pins")),
                                len(decisive)),
        # trace contract (policy 60)
        "unattributed_belief_changing": mc.get("unattributed_belief_changing_count"),
        "reasoning_steps_incomplete_decision":
            mc.get("reasoning_steps_with_incomplete_decision_count"),
        "models_seen": mc.get("models_seen") or {},
        # residual method_compliance flags, verbatim, so nothing is hidden by aggregation
        "compliance_flags_nonempty": sorted(
            k for k, v in mc.items()
            if isinstance(v, list) and v and not k.startswith("_")),
        # step 7 — the decider must exist and be registered
        "next_experiment_set": bool((ctx.get("project") or {}).get("next_experiment")),
        "decider_registered_as_prediction": any(
            p.get("verdict") in (None, "") and p.get("discriminates") for p in preds),
    }


def cost(ctx):
    hist = ctx.get("history") or []
    et = collections.Counter(e.get("event_type") for e in hist)
    ts = [e.get("created_at") for e in hist if e.get("created_at")]
    return {"n_events": len(hist), "event_types": dict(et),
            "n_reasoning_steps": et.get("reasoning_step", 0),
            "n_compute_runs": et.get("compute_submitted", 0),
            "first_event": ts[0] if ts else None, "last_event": ts[-1] if ts else None}


# --------------------------------------------------------------------------
# CONVERGENCE: agreement across runs, on four axes that can disagree
# --------------------------------------------------------------------------
def _norm(s):
    return " ".join((s or "").lower().replace("-", " ").replace("_", " ").split())


def convergence(runs):
    """Four axes, deliberately separate. Two runs can pick the same winner while having
    reasoned about entirely different candidate sets, and that tells us the schema is
    doing more work than the model."""
    tops, orders, sets_, nexts = {}, {}, {}, {}
    for tag, r in runs.items():
        hyps = sorted((r["ctx"].get("hypotheses") or []),
                      key=lambda h: h.get("confidence") or 0, reverse=True)
        if hyps:
            tops[tag] = _norm(hyps[0].get("mechanism") or hyps[0].get("label"))
            orders[tag] = [_norm(h.get("mechanism") or h.get("label")) for h in hyps]
            sets_[tag] = {_norm(h.get("mechanism") or h.get("label")) for h in hyps}
        ne = (r["ctx"].get("project") or {}).get("next_experiment") or {}
        nexts[tag] = _norm(ne.get("title") or ne.get("rationale"))[:160]

    def agree(d):
        c = collections.Counter(d.values())
        top, n = (c.most_common(1)[0] if c else ("", 0))
        return {"modal_value": top, "n_agreeing": n, "n_runs": len(d),
                "unanimous": n == len(d) and len(d) > 0}

    jac = {}
    tags = sorted(sets_)
    for i, a in enumerate(tags):
        for b in tags[i + 1:]:
            u = sets_[a] | sets_[b]
            jac[f"{a}~{b}"] = round(len(sets_[a] & sets_[b]) / len(u), 3) if u else None
    return {"top_ranked_mechanism": agree(tops),
            "full_ranked_order_identical": agree({k: "|".join(v) for k, v in orders.items()}),
            "hypothesis_set_overlap_jaccard": jac,
            "next_experiment": agree(nexts),
            "per_run_top": tops}


# --------------------------------------------------------------------------
# INDEPENDENCE: was any run contaminated by a sibling?
# --------------------------------------------------------------------------
def independence(runs, accounts, window):
    """Records are a SHARED repository by design. That is correct, and it means a run
    could in principle cite a record a sibling created mid-window. Detectable, because
    records carry attribution.uploaded_by. Measured, not assumed."""
    cited = {}
    for tag, r in runs.items():
        ids = set()
        for h in (r["ctx"].get("hypotheses") or []):
            for p in (h.get("predictions") or []):
                ids.update(p.get("evidence_record_ids") or [])
        cited[tag] = ids
    return {"cited_record_ids_per_run": {k: sorted(v) for k, v in cited.items()},
            "shared_citations": {f"{a}~{b}": sorted(cited[a] & cited[b])
                                 for i, a in enumerate(sorted(cited))
                                 for b in sorted(cited)[i + 1:]},
            "note": "cross-check uploaded_by of any shared id against the replicate "
                    "accounts to detect sibling contamination; overlap alone is EXPECTED "
                    "because they share one evidence base"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assignments", required=True)
    ap.add_argument("--keys", required=True)
    ap.add_argument("--out", default="replication_report.json")
    a = ap.parse_args()
    assign = json.load(open(a.assignments))
    keys = json.load(open(a.keys))

    runs = {}
    for tag, meta in sorted(assign.items()):
        tok = keys[meta["account"]]
        pid = meta["project_id"]
        try:
            runs[tag] = {"meta": meta,
                         "ctx": get(tok, f"/projects/{pid}/context"),
                         "brief": get(tok, f"/projects/{pid}/briefing")}
        except Exception as e:
            print(f"  {tag}: FETCH FAILED {e}")

    report = {"generated_utc": datetime.datetime.utcnow().isoformat() + "Z",
              "steering": {t: steering(r["ctx"], r["brief"]) for t, r in runs.items()},
              "cost": {t: cost(r["ctx"]) for t, r in runs.items()},
              "convergence": convergence(runs),
              "independence": independence(runs, assign, None)}
    json.dump(report, open(a.out, "w"), indent=1)

    # human-readable summary
    print("\n== STEERING (did the contract hold them?) ==")
    cols = ["n_hypotheses", "has_residual_null_hypothesis", "predictions_per_hypothesis",
            "predictions_with_falsification_criterion", "predictions_with_discriminates",
            "decisive_cited", "decisive_pinned", "unattributed_belief_changing",
            "reasoning_steps_incomplete_decision"]
    print("%-4s %s" % ("run", "  ".join(c[:14] for c in cols)))
    for t in sorted(report["steering"]):
        s = report["steering"][t]
        print("%-4s %s" % (t, "  ".join(str(s.get(c))[:14].ljust(14) for c in cols)))
    print("\n== CONVERGENCE ==")
    c = report["convergence"]
    for k in ("top_ranked_mechanism", "full_ranked_order_identical", "next_experiment"):
        v = c[k]
        print("  %-30s %d/%d agree   %s" % (k, v["n_agreeing"], v["n_runs"],
                                            str(v["modal_value"])[:52]))
    print("  hypothesis-set overlap (Jaccard):", c["hypothesis_set_overlap_jaccard"])
    print("\nwrote", a.out)


if __name__ == "__main__":
    sys.exit(main())
