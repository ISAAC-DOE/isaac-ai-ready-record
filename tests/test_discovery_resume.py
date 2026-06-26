"""
Resume-comprehension unit tests (Stage 5). Pure functions, no DB:
- `_resume_synthesis` composes the server-side state-of-the-project a resuming agent
  reads first.
- `_true_status_from_ranking` / `_canon_resume_status` are the ground-truth the
  comprehension check diffs an agent's stated understanding against — the part that
  catches the #1 resume error (calling an unreliable front-runner 'established').
"""

import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(PORTAL))

from discovery import (_resume_synthesis, _true_status_from_ranking,  # noqa: E402
                       _canon_resume_status, _compose_headline,
                       compute_hypothesis_score)


def _ev(v, ws="evaluated", s="weak"):
    return {"verdict": v, "strength": s, "work_status": ws}


# --- the comprehension diff's ground-truth mapping ----------------------------

def test_unreliable_frontrunner_is_undetermined_not_supported():
    # the single most important case: a high-ish confidence but UNRELIABLE leader is
    # UNDETERMINED, never 'supported' — this is what the resume check must catch.
    r = {"label": "H", "status": "proposed", "computed_confidence": 0.6, "reliable": False}
    assert _true_status_from_ranking(r, set()) == "undetermined"


def test_reliable_low_is_refuted():
    r = {"label": "H", "status": "proposed", "computed_confidence": 0.15, "reliable": True}
    assert _true_status_from_ranking(r, set()) == "refuted"


def test_reliable_high_is_supported():
    r = {"label": "H", "status": "proposed", "computed_confidence": 0.85, "reliable": True}
    assert _true_status_from_ranking(r, set()) == "supported"


def test_decided_wins_over_contested_membership():
    # a reliably-DECIDED hypothesis is refuted/supported even if still a nominal cluster
    # member — this is the Run-11 fix (H-INTERFACE was refuted but showed 'contested').
    refuted = {"label": "H", "status": "proposed", "computed_confidence": 0.15, "reliable": True}
    assert _true_status_from_ranking(refuted, {"H"}) == "refuted"
    supported = {"label": "H", "status": "proposed", "computed_confidence": 0.85, "reliable": True}
    assert _true_status_from_ranking(supported, {"H"}) == "supported"


def test_undecided_member_is_contested():
    # an UNDECIDED member (reliable but mid-confidence, or unreliable) in a cluster → contested
    mid = {"label": "H", "status": "proposed", "computed_confidence": 0.34, "reliable": True}
    assert _true_status_from_ranking(mid, {"H"}) == "contested"
    unreliable = {"label": "H", "status": "proposed", "computed_confidence": 0.6, "reliable": False}
    assert _true_status_from_ranking(unreliable, {"H"}) == "contested"


def test_eliminated_status_is_refuted():
    r = {"label": "H", "status": "eliminated", "computed_confidence": 0.5, "reliable": False}
    assert _true_status_from_ranking(r, set()) == "refuted"


def test_canon_status_synonyms():
    assert _canon_resume_status("established") == "supported"
    assert _canon_resume_status("front-runner") == "undetermined"
    assert _canon_resume_status("ruled out") == "refuted"
    assert _canon_resume_status("UNRESOLVED") == "undetermined"
    assert _canon_resume_status("contested") == "contested"


# --- the server-composed synthesis --------------------------------------------

def _data():
    return {
        "project": {"goal": "explain selectivity"},
        "hypotheses": [
            {"label": "H1", "status": "proposed", "grounding": "standing_prior",
             "predictions": [_ev("contradicts", s="strong"), _ev("contradicts", s="moderate")]},
            {"label": "H2", "status": "proposed", "grounding": "ad_hoc",
             "predictions": [_ev("supports"), _ev("neutral"),
                             {"verdict": None, "work_status": "compute_failed",
                              "descriptor_name": "mkm"}]},
        ],
        "next_experiment": {"descriptor": "CORR rescue"},
    }


def test_synthesis_marks_reliable_refutation_established():
    syn = _resume_synthesis(_data(),
                            {"convergence": {"contested_clusters": [], "decision_distance": 0.5},
                             "recommended_actions": ["run the discriminating test"]},
                            {"items": [{"summary": "poll VASP job"}]},
                            [{"event_type": "reasoning_step", "summary": "tandem hinges on CO supply"}])
    # H1: two strong/moderate contradictions → reliable, capped low → refuted/established
    labels = {e["label"]: e for e in syn["established"]}
    assert "H1" in labels and labels["H1"]["conclusion"] == "refuted"
    # leader is H2 (higher confidence) but UNRELIABLE → caveat present
    assert syn["leader"]["label"] == "H2"
    assert syn["leader"]["reliable"] is False
    assert syn["leader"]["caveat"]


def test_synthesis_surfaces_tried_and_failed_and_open_loops():
    syn = _resume_synthesis(_data(),
                            {"convergence": {"contested_clusters": []}, "recommended_actions": []},
                            {"items": [{"summary": "poll VASP job"}]},
                            [{"event_type": "reasoning_step", "summary": "why we pivoted"}])
    assert "H2/mkm" in syn["tried_and_failed"]["compute_failed_rerun_todo"]
    assert syn["open_loops"]["pending"] == ["poll VASP job"]
    assert syn["open_loops"]["next_experiment"] == "CORR rescue"
    assert syn["recent_reasoning"] == ["why we pivoted"]
    assert "how_to_read_this" in syn and "UNDETERMINED" in syn["how_to_read_this"]


def test_decided_member_dropped_from_contested():
    # H1 is reliably refuted; even if convergence lists it in a contested cluster, the
    # synthesis must NOT show it as still_contested (no double-listing with established).
    syn = _resume_synthesis(
        _data(),
        {"convergence": {"contested_clusters": [
            {"survivors": ["H1", "H2"], "state": "blocked_on_experiment",
             "blocking_experiments": ["CORR rescue"]}]},
         "recommended_actions": []},
        {"items": []}, [])
    assert any(e["label"] == "H1" and e["conclusion"] == "refuted" for e in syn["established"])
    # cluster had {H1(decided), H2}; H1 dropped → only H2 left → <2 → no contested entry
    contested_labels = {m for c in syn["still_contested"] for m in c["members"]}
    assert "H1" not in contested_labels


# --- Headline tier ("X because Y unless Z") ------------------------------------

def _hp(label, status="proposed", grounding=None, preds=None):
    return {"label": label, "status": status, "grounding": grounding,
            "predictions": preds or []}


def _p(verdict, strength="strong", descriptor="d", ev=None, cross_system=None):
    return {"verdict": verdict, "strength": strength, "work_status": "evaluated",
            "descriptor_name": descriptor, "evidence_record_ids": ev or [],
            "margin": None, "cross_system": cross_system, "evidence_independence": None,
            "reliability_tier": None}


def _scored(*hyps):
    return [(h, compute_hypothesis_score({"predictions": h["predictions"],
                                          "grounding": h.get("grounding")})) for h in hyps]


def test_headline_caps_at_three_units():
    hyps = [_hp(f"H{i}", preds=[_p("contradicts", ev=["a"]), _p("contradicts", ev=["b"])])
            for i in range(5)]
    sc = _scored(*hyps)
    est = [{"label": h["label"], "conclusion": "refuted"} for h, _ in sc]
    hl = _compose_headline(sc, est, [])
    assert len(hl["units"]) <= 3


def test_headline_delivers_clean_supported_when_robust():
    # 3 independent supports → reliably supported AND not dangerously fragile → clean
    h = _hp("H1", preds=[_p("supports", ev=["a"]), _p("supports", ev=["b"]),
                         _p("supports", ev=["c"])])
    sc = _scored(h)
    hl = _compose_headline(sc, [{"label": "H1", "conclusion": "supported"}], [])
    assert hl["verdict"] == "supported"
    assert hl["units"][0]["claim"] == "H1 is supported"
    assert "_fail_loud" not in hl


def test_headline_degrades_borrowed_support_and_fails_loud():
    # 'supported' but the keystone is a cross-system/borrowed leg → DEGRADE + fail loud
    h = _hp("H1", preds=[_p("supports", ev=["a"]),
                         _p("supports", ev=["b"], cross_system=True)])
    sc = _scored(h)
    hl = _compose_headline(sc, [{"label": "H1", "conclusion": "supported"}], [])
    assert "front-runner" in hl["units"][0]["claim"]
    assert hl["verdict"] != "supported"
    assert "_fail_loud" in hl


def test_headline_unreliable_leader_is_not_an_answer():
    h = _hp("H1", preds=[_p("supports", ev=["a"])])   # 1 decisive → unreliable
    sc = _scored(h)
    hl = _compose_headline(sc, [], [])
    assert "UNRELIABLE" in hl["units"][0]["claim"]
    assert hl["verdict"] == "undetermined"
    assert "_fail_loud" in hl


def test_headline_unit_conclusions_equal_ledger_truth():
    # invariant: a unit's conclusion must match _true_status_from_ranking (resume_check)
    h_ref = _hp("H1", preds=[_p("contradicts", ev=["a"]), _p("contradicts", ev=["b"])])
    h_sup = _hp("H2", preds=[_p("supports", ev=["a"]), _p("supports", ev=["b"]),
                             _p("supports", ev=["c"])])
    sc = _scored(h_ref, h_sup)
    est = [{"label": "H1", "conclusion": "refuted"}, {"label": "H2", "conclusion": "supported"}]
    hl = _compose_headline(sc, est, [])
    for h, s in sc:
        truth = _true_status_from_ranking(
            {"status": h["status"], "computed_confidence": s["computed_confidence"],
             "reliable": s["reliable"], "label": h["label"]}, set())
        unit = next((u for u in hl["units"] if u["hypothesis"] == h["label"]), None)
        assert unit is not None
        # 'refuted'/'supported' claims must match the ledger's decided status
        if truth in ("refuted", "supported"):
            assert truth in unit["claim"]
