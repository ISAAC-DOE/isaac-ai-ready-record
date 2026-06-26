"""
Canonical discovery-scoring unit tests.

`compute_hypothesis_score` is THE single way a hypothesis earns confidence in the
Discovery workbench: confidence is COMPUTED from the prediction verdicts (never
authored). It runs only against a live DB at runtime, so before these tests it had
zero offline coverage — a refactor could silently break the scoring math and the
record-validation battery would never notice. This pins the verdict→log-odds→sigmoid
contract, the schema gate (blocked), the reliability threshold, and the strong-
contradiction cap as pure functions (no DB, no API).

Keep this in lockstep with the manifest's `scoring_model` (discovery.get_manifest):
if a weight or rule changes, the manifest prose and these assertions move together.
"""

import math
import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(PORTAL))

from discovery import compute_hypothesis_score, _STRENGTH_W  # noqa: E402


def _pred(verdict=None, strength=None, work_status="evaluated",
          evidence_record_ids=None, evidence_independence=None):
    return {"verdict": verdict, "strength": strength, "work_status": work_status,
            "evidence_record_ids": evidence_record_ids,
            "evidence_independence": evidence_independence}


# evidence_independence that is CIRCULAR (model fit to the data it's tested on)
_CIRCULAR = {"parameters_fit_to": ["R1", "R2"], "tested_against": ["R2", "R3"]}


def _h(*preds):
    return {"predictions": list(preds)}


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


# --- the 0.5 prior -------------------------------------------------------------

def test_no_predictions_is_the_prior():
    s = compute_hypothesis_score(_h())
    assert s["computed_confidence"] == 0.5
    assert s["n_decisive"] == 0
    assert s["reliable"] is False


def test_unevaluated_predictions_do_not_move_belief():
    s = compute_hypothesis_score(_h(
        _pred("supports", "strong", work_status="awaiting_evidence"),
        _pred("contradicts", "strong", work_status="compute_running"),
    ))
    assert s["computed_confidence"] == 0.5
    assert s["n_decisive"] == 0
    assert s["breakdown"]["unevaluated"] == 2


def test_failed_compute_never_penalizes_the_score():
    # a crashed/non-converged calc carries no verdict and must not move belief, even
    # alongside a real support: confidence reflects the evidence we actually have.
    only_support = compute_hypothesis_score(_h(_pred("supports", "moderate")))
    with_failed = compute_hypothesis_score(_h(
        _pred("supports", "moderate"),
        _pred(None, "strong", work_status="compute_failed"),   # would-be evidence that failed
    ))
    assert with_failed["computed_confidence"] == only_support["computed_confidence"]
    assert with_failed["n_decisive"] == only_support["n_decisive"]
    assert with_failed["breakdown"]["unevaluated"] == 1   # the failed calc is unevaluated, not a verdict


def test_all_failed_compute_stays_at_the_prior():
    s = compute_hypothesis_score(_h(
        _pred(None, work_status="compute_failed"),
        _pred(None, work_status="compute_failed"),
    ))
    assert s["computed_confidence"] == 0.5
    assert s["n_decisive"] == 0
    assert s["reliable"] is False


# --- supports / contradicts magnitudes (formula is literally +sw / -sw*1.25) ---

def test_single_strong_support_matches_sigmoid():
    s = compute_hypothesis_score(_h(_pred("supports", "strong")))
    assert s["computed_confidence"] == round(_sigmoid(1.0), 3)
    assert s["n_decisive"] == 1
    assert s["reliable"] is False  # one verdict can't validate a hypothesis


def test_contradiction_outweighs_an_equal_support():
    # one strong support (+1.0) and one strong contradiction (-1.25) -> net negative
    s = compute_hypothesis_score(_h(
        _pred("supports", "strong"),
        _pred("contradicts", "strong"),
    ))
    # strong contradiction triggers the falsification cap regardless of the support
    assert s["computed_confidence"] <= 0.15
    assert s["n_decisive"] == 2
    assert s["reliable"] is True


def test_strength_weights_are_ordered():
    strong = compute_hypothesis_score(_h(_pred("supports", "strong")))["computed_confidence"]
    moderate = compute_hypothesis_score(_h(_pred("supports", "moderate")))["computed_confidence"]
    weak = compute_hypothesis_score(_h(_pred("supports", "weak")))["computed_confidence"]
    assert strong > moderate > weak > 0.5


def test_omitted_strength_defaults_to_weak():
    none_given = compute_hypothesis_score(_h(_pred("supports", None)))["computed_confidence"]
    weak = compute_hypothesis_score(_h(_pred("supports", "weak")))["computed_confidence"]
    assert none_given == weak
    # and it is the conservative tier, not the strong one
    assert none_given < compute_hypothesis_score(_h(_pred("supports", "strong")))["computed_confidence"]


# --- the strong-contradiction (falsification) cap ------------------------------

def test_strong_contradiction_caps_at_015():
    s = compute_hypothesis_score(_h(
        _pred("contradicts", "strong"),
        _pred("contradicts", "moderate"),
    ))
    assert s["computed_confidence"] <= 0.15


def test_weak_contradiction_does_not_trip_the_cap():
    # two weak contradictions: below the 0.15 floor only if the cap is NOT applied
    s = compute_hypothesis_score(_h(
        _pred("contradicts", "weak"),
        _pred("contradicts", "weak"),
    ))
    # logit = -2 * (0.3 * 1.25) = -0.75 -> sigmoid ~ 0.32; cap (strong only) must NOT fire
    assert s["computed_confidence"] == round(_sigmoid(-2 * _STRENGTH_W["weak"] * 1.25), 3)
    assert s["computed_confidence"] > 0.15


# --- neutral / insufficient ----------------------------------------------------

def test_neutral_is_mildly_negative():
    s = compute_hypothesis_score(_h(_pred("neutral"), _pred("neutral")))
    assert s["computed_confidence"] == round(_sigmoid(-0.40), 3)
    assert s["computed_confidence"] < 0.5
    assert s["n_decisive"] == 0          # neutral is not decisive
    assert s["breakdown"]["neutral"] == 2


def test_insufficient_does_not_move_belief():
    s = compute_hypothesis_score(_h(_pred("insufficient"), _pred("insufficient")))
    assert s["computed_confidence"] == 0.5
    assert s["n_decisive"] == 0
    assert s["breakdown"]["insufficient"] == 2


# --- the schema gate: blocked is counted but excluded from belief --------------

def test_blocked_is_excluded_from_belief_and_lowers_coverage():
    s = compute_hypothesis_score(_h(
        _pred("supports", "strong"),
        _pred("blocked", "strong"),   # methodologically incomparable
    ))
    # identical confidence to the support alone — blocked moved nothing
    only_support = compute_hypothesis_score(_h(_pred("supports", "strong")))
    assert s["computed_confidence"] == only_support["computed_confidence"]
    assert s["n_blocked"] == 1
    assert s["breakdown"]["blocked"] == 1
    # blocked counts against coverage: 1 of 2 predictions yielded a usable test
    assert s["coverage"] == 0.5


def test_blocked_synonyms_normalize_to_blocked():
    for syn in ("incompatible", "not_comparable", "schema_blocked", "ill_posed"):
        s = compute_hypothesis_score(_h(_pred(syn, "strong")))
        assert s["breakdown"]["blocked"] == 1, syn
        assert s["computed_confidence"] == 0.5, syn  # excluded → still the prior


# --- reliability gate ----------------------------------------------------------

def test_reliable_requires_two_decisive_verdicts():
    one = compute_hypothesis_score(_h(_pred("supports", "strong")))
    two = compute_hypothesis_score(_h(_pred("supports", "weak"), _pred("supports", "weak")))
    assert one["reliable"] is False
    assert two["reliable"] is True


def test_neutral_and_blocked_do_not_count_toward_reliability():
    s = compute_hypothesis_score(_h(
        _pred("neutral"), _pred("blocked", "strong"), _pred("insufficient"),
    ))
    assert s["n_decisive"] == 0
    assert s["reliable"] is False


# --- bookkeeping keys every caller depends on ----------------------------------

def test_return_contract_keys_present():
    s = compute_hypothesis_score(_h(_pred("supports", "strong")))
    for key in ("computed_confidence", "n_decisive", "n_scored", "n_predictions",
                "n_blocked", "coverage", "conflict", "breakdown", "reliable", "note"):
        assert key in s, key
    assert s["n_scored"] == s["n_decisive"]  # back-compat alias must track


# --- Item 1: evidence independence / use-novelty enforced in the math ----------

def test_circular_support_does_not_confirm():
    # a 'supports' whose model was fit to the data it's tested on is circular → 0,
    # not decisive: same score as no evidence at all.
    circ = compute_hypothesis_score(_h(_pred("supports", "strong",
                                              evidence_independence=_CIRCULAR)))
    assert circ["computed_confidence"] == 0.5           # contributed nothing
    assert circ["n_decisive"] == 0
    assert circ["breakdown"]["circular_discounted"] == 1
    assert circ["breakdown"]["supports"] == 1           # still recorded as a support


def test_clean_support_still_confirms():
    # control: identical support WITHOUT circularity moves belief normally
    clean = compute_hypothesis_score(_h(_pred("supports", "strong",
                                              evidence_independence={"model_was_fit": False})))
    assert clean["computed_confidence"] == round(_sigmoid(1.0), 3)
    assert clean["n_decisive"] == 1


def test_correlated_supports_do_not_double_count():
    # two strong supports resting on the SAME record: the second is attenuated to 0.3×
    # and does not add to the independent-decisive count.
    s = compute_hypothesis_score(_h(
        _pred("supports", "strong", evidence_record_ids=["RX"]),
        _pred("supports", "strong", evidence_record_ids=["RX"]),
    ))
    assert s["computed_confidence"] == round(_sigmoid(1.0 + 0.3 * 1.0), 3)
    assert s["n_decisive"] == 1                          # only ONE independent decisive
    assert s["reliable"] is False                        # can't fake reliability with shared data
    assert s["breakdown"]["correlated_attenuated"] == 1


def test_independent_supports_each_count():
    # same two supports but on DIFFERENT records: both full weight, both decisive
    s = compute_hypothesis_score(_h(
        _pred("supports", "strong", evidence_record_ids=["RX"]),
        _pred("supports", "strong", evidence_record_ids=["RY"]),
    ))
    assert s["computed_confidence"] == round(_sigmoid(2.0), 3)
    assert s["n_decisive"] == 2
    assert s["reliable"] is True
    assert s["breakdown"]["correlated_attenuated"] == 0


def test_opposite_directions_on_same_record_are_not_correlated():
    # a support and a contradiction sharing a record are a CONFLICT, not redundancy —
    # both count in full (dedup is within-direction only)
    s = compute_hypothesis_score(_h(
        _pred("supports", "moderate", evidence_record_ids=["RX"]),
        _pred("contradicts", "moderate", evidence_record_ids=["RX"]),
    ))
    assert s["breakdown"]["correlated_attenuated"] == 0
    assert s["n_decisive"] == 2
    assert s["computed_confidence"] == round(_sigmoid(0.6 - 0.6 * 1.25), 3)


def test_coverage_and_conflict_math():
    s = compute_hypothesis_score(_h(
        _pred("supports", "strong"),
        _pred("contradicts", "strong"),
        _pred("blocked", "strong"),
        _pred("supports", "weak", work_status="awaiting_evidence"),  # unevaluated
    ))
    # 3 evaluated of 4 total, but blocked is not a "tested" outcome: tested = 2/4
    assert s["coverage"] == 0.5
    # conflict = min(1,1)/(1+1) = 0.5 (one support vs one contradict)
    assert s["conflict"] == 0.5
