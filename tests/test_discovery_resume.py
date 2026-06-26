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
                       _canon_resume_status)


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


def test_contested_membership_wins():
    r = {"label": "H", "status": "proposed", "computed_confidence": 0.85, "reliable": True}
    assert _true_status_from_ranking(r, {"H"}) == "contested"


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
