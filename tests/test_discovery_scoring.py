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

from discovery import (compute_hypothesis_score, compute_fragility,  # noqa: E402
                       _derive_reliability_tier, _STRENGTH_W)


def _pred(verdict=None, strength=None, work_status="evaluated",
          evidence_record_ids=None, evidence_independence=None, margin=None,
          cross_system=None, reliability_tier=None):
    return {"verdict": verdict, "strength": strength, "work_status": work_status,
            "evidence_record_ids": evidence_record_ids,
            "evidence_independence": evidence_independence, "margin": margin,
            "cross_system": cross_system, "reliability_tier": reliability_tier}


# evidence_independence that is CIRCULAR (model fit to the data it's tested on)
_CIRCULAR = {"parameters_fit_to": ["R1", "R2"], "tested_against": ["R2", "R3"]}


def _h(*preds, grounding=None):
    return {"predictions": list(preds), "grounding": grounding}


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


def test_circular_support_ad_hoc_is_zeroed():
    # default grounding (unset → ad_hoc): fitted-parameter overlap = accommodation → 0
    s = compute_hypothesis_score(_h(_pred("supports", "strong",
                                          evidence_independence=_CIRCULAR)))
    assert s["computed_confidence"] == 0.5
    assert s["breakdown"]["circular_discounted"] == 1
    assert s["breakdown"]["circular_softened"] == 0


def test_circular_support_standing_prior_is_softened_not_zeroed():
    # a STANDING-PRIOR (literature) hypothesis with the same overlap is a consistency
    # check — kept but capped at weak, NOT zeroed (it was not built to fit this data)
    s = compute_hypothesis_score(_h(_pred("supports", "strong",
                                          evidence_independence=_CIRCULAR),
                                    grounding="standing_prior"))
    assert s["computed_confidence"] == round(_sigmoid(0.3), 3)   # capped at weak
    assert s["breakdown"]["circular_softened"] == 1
    assert s["breakdown"]["circular_discounted"] == 0
    assert s["n_decisive"] == 1


def test_non_circular_support_unaffected_by_grounding():
    # grounding only gates the accommodation discount; a clean support is full either way
    for g in (None, "ad_hoc", "standing_prior"):
        s = compute_hypothesis_score(_h(_pred("supports", "strong"), grounding=g))
        assert s["computed_confidence"] == round(_sigmoid(1.0), 3), g


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


# --- Item 2: per-verdict sharpness (margin) ------------------------------------

def test_margin_none_is_backward_compatible():
    # omitting margin === today's tier-only behaviour
    assert (compute_hypothesis_score(_h(_pred("supports", "moderate")))["computed_confidence"]
            == round(_sigmoid(0.6), 3))


def test_decisive_margin_weighs_more_than_marginal():
    sharp = compute_hypothesis_score(_h(_pred("supports", "moderate", margin=1.0)))
    blunt = compute_hypothesis_score(_h(_pred("supports", "moderate", margin=0.0)))
    # 1.3x vs 0.7x the tier weight
    assert sharp["computed_confidence"] == round(_sigmoid(0.6 * 1.3), 3)
    assert blunt["computed_confidence"] == round(_sigmoid(0.6 * 0.7), 3)
    assert sharp["computed_confidence"] > blunt["computed_confidence"]


def test_marginal_strong_contradiction_does_not_auto_falsify():
    # a STRONG contradiction barely past the threshold (margin<0.5) is strong
    # evidence-against but NOT an automatic kill → the ≤0.15 cap must NOT fire
    s = compute_hypothesis_score(_h(
        _pred("contradicts", "strong", margin=0.1),
        _pred("supports", "weak"),
    ))
    assert s["computed_confidence"] > 0.15
    # contribution: -1.0*1.25*(0.7+0.06) + 0.3 = -0.95 + 0.3
    expected = _sigmoid(-(1.0 * 1.25 * (0.7 + 0.6 * 0.1)) + 0.3)
    assert s["computed_confidence"] == round(expected, 3)


def test_decisive_strong_contradiction_still_falsifies():
    s = compute_hypothesis_score(_h(
        _pred("contradicts", "strong", margin=0.9),
        _pred("supports", "strong"),
    ))
    assert s["computed_confidence"] <= 0.15   # decisive breach → hard cap fires


def test_unqualified_strong_contradiction_keeps_old_cap():
    # no margin → old behaviour: strong contradiction caps
    s = compute_hypothesis_score(_h(_pred("contradicts", "strong"), _pred("contradicts", "moderate")))
    assert s["computed_confidence"] <= 0.15


# --- Cross-system / borrowed-analog evidence (the Cu-Ag lesson) ----------------

def test_cross_system_cannot_make_a_hypothesis_reliable():
    # the Cu-Ag scenario: two strong supports, but BOTH borrowed from another system.
    # They may nudge confidence up, but cannot make the hypothesis 'reliable'.
    s = compute_hypothesis_score(_h(
        _pred("supports", "strong", cross_system=True, evidence_record_ids=["A"]),
        _pred("supports", "strong", cross_system=True, evidence_record_ids=["B"]),
    ))
    assert s["n_decisive"] == 0            # neither counts toward reliability
    assert s["reliable"] is False
    assert s["breakdown"]["cross_system_attenuated"] == 2
    assert s["computed_confidence"] > 0.5  # still suggestive (capped weak each)


def test_cross_system_is_capped_at_weak():
    strong_xsys = compute_hypothesis_score(_h(_pred("supports", "strong", cross_system=True)))
    weak_insys = compute_hypothesis_score(_h(_pred("supports", "weak")))
    # a 'strong' cross-system support contributes no more than a weak in-system one
    assert strong_xsys["computed_confidence"] == weak_insys["computed_confidence"]


def test_cross_system_contradiction_never_falsifies():
    # a strong cross-system contradiction must NOT trip the ≤0.15 falsification cap
    s = compute_hypothesis_score(_h(
        _pred("contradicts", "strong", cross_system=True),
        _pred("supports", "weak"),
    ))
    assert s["computed_confidence"] > 0.15


def test_in_system_still_establishes_normally():
    # control: the same two strong supports, in-system → reliable as before
    s = compute_hypothesis_score(_h(
        _pred("supports", "strong", evidence_record_ids=["A"]),
        _pred("supports", "strong", evidence_record_ids=["B"]),
    ))
    assert s["n_decisive"] == 2 and s["reliable"] is True
    assert s["breakdown"]["cross_system_attenuated"] == 0


# --- Symmetric use-novelty: a novel (ad_hoc) outlier earns full out-of-sample credit -

def test_adhoc_out_of_sample_gets_full_credit():
    # a NOVEL (ad_hoc) hypothesis tested on data it did NOT fit (no parameter overlap)
    # earns full, reliability-bearing credit — use-novelty must not penalise newness.
    oos1 = {"model_was_fit": True, "parameters_fit_to": ["A"], "tested_against": ["B"]}
    oos2 = {"model_was_fit": True, "parameters_fit_to": ["A"], "tested_against": ["C"]}
    s = compute_hypothesis_score(_h(
        _pred("supports", "strong", evidence_independence=oos1),
        _pred("supports", "strong", evidence_independence=oos2),
        grounding="ad_hoc"))
    assert s["reliable"] is True and s["n_decisive"] == 2
    assert s["breakdown"]["circular_discounted"] == 0   # out-of-sample → no discount


# --- Fragility / contingency (leave-one-out) -----------------------------------

def test_fragility_flags_load_bearing_keystone():
    # reliable on exactly 2 independent supports — pull either and it drops to 1
    # decisive (unreliable). That's fragile, and the keystone is named.
    f = compute_fragility(_h(
        _pred("supports", "strong", evidence_record_ids=["A"]),
        _pred("supports", "strong", evidence_record_ids=["B"]),
    ))
    assert f["reliable"] is True
    assert f["fragile"] is True
    assert f["keystone"] is not None
    assert f["keystone"]["reliable_if_removed"] is False


def test_fragility_robust_when_overdetermined():
    # 3 independent supports — removing one still leaves 2 decisive; not fragile
    f = compute_fragility(_h(
        _pred("supports", "strong", evidence_record_ids=["A"]),
        _pred("supports", "strong", evidence_record_ids=["B"]),
        _pred("supports", "strong", evidence_record_ids=["C"]),
    ))
    assert f["reliable"] is True
    assert f["fragile"] is False


def test_fragility_flags_a_borrowed_keystone():
    # a hypothesis whose mover is a cross-system/borrowed leg is fragile by construction
    f = compute_fragility(_h(_pred("supports", "strong", cross_system=True,
                                   evidence_record_ids=["A"])))
    assert f["keystone"]["cross_system"] is True
    assert f["fragile"] is True


# --- Reliability axis (opt-in trust tier) --------------------------------------

def test_reliability_omitted_is_a_noop():
    # the whole point of "blend like fabric": an undeclared verdict scores as before
    plain = compute_hypothesis_score(_h(_pred("supports", "strong"),
                                        _pred("supports", "strong")))
    explicit_none = compute_hypothesis_score(_h(
        _pred("supports", "strong", reliability_tier=None),
        _pred("supports", "strong", reliability_tier=None)))
    assert plain["computed_confidence"] == explicit_none["computed_confidence"]
    assert plain["reliable"] == explicit_none["reliable"] is True


def test_low_reliability_moves_belief_but_not_reliability():
    # two 'anecdotal' supports: belief nudges up, but the hypothesis cannot be reliable
    s = compute_hypothesis_score(_h(
        _pred("supports", "strong", evidence_record_ids=["A"], reliability_tier="anecdotal"),
        _pred("supports", "strong", evidence_record_ids=["B"], reliability_tier="anecdotal"),
    ))
    assert s["n_decisive"] == 0 and s["reliable"] is False
    assert s["breakdown"]["low_reliability_excluded"] == 2
    assert s["computed_confidence"] > 0.5     # still moved belief, weakly


def test_established_outweighs_single_source():
    est = compute_hypothesis_score(_h(_pred("supports", "strong", reliability_tier="established")))
    ss = compute_hypothesis_score(_h(_pred("supports", "strong", reliability_tier="single_source")))
    assert est["computed_confidence"] > ss["computed_confidence"]


def test_reliability_tier_is_server_derived_not_self_asserted():
    # anti-laundering: 'corroborated' requires reproduced_by INDEPENDENT of own evidence.
    # claiming reproduced_by == own evidence is self-citation → collapses to single_source.
    laundered = _derive_reliability_tier(
        {"tier": "established",
         "basis": {"reproduced_by": ["A", "B"]}},  # but these ARE the verdict's own records
        own_evidence_ids=["A", "B"])
    assert laundered == "single_source"          # not 'established'
    # genuine independent corroboration earns it
    earned = _derive_reliability_tier(
        {"basis": {"reproduced_by": ["C", "D"]}}, own_evidence_ids=["A"])
    assert earned == "established"
    # a non-portable model can't rise above anecdotal
    assert _derive_reliability_tier(
        {"basis": {"source_class": "modeled_nonportable"}}, own_evidence_ids=[]) == "anecdotal"


def test_score_is_invariant_to_consensus_fields():
    # THE load-bearing guard: consensus/surprise must NEVER touch belief. The scorer
    # ignores unknown keys, so injecting consensus_relation/strength changes nothing.
    base = _h(_pred("supports", "strong"), _pred("contradicts", "moderate"))
    ref = compute_hypothesis_score(base)
    for rel in ("concordant", "silent", "discordant"):
        for strn in ("none", "weak", "strong"):
            poisoned = {"predictions": [
                {**p, "consensus_relation": rel, "consensus_strength": strn,
                 "consensus_basis": ["rec_FAKE"]} for p in base["predictions"]]}
            assert compute_hypothesis_score(poisoned) == ref, f"consensus leaked via {rel}/{strn}"


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
