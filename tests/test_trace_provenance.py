"""Trace provenance — WHO reasoned and WHY. Pure logic, no DB."""
import pytest
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "portal"))
import trace_provenance as tp


class TestActorModel:
    def test_none_for_junk(self):
        for bad in (None, "claude", 42, [], {}):
            assert tp.normalize_actor_model(bad) is None

    def test_keeps_portable_fields(self):
        got = tp.normalize_actor_model(
            {"provider": "anthropic", "model_id": "claude-opus-5",
             "model_version": "20260501", "harness": "claude-code"})
        assert got["provider"] == "anthropic"
        assert got["model_id"] == "claude-opus-5"
        assert got["model_version"] == "20260501"

    def test_client_can_never_self_promote_trust(self):
        """The whole point of identity_trust: a caller cannot vouch for itself."""
        got = tp.normalize_actor_model(
            {"model_id": "x", "identity_trust": "gateway_stamped"})
        assert got["identity_trust"] == "client_attested"

    def test_unknown_keys_are_dropped_not_stored(self):
        got = tp.normalize_actor_model({"model_id": "x", "api_key": "sk-secret"})
        assert "api_key" not in got

    def test_long_strings_are_bounded(self):
        got = tp.normalize_actor_model({"model_id": "z" * 9000})
        assert len(got["model_id"]) <= 2000


class TestDecision:
    def test_none_for_junk(self):
        for bad in (None, "chose x", 7, []):
            assert tp.normalize_decision(bad) is None

    def test_full_shape(self):
        got = tp.normalize_decision(
            {"chose": "run DFT", "rejected": ["MLIP only"],
             "because": ["need decisive number"], "blocked_on": ["queue"]})
        assert got["chose"] == "run DFT"
        assert got["rejected"] == ["MLIP only"]
        assert got["because"] == ["need decisive number"]
        assert got["blocked_on"] == ["queue"]

    def test_bare_string_becomes_one_item_list(self):
        got = tp.normalize_decision({"chose": "a", "rejected": "b"})
        assert got["rejected"] == ["b"]

    def test_empty_items_dropped(self):
        got = tp.normalize_decision({"chose": "a", "because": ["", "   ", "real"]})
        assert got["because"] == ["real"]

    def test_lists_are_bounded(self):
        got = tp.normalize_decision({"chose": "a", "because": ["x"] * 500})
        assert len(got["because"]) <= 40


class TestCompleteness:
    def test_chose_alone_is_thin(self):
        assert tp.is_complete_decision({"chose": "a"}) is False

    def test_chose_plus_because_is_complete(self):
        assert tp.is_complete_decision({"chose": "a", "because": ["b"]}) is True

    def test_chose_plus_rejected_is_complete(self):
        assert tp.is_complete_decision({"chose": "a", "rejected": ["b"]}) is True

    def test_blocked_alone_is_not_a_decision(self):
        assert tp.is_complete_decision({"blocked_on": ["queue"]}) is False


class TestTraceGaps:
    def test_flags_unattributed_belief_changing_only(self):
        gaps = tp.trace_gaps([
            {"id": 1, "event_type": "prediction_evaluated"},
            {"id": 2, "event_type": "agent_message"},
        ])
        assert gaps["unattributed_belief_changing"] == [1]

    def test_attributed_event_is_not_flagged_and_is_counted(self):
        gaps = tp.trace_gaps([
            {"id": 1, "event_type": "prediction_evaluated",
             "actor_model": {"model_id": "claude-opus-5"}},
        ])
        assert gaps["unattributed_belief_changing"] == []
        # keyed provider/model_id; provider unknown renders as "?"
        assert gaps["models_seen"] == {"?/claude-opus-5": 1}

    def test_thin_reasoning_step_is_flagged(self):
        gaps = tp.trace_gaps([
            {"id": 3, "event_type": "reasoning_step", "decision": {"chose": "a"}},
        ])
        assert gaps["reasoning_steps_with_incomplete_decision"] == [3]

    def test_multi_model_trace_detected(self):
        gaps = tp.trace_gaps([
            {"id": 1, "event_type": "prediction_evaluated",
             "actor_model": {"model_id": "a"}},
            {"id": 2, "event_type": "prediction_evaluated",
             "actor_model": {"model_id": "b"}},
        ])
        assert gaps["single_model_trace"] is False

    def test_empty_is_safe(self):
        assert tp.trace_gaps([])["unattributed_belief_changing"] == []
        assert tp.trace_gaps(None)["models_seen"] == {}


class TestReviewFixes:
    """Regressions for the six defects found in adversarial review."""

    def test_nul_byte_is_stripped_before_it_can_reach_jsonb(self):
        """A NUL in a client string makes PostgreSQL reject the whole jsonb value,
        which would fail the INSERT and LOSE the event."""
        got = tp.normalize_decision({"chose": "a\x00b", "because": ["c\x00d"]})
        assert "\x00" not in got["chose"]
        assert "\x00" not in got["because"][0]

    def test_control_chars_stripped_but_whitespace_kept(self):
        got = tp.normalize_decision({"chose": "line1\nline2\ttabbed\x07bell"})
        assert "\n" in got["chose"] and "\t" in got["chose"]
        assert "\x07" not in got["chose"]

    def test_models_keyed_by_provider_so_vendors_do_not_collide(self):
        gaps = tp.trace_gaps([
            {"id": 1, "event_type": "prediction_evaluated",
             "actor_model": {"provider": "xai", "model_id": "m"}},
            {"id": 2, "event_type": "prediction_evaluated",
             "actor_model": {"provider": "openai", "model_id": "m"}},
        ])
        assert len(gaps["models_seen"]) == 2

    def test_no_models_is_unknown_not_single(self):
        """'one model' and 'no idea which model' must not look alike."""
        assert tp.trace_gaps([{"id": 1, "event_type": "agent_message"}])["single_model_trace"] is None

    def test_one_model_is_single(self):
        gaps = tp.trace_gaps([{"id": 1, "event_type": "prediction_evaluated",
                               "actor_model": {"model_id": "a"}}])
        assert gaps["single_model_trace"] is True

    def test_legacy_flood_is_capped_but_counted(self):
        """23 live projects predate this feature; an exhaustive dump would be noise."""
        gaps = tp.trace_gaps([{"id": i, "event_type": "prediction_evaluated"}
                              for i in range(1000)])
        assert len(gaps["unattributed_belief_changing"]) == 20
        assert gaps["unattributed_belief_changing_count"] == 1000


class TestPolicyEnforcement:
    """Policy-versioned enforcement: hold NEW projects to the contract without
    retro-enforcing it on the legacy demos."""

    def test_legacy_project_is_never_retro_enforced(self):
        for et in ("prediction_evaluated", "reasoning_step", "hypothesis_created"):
            assert tp.enforcement_error(None, et, None, None) is None

    def test_older_policy_is_not_enforced(self):
        assert tp.enforcement_error(59, "prediction_evaluated", None, None) is None

    def test_unsigned_belief_changing_write_is_rejected(self):
        err = tp.enforcement_error(60, "prediction_evaluated", None, None)
        assert err and "actor_model" in err

    def test_signed_belief_changing_write_passes(self):
        assert tp.enforcement_error(
            60, "prediction_evaluated", {"model_id": "m"}, None) is None

    def test_reasoning_step_without_decision_is_rejected(self):
        err = tp.enforcement_error(60, "reasoning_step", {"model_id": "m"}, None)
        assert err and "decision" in err

    def test_thin_decision_is_accepted_and_only_flagged(self):
        """A hard gate on completeness would push agents to write nothing
        rather than something partial."""
        assert tp.enforcement_error(
            60, "reasoning_step", {"model_id": "m"}, {"chose": "a"}) is None

    def test_non_belief_changing_events_are_unaffected(self):
        for et in ("agent_message", "compute_submitted", "resume_check"):
            assert tp.enforcement_error(60, et, None, None) is None

    def test_empty_actor_model_object_does_not_satisfy_the_gate(self):
        err = tp.enforcement_error(60, "prediction_evaluated", {}, None)
        assert err is not None

    def test_garbage_policy_version_degrades_to_advisory(self):
        assert tp.enforcement_error("junk", "prediction_evaluated", None, None) is None


class TestServerActor:
    def test_server_actor_is_marked_client_attested_like_everything_else(self):
        """The portal does not get to claim a stronger trust tier than an agent."""
        assert tp.SERVER_ACTOR["identity_trust"] == "client_attested"

    def test_server_actor_normalizes(self):
        got = tp.normalize_actor_model(tp.SERVER_ACTOR)
        assert got["model_id"] == "portal" and got["provider"] == "isaac"

    def test_server_actor_satisfies_the_gate(self):
        assert tp.enforcement_error(
            60, "status_changed", tp.SERVER_ACTOR, None) is None


class TestServerEmittedEvents:
    """An agent wrote nine `prediction_evaluated` events describing verdicts it had reasoned
    out, never called PUT /predictions/{id}, and left every prediction at verdict=None with
    all four hypotheses on the 0.5 prior. The journal read as a finished analysis while the
    state had not moved at all. Four sibling runs on the identical frozen set were fine, so
    the contract left the trap open rather than the model being broken."""

    def test_state_changing_types_are_refused(self):
        for t in ("prediction_evaluated", "hypothesis_created", "prediction_added",
                  "next_experiment_proposed"):
            assert tp.server_emitted_error(t), t

    def test_the_rejection_names_the_right_endpoint(self):
        msg = tp.server_emitted_error("prediction_evaluated")
        assert "PUT /predictions/{prediction_id}" in msg
        # It must also say WHY, so the 400 teaches instead of merely blocking.
        assert "verdict=null" in msg and "prior" in msg

    def test_narrative_types_stay_open(self):
        """reasoning_step is how an agent records the thinking. Closing it would push agents
        to write nothing, which is the failure this platform exists to prevent."""
        for t in ("reasoning_step", "human_directive", "compute_submitted", "status_changed"):
            assert tp.server_emitted_error(t) is None, t

    def test_refusal_is_independent_of_policy_version(self):
        """API misuse, not a scientific-contract rule, so legacy projects are refused too."""
        assert tp.server_emitted_error("prediction_evaluated") is not None
        assert tp.enforcement_error(None, "reasoning_step", None, {"chose": "x"}) is None


class TestMisattribution:
    """Found live in replication round 1: 89 of 96 events on a completed run were signed
    `isaac/portal`, including every hypothesis and every verdict, because the dedicated
    endpoints never accepted an actor_model to pass down. `unattributed_*` read 0 the whole
    time, because a portal signature IS a signature. A compliance surface that reports
    perfect while the trace cannot name the model is worse than one reporting a gap."""

    def test_hypothesis_signed_by_portal_is_counted_as_misattributed(self):
        g = tp.trace_gaps([{"id": 1, "event_type": "hypothesis_created",
                            "actor_model": tp.SERVER_ACTOR}])
        assert g["agent_actions_signed_by_portal_count"] == 1
        assert g["agent_actions_signed_by_portal"] == [1]

    def test_misattribution_is_invisible_to_the_unattributed_counter(self):
        """The exact blind spot: populated field, wrong actor, old metric reads clean."""
        g = tp.trace_gaps([{"id": 1, "event_type": "prediction_evaluated",
                            "actor_model": tp.SERVER_ACTOR}])
        assert g["unattributed_belief_changing_count"] == 0
        assert g["agent_actions_signed_by_portal_count"] == 1

    def test_real_model_signature_is_not_misattributed(self):
        g = tp.trace_gaps([{"id": 1, "event_type": "hypothesis_created",
                            "actor_model": {"provider": "xai", "model_id": "grok-4.5"}}])
        assert g["agent_actions_signed_by_portal_count"] == 0

    def test_genuinely_server_side_events_are_not_flagged(self):
        """project_created and status_changed really are the portal's, so signing them
        with SERVER_ACTOR is honest and must stay silent."""
        g = tp.trace_gaps([{"id": 1, "event_type": "project_created",
                            "actor_model": tp.SERVER_ACTOR},
                           {"id": 2, "event_type": "status_changed",
                            "actor_model": tp.SERVER_ACTOR}])
        assert g["agent_actions_signed_by_portal_count"] == 0

    def test_unsigned_agent_action_is_unattributed_not_misattributed(self):
        g = tp.trace_gaps([{"id": 1, "event_type": "hypothesis_created"}])
        assert g["unattributed_belief_changing_count"] == 1
        assert g["agent_actions_signed_by_portal_count"] == 0

    def test_sample_is_capped_but_count_is_not(self):
        evs = [{"id": i, "event_type": "prediction_added",
                "actor_model": tp.SERVER_ACTOR} for i in range(50)]
        g = tp.trace_gaps(evs, sample_cap=5)
        assert len(g["agent_actions_signed_by_portal"]) == 5
        assert g["agent_actions_signed_by_portal_count"] == 50


class TestStrongWithoutRivalContrast:
    """The strength-is-discrimination rule's machine-checkable core. Measured motivation:
    across a 30-run frozen benchmark, the only strength-unanimous item was the single one
    whose observation uniquely killed a rival; everywhere else five models split between
    reading strength as discrimination (the written rule) and as effect size (the everyday
    meaning). Advisory only, never a gate."""

    def _hyps(self, strength="strong", verdict="supports", disc=None, own="H1"):
        import discovery
        return [{"label": own, "predictions": [{
            "label": "P1", "verdict": verdict, "strength": strength,
            "descriptor_name": "x", "discriminates": disc}]}], discovery

    def test_strong_naming_only_own_hypothesis_is_flagged(self):
        hyps, d = self._hyps(disc=[{"hypothesis_label": "H1", "expected": "up"}])
        assert len(d._strong_without_rival_contrast(hyps)) == 1

    def test_strong_with_empty_discriminates_is_flagged(self):
        hyps, d = self._hyps(disc=None)
        assert len(d._strong_without_rival_contrast(hyps)) == 1

    def test_strong_naming_a_rival_is_clean(self):
        hyps, d = self._hyps(disc=[{"hypothesis_label": "H2", "expected": "down"}])
        assert d._strong_without_rival_contrast(hyps) == []

    def test_moderate_and_weak_are_never_flagged(self):
        for tier in ("moderate", "weak", None):
            hyps, d = self._hyps(strength=tier, disc=None)
            assert d._strong_without_rival_contrast(hyps) == []

    def test_non_decisive_verdicts_are_never_flagged(self):
        for v in ("neutral", "insufficient", "blocked", None):
            hyps, d = self._hyps(verdict=v, disc=None)
            assert d._strong_without_rival_contrast(hyps) == []


class TestDerivedStrength:
    """Policy-61: the scoring tier is a pure function of rival-contrast + margin. Each rung
    of the ladder to here was measured first: prose moved direction not variance; structure
    raised agreement 0.54->0.67 but left 0.13-0.21 confidence spread; the tier was the last
    authored adjective in the scoring path."""

    def _p(self, disc=None, margin=None):
        import discovery
        return discovery, {"discriminates": disc, "margin": margin}

    def test_no_discriminates_is_weak_regardless_of_authored_claim(self):
        d, p = self._p(None)
        assert d._derived_strength(p, "H1") == "weak"

    def test_own_hypothesis_only_is_weak(self):
        d, p = self._p([{"hypothesis_label": "H1", "expected": "up"}])
        assert d._derived_strength(p, "H1") == "weak"

    def test_rival_contrast_defaults_strong(self):
        d, p = self._p([{"hypothesis_label": "H3", "expected": "flat"}])
        assert d._derived_strength(p, "H1") == "strong"

    def test_rival_contrast_with_soft_margin_is_moderate(self):
        d, p = self._p([{"hypothesis_label": "H3", "expected": "flat"}], margin=0.4)
        assert d._derived_strength(p, "H1") == "moderate"
        d, p = self._p([{"hypothesis_label": "H3", "expected": "flat"}], margin=0.5)
        assert d._derived_strength(p, "H1") == "strong"

    def test_jsonb_string_form_is_parsed(self):
        d, p = self._p('[{"hypothesis_label": "H2", "expected": "down"}]')
        assert d._derived_strength(p, "H1") == "strong"

    def test_rival_entry_without_expected_does_not_count(self):
        d, p = self._p([{"hypothesis_label": "H2"}])
        assert d._derived_strength(p, "H1") == "weak"

    def test_scoring_uses_derived_only_at_policy_61(self):
        import discovery
        pred = {"work_status": "evaluated", "verdict": "supports", "strength": "strong",
                "descriptor_name": "x", "evidence_record_ids": ["r1"],
                "falsification_criterion": "f", "direction": "up",
                "reference_condition": "c", "rationale": "because", "discriminates": None}
        legacy = discovery.compute_hypothesis_score(
            {"predictions": [dict(pred)], "label": "H1", "policy_version": 60})
        derived = discovery.compute_hypothesis_score(
            {"predictions": [dict(pred)], "label": "H1", "policy_version": 61})
        # same authored 'strong', no rival contrast: legacy scores it strong, 61 scores weak
        assert derived["computed_confidence"] < legacy["computed_confidence"]

    def test_policy_60_trace_gates_still_bind_after_current_moved_to_61(self):
        """The trap the pre-registration called out: raising CURRENT must not demote
        policy-60 projects to legacy for the policy-60 attribution gates."""
        # Intent, not a frozen constant: however far CURRENT advances, the policy-60
        # gates must keep binding for policy-60-and-later projects.
        assert tp.CURRENT_POLICY_VERSION >= 61
        for pv in (60, 61, tp.CURRENT_POLICY_VERSION):
            err = tp.enforcement_error(pv, "hypothesis_created", None, None)
            assert err is not None and "actor_model" in err, pv


class TestDerivedMargin:
    """Policy-62: margin from structured threshold + observed + scale. Ordered by the 0.67
    arm, where all remaining confidence variance was authored-margin variance and one 0.4
    margin toggled the kill-cap."""

    def _m(self, th, ob):
        import discovery
        return discovery._derived_margin({"threshold": th, "observed": ob})

    def test_three_sigma_is_fully_decisive(self):
        assert self._m({"comparator": "gte", "value": 0.1, "unit": "fraction"},
                       {"value": 0.4, "unit": "fraction", "scale": 0.1}) == 1.0

    def test_at_the_line_is_zero(self):
        assert self._m({"value": 0.2, "unit": "x"}, {"value": 0.2, "unit": "x", "scale": 0.05}) == 0.0

    def test_partial_divergence_scales_linearly(self):
        m = self._m({"value": 0.0, "unit": "x"}, {"value": 0.15, "unit": "x", "scale": 0.1})
        assert abs(m - 0.5) < 1e-9

    def test_unit_mismatch_refuses(self):
        assert self._m({"value": 1, "unit": "mA"}, {"value": 2, "unit": "A", "scale": 0.1}) is None

    def test_missing_or_bad_scale_refuses(self):
        assert self._m({"value": 1, "unit": "x"}, {"value": 2, "unit": "x", "scale": 0}) is None
        assert self._m({"value": 1, "unit": "x"}, {"value": 2, "unit": "x"}) is None
        assert self._m(None, {"value": 2, "unit": "x", "scale": 1}) is None

    def test_scoring_uses_derived_margin_only_at_policy_62(self):
        import discovery
        pred = {"work_status": "evaluated", "verdict": "supports", "strength": "strong",
                "descriptor_name": "x", "evidence_record_ids": ["r1"],
                "falsification_criterion": "f", "direction": "up",
                "reference_condition": "c", "rationale": "because",
                "discriminates": [{"hypothesis_label": "H9", "expected": "down"}],
                "margin": 1.0,   # authored claim: fully decisive
                "threshold": {"value": 0.0, "unit": "x"},
                "observed": {"value": 0.03, "unit": "x", "scale": 0.1}}  # derived: 0.1
        p61 = discovery.compute_hypothesis_score(
            {"predictions": [dict(pred)], "label": "H1", "policy_version": 61})
        p62 = discovery.compute_hypothesis_score(
            {"predictions": [dict(pred)], "label": "H1", "policy_version": 62})
        # same inputs: 61 trusts the authored 1.0, 62 derives 0.1 -> smaller contribution
        assert p62["computed_confidence"] < p61["computed_confidence"]


class TestDecisiveWithoutObserved:
    """0.69 surfacing: adoption variance was the largest resolvable component of the 0.68
    arm's residual spread — one model declared observed on 0/6 under an identical prompt."""

    def _h(self, threshold=None, observed=None, verdict="supports"):
        import discovery
        return discovery, [{"label": "H1", "predictions": [{
            "label": "P1", "verdict": verdict, "descriptor_name": "x",
            "threshold": threshold, "observed": observed}]}]

    def test_threshold_without_observed_is_flagged(self):
        d, h = self._h(threshold={"value": 1, "unit": "x"})
        assert len(d._decisive_without_observed(h)) == 1

    def test_observed_present_is_clean(self):
        d, h = self._h(threshold={"value": 1, "unit": "x"},
                       observed={"value": 2, "unit": "x", "scale": 0.1})
        assert d._decisive_without_observed(h) == []

    def test_no_threshold_is_out_of_scope(self):
        d, h = self._h(threshold=None)
        assert d._decisive_without_observed(h) == []

    def test_non_decisive_is_out_of_scope(self):
        d, h = self._h(threshold={"value": 1, "unit": "x"}, verdict="insufficient")
        assert d._decisive_without_observed(h) == []


import discovery  # noqa: E402
import trace_provenance  # noqa: E402


class TestObservedScale:
    """Policy 63: the scale the margin divides by must be the evidence's own.

    The four scales below are the ACTUAL declarations from the case-2b arm, where four
    agents recorded the identical observation on the identical records with the identical
    verdict and split 0.150 against 0.709 on the hypothesis purely through this field.
    """

    THRESHOLD = {"comparator": "lte", "value": 0.057, "unit": "fraction_FE_delta"}

    def _obs(self, scale):
        return {"value": 0.00969, "unit": "fraction_FE_delta", "scale": scale,
                "scale_basis": "case-2b declaration"}

    def test_threshold_offered_as_scale_is_refused(self, monkeypatch):
        monkeypatch.setattr(discovery, "_descriptor_sigmas", lambda *a, **k: [])
        why = discovery._check_observed_scale(self._obs(0.057), self.THRESHOLD, "d", ["R1"])
        assert why and "decision line is not a noise scale" in why

    def test_scale_far_from_declared_uncertainty_is_refused(self, monkeypatch):
        monkeypatch.setattr(discovery, "_descriptor_sigmas", lambda *a, **k: [0.02, 0.02])
        for scale in (0.005, 0.12):          # >2x either side of sqrt(2)*0.02 = 0.0283
            why = discovery._check_observed_scale(self._obs(scale), self.THRESHOLD, "d",
                                                  ["R1", "R2"])
            assert why and "factor of two" in why

    def test_the_band_deliberately_tolerates_the_case2b_low_outlier(self, monkeypatch):
        """Seat D declared 0.015 against a derivable 0.0283. That is inside the 2x band and
        is NOT refused, on purpose: it lands on the same side of the margin cap as the two
        correct derivations, so refusing it would buy no agreement and would start policing
        judgement calls the evidence cannot adjudicate. Rule 1 (threshold-as-scale) is what
        catches the declaration that actually moved the answer."""
        monkeypatch.setattr(discovery, "_descriptor_sigmas", lambda *a, **k: [0.02, 0.02])
        assert discovery._check_observed_scale(self._obs(0.015), self.THRESHOLD, "d",
                                               ["R1", "R2"]) is None

    def test_correctly_derived_scale_passes(self, monkeypatch):
        monkeypatch.setattr(discovery, "_descriptor_sigmas", lambda *a, **k: [0.02, 0.02])
        for scale in (0.0283, 0.02828, 0.02, 0.04):
            assert discovery._check_observed_scale(self._obs(scale), self.THRESHOLD, "d",
                                                   ["R1", "R2"]) is None

    def test_silent_evidence_leaves_the_agent_alone(self, monkeypatch):
        """Where nothing is declared, an unusual scale is the agent's call (0.69 behaviour):
        absent is not zero, and refusing here would block honest work on digitized corpora."""
        monkeypatch.setattr(discovery, "_descriptor_sigmas", lambda *a, **k: [])
        assert discovery._check_observed_scale(self._obs(0.015), self.THRESHOLD, "d",
                                               ["R1"]) is None

    def test_no_observed_and_no_scale_are_not_errors(self, monkeypatch):
        monkeypatch.setattr(discovery, "_descriptor_sigmas", lambda *a, **k: [0.02])
        assert discovery._check_observed_scale(None, self.THRESHOLD, "d", ["R1"]) is None
        assert discovery._check_observed_scale({"value": 1.0}, self.THRESHOLD, "d",
                                               ["R1"]) is None

    def test_declared_scale_uses_two_sample_rule(self, monkeypatch):
        monkeypatch.setattr(discovery, "_descriptor_sigmas", lambda *a, **k: [0.02, 0.02])
        dec = discovery._declared_scale("d", ["R1", "R2"])
        assert abs(dec["value"] - 0.02 * 2 ** 0.5) < 1e-9
        monkeypatch.setattr(discovery, "_descriptor_sigmas", lambda *a, **k: [0.02])
        assert abs(discovery._declared_scale("d", ["R1"])["value"] - 0.02) < 1e-9

    def test_loosest_declaration_binds(self, monkeypatch):
        """Conservative by choice: with mixed declarations the largest sigma sets the scale,
        so the platform never sharpens a verdict the evidence cannot support."""
        monkeypatch.setattr(discovery, "_descriptor_sigmas", lambda *a, **k: [0.01, 0.05])
        assert abs(discovery._declared_scale("d", ["R1", "R2"])["value"]
                   - 0.05 * 2 ** 0.5) < 1e-9

    def test_the_gate_binds_only_at_63_and_above(self):
        """The trap this repo has fallen into once: a `pv < CURRENT` legacy test silently
        switches OFF older gates the day CURRENT moves. Each gate binds at its own minimum."""
        assert trace_provenance.POLICY_OBSERVED_SCALE == 63
        assert trace_provenance.CURRENT_POLICY_VERSION >= 63
        for pv in (60, 61, 62, trace_provenance.CURRENT_POLICY_VERSION):
            assert pv >= trace_provenance.POLICY_TRACE_GATES
            assert (pv >= trace_provenance.POLICY_OBSERVED_SCALE) == (pv >= 63)


class TestManifestAdvertisesItsOwnPolicy:
    """The manifest's advertised policy_version must BE the enforced one.

    It drifted once: 0.70 raised CURRENT_POLICY_VERSION to 63 while the manifest still
    carried a hand-typed 62. An agent reading the contract would have been told the wrong
    version of the contract it is held to, and a benchmark arm pinning that string would
    have recorded a version that did not describe its own enforcement. Caught by adversarial
    review, not by a test, which is why this test exists.
    """

    def test_advertised_equals_enforced(self):
        m = discovery.get_manifest()
        assert m["policy_version"] == trace_provenance.CURRENT_POLICY_VERSION

    def test_version_string_and_policy_move_together(self):
        """The human-readable version and the enforced policy both name the same contract."""
        m = discovery.get_manifest()
        assert isinstance(m["version"], str) and m["version"]
        assert isinstance(m["policy_version"], int)
        assert m["policy_version"] >= trace_provenance.POLICY_TRACE_GATES


class TestContractRefusalIsActionable:
    """A contract refusal must reach the agent as a 400 with the reason, never a 500.

    Found by a live production smoke test, not by these tests: the 0.70 scale gate raised
    TraceContractError from /evaluate, which had no handler, so the refusal arrived as
    `500 Internal Server Error` with an HTML body. An agent cannot learn from that, and the
    predictable response to an unexplained 500 is to drop the field that caused it, which is
    exactly falsifier F5 of that rung's own pre-registration. The handler is now app-wide, so
    a refusal raised from any future endpoint is covered without anyone remembering to wrap it.
    """

    def test_app_registers_a_handler_for_contract_errors(self):
        import api
        handlers = api.app.error_handler_spec[None][None]
        assert any(issubclass(k, discovery.TraceContractError)
                   for k in handlers) or discovery.TraceContractError in handlers, \
            "TraceContractError must have an app-wide error handler"

    def test_handler_returns_400_and_the_reason(self):
        import api
        with api.app.test_request_context():
            body, status = api._trace_contract_error(
                discovery.TraceContractError("the scale is not the evidence's"))
            assert status == 400
            payload = body.get_json()
            assert payload["error"] == "the scale is not the evidence's"
            assert payload["policy_version"] == trace_provenance.CURRENT_POLICY_VERSION

    def test_every_raise_site_is_covered_by_the_app_wide_handler(self):
        """The per-route approach was already incomplete: three raise sites, one route
        catching them. Assert the count relationship rather than the routes."""
        import pathlib
        src = pathlib.Path(__file__).parent.parent / "portal" / "discovery.py"
        n_raises = src.read_text().count("raise TraceContractError")
        assert n_raises >= 3
        import api
        assert discovery.TraceContractError in api.app.error_handler_spec[None][None]


class TestPolicyPinningAtCreation:
    """A project may be pinned BACKWARD to an older contract, never forward.

    Reproducibility motive: a project is held for life to the contract it was born under, so
    a contract arm could previously only run while that contract was deployed. That forced
    every treatment arm to run at a different TIME from its baseline, and one such comparison
    manufactured 43% of an improvement out of a seat-count difference (CORRECTIONS #13).
    Pinning lets baseline and treatment run interleaved on one deployment with one seat set.
    """

    def test_default_is_current(self):
        assert (discovery._resolve_policy_version(None)
                == trace_provenance.CURRENT_POLICY_VERSION)

    def test_pins_backward_to_any_supported_gate(self):
        for pv in range(trace_provenance.POLICY_TRACE_GATES,
                        trace_provenance.CURRENT_POLICY_VERSION + 1):
            assert discovery._resolve_policy_version(pv) == pv

    def test_refuses_forward(self):
        with pytest.raises(discovery.TraceContractError) as e:
            discovery._resolve_policy_version(trace_provenance.CURRENT_POLICY_VERSION + 1)
        assert "ahead of this server" in str(e.value)

    def test_refuses_below_the_gate_floor(self):
        """Below POLICY_TRACE_GATES nothing binds, so this would create an UNENFORCED project
        while looking like a pinned one. That is worse than refusing."""
        with pytest.raises(discovery.TraceContractError) as e:
            discovery._resolve_policy_version(trace_provenance.POLICY_TRACE_GATES - 1)
        assert "no gate binds" in str(e.value)

    def test_refuses_garbage_rather_than_defaulting(self):
        """A malformed value must not silently become CURRENT: a benchmark arm would then
        record a pin it never had."""
        for bad in ("sixty", None.__class__, [63]):
            if bad is None:
                continue
            with pytest.raises(discovery.TraceContractError):
                discovery._resolve_policy_version(bad)

    def test_pinning_is_a_contract_error_so_it_surfaces_as_400(self):
        import api
        assert discovery.TraceContractError in api.app.error_handler_spec[None][None]


class TestVerdictBasis:
    """Policy 64: a decisive verdict declares what it RESTS ON, and the claim is checked.

    The scientific point, not the bookkeeping one: a conclusion built from cited records is
    something a CONTRACT can constrain and improve; a conclusion built from what the model
    already believed is not. Across 990 benchmark verdicts the two wrote an identical shape of
    record, so the distinction that decides what the manifest can even address was invisible.
    """

    def test_missing_basis_is_refused_with_the_reason(self):
        why = discovery._check_verdict_basis(None, "faradaic_efficiency.CO", ["R1"], None, None)
        assert why and "must declare `basis`" in why
        assert "already believed" in why      # the refusal must teach, not just reject

    def test_unknown_basis_is_refused(self):
        why = discovery._check_verdict_basis("vibes", "x", ["R1"], None, None)
        assert why and "unknown" in why.lower()

    def test_every_documented_option_is_accepted_when_its_support_is_present(self, monkeypatch):
        monkeypatch.setattr(discovery.database, "get_records_batch",
                            lambda ids: [{"descriptors": {"outputs": [
                                {"descriptors": [{"name": "x"}]}]}}])
        assert discovery._check_verdict_basis("cited_record", "x", ["R1"], None, None) is None
        assert discovery._check_verdict_basis("derived", "x", ["R1"], None, None) is None
        assert discovery._check_verdict_basis("computed_run", "x", ["R1"], "mlflow://run", None) is None
        assert discovery._check_verdict_basis("literature", "x", ["R1"], None, [{"doi": "10.x"}]) is None
        assert discovery._check_verdict_basis("prior_knowledge", "x", [], None, None) is None

    def test_cited_record_is_VERIFIED_against_the_records(self, monkeypatch):
        """The whole point: claiming a value is cited when it is not in the cited records is
        exactly the failure this rung exists to catch."""
        monkeypatch.setattr(discovery.database, "get_records_batch",
                            lambda ids: [{"descriptors": {"outputs": [
                                {"descriptors": [{"name": "something_else"}]}]}}])
        why = discovery._check_verdict_basis("cited_record", "faradaic_efficiency.CO",
                                             ["R1"], None, None)
        assert why and "does not appear in any of the cited records" in why
        assert "prior_knowledge" in why       # it must name the honest alternative

    def test_cited_record_without_citations_is_refused(self):
        why = discovery._check_verdict_basis("cited_record", "x", [], None, None)
        assert why and "no evidence_record_ids" in why

    def test_prior_knowledge_is_permitted_not_forbidden(self):
        """Domain knowledge is often what makes a reading correct. The rung makes it visible
        and discountable, never prohibited."""
        assert discovery._check_verdict_basis("prior_knowledge", "x", [], None, None) is None
        assert "NOT forbidden" in trace_provenance.VERDICT_BASIS["prior_knowledge"]

    def test_gate_binds_only_at_64_and_leaves_older_projects_alone(self):
        assert trace_provenance.POLICY_VERDICT_BASIS == 64
        assert trace_provenance.CURRENT_POLICY_VERSION >= 64
        for pv in (60, 61, 62, 63, trace_provenance.CURRENT_POLICY_VERSION):
            assert (pv >= trace_provenance.POLICY_VERDICT_BASIS) == (pv >= 64)


class TestStampedValuesAreNotIndependent:
    """One determination written onto N records is not N measurements.

    Found by a genericity adversary: `_declared_scale` computed `two_sample = len(sigmas) > 1`
    and returned sqrt(2)*sigma, so five stamped copies of a single paper-level number were read
    by our own scorer as five independent declarations. That is metadata-as-measurement,
    committed by the platform, on the very item where the benchmark penalises a model for
    committing it.
    """

    def _recs(self, *triples):
        return [{"descriptors": {"outputs": [{"descriptors": [
            {"name": "q", "value": v, "uncertainty": {"sigma": s}, "definition": d}]}]}}
            for v, s, d in triples]

    def test_identical_stamped_values_collapse_to_one(self, monkeypatch):
        stamped = self._recs(*[(-1.11, 0.05, "converted from one stated -1.6 V vs SHE")] * 5)
        monkeypatch.setattr(discovery.database, "get_records_batch", lambda ids: stamped)
        assert discovery._descriptor_sigmas("q", ["a", "b", "c", "d", "e"]) == [0.05]

    def test_stamped_values_do_not_earn_a_two_sample_scale(self, monkeypatch):
        """The consequence that matters: a narrowed scale makes a margin look decisive."""
        stamped = self._recs(*[(-1.11, 0.05, "one conversion")] * 5)
        monkeypatch.setattr(discovery.database, "get_records_batch", lambda ids: stamped)
        dec = discovery._declared_scale("q", ["a", "b", "c", "d", "e"])
        assert dec["value"] == 0.05                      # NOT 0.05 * sqrt(2)
        assert dec["kind"] == "value"

    def test_genuinely_distinct_measurements_still_earn_it(self, monkeypatch):
        distinct = self._recs((0.25, 0.02, "measured on sample A"),
                              (0.31, 0.02, "measured on sample B"))
        monkeypatch.setattr(discovery.database, "get_records_batch", lambda ids: distinct)
        dec = discovery._declared_scale("q", ["a", "b"])
        assert abs(dec["value"] - 0.02 * 2 ** 0.5) < 1e-9
        assert dec["kind"] == "difference"

    def test_same_value_different_definition_is_two_determinations(self, monkeypatch):
        """Two labs reporting the same number by different routes IS corroboration."""
        two = self._recs((0.25, 0.02, "measured by GC"), (0.25, 0.02, "measured by NMR"))
        monkeypatch.setattr(discovery.database, "get_records_batch", lambda ids: two)
        assert len(discovery._descriptor_sigmas("q", ["a", "b"])) == 2


class TestPolicy65SharedCauseIndependence:
    """Independence is shared-CAUSE, not shared-identifier.

    Pre-registered in the bench repo before any of this existed. The motivating measurement:
    of 276 record pairs in the benchmark corpus that the previous rule counted as fully
    independent, 83 (30%) were taken on the same instrument at the same facility by the same
    technique. `n_decisive` - the platform's bar for calling a hypothesis supported - was built
    on that count.
    """

    def _rec(self, rid, inst=None, fac=None, sess=None, group=None, links=None):
        r = {"record_id": rid, "system": {}}
        if inst or sess:
            r["system"]["instrument"] = {"instrument_name": inst}
            if sess:
                r["system"]["session"] = {"session_id": sess}
        if fac:
            r["system"]["facility"] = {"organization": fac}
        if group:
            r["attribution"] = {"produced_by": {"group": group}}
        if links:
            r["links"] = links
        return r

    def _tiers(self, rec):
        return {t for t, _ in discovery._cause_signature(rec)}

    def test_same_instrument_and_session_is_a_shared_cause(self):
        a = self._rec("A", inst="Gamry_G_300", sess="S1", fac="LBNL")
        b = self._rec("B", inst="Gamry_G_300", sess="S1", fac="LBNL")
        sa = {k for t, k in discovery._cause_signature(a) if t == 2}
        sb = {k for t, k in discovery._cause_signature(b) if t == 2}
        assert sa and sa == sb

    def test_same_instrument_DIFFERENT_session_is_not_tier_2(self):
        """A second sitting re-calibrates. That is weaker corroboration than a fresh lab, but
        it is not the same measurement twice."""
        a = self._rec("A", inst="Gamry_G_300", sess="S1", fac="LBNL")
        b = self._rec("B", inst="Gamry_G_300", sess="S2", fac="LBNL")
        sa = {k for t, k in discovery._cause_signature(a) if t == 2}
        sb = {k for t, k in discovery._cause_signature(b) if t == 2}
        assert sa and sb and not (sa & sb)

    def test_same_facility_is_tier_3_robustness_not_correlation(self):
        a = self._rec("A", inst="X", sess="S1", fac="LBNL")
        b = self._rec("B", inst="Y", sess="S2", fac="LBNL")
        assert 3 in self._tiers(a) and 3 in self._tiers(b)
        f = {k for t, k in discovery._cause_signature(a) if t == 3}
        g = {k for t, k in discovery._cause_signature(b) if t == 3}
        assert f == g

    def test_a_same_sample_link_binds_BOTH_endpoints_symmetrically(self):
        a = self._rec("A", links=[{"rel": "same_sample_as", "target": "B"}])
        b = self._rec("B", links=[{"rel": "same_sample_as", "target": "A"}])
        ka = {k for t, k in discovery._cause_signature(a) if t == 1}
        kb = {k for t, k in discovery._cause_signature(b) if t == 1}
        assert ka and ka == kb, "the key must be unordered or A->B and B->A never match"

    def test_two_calculations_sharing_code_and_functional_are_correlated(self):
        """A computed record's shared cause of error is the APPROXIMATION, not an instrument.
        Two DFT results at one functional are wrong together in a way two experiments on one
        bench are not - the schema is generic across theory and experiment and this rung must
        be too. 132 of 1722 records in the repository are computational."""
        a = {"record_id": "A", "computation": {"method": {
            "code": "VASP", "code_version": "6.3.2", "functional_name": "RPBE"}}}
        b = {"record_id": "B", "computation": {"method": {
            "code": "VASP", "code_version": "6.3.2", "functional_name": "RPBE"}}}
        ka = {k for t, k in discovery._cause_signature(a) if t == 2}
        kb = {k for t, k in discovery._cause_signature(b) if t == 2}
        assert ka and ka == kb

    def test_the_SAME_code_at_a_DIFFERENT_functional_is_not_correlated(self):
        """Varying the functional is the standard robustness check in computational science.
        It must not be scored as repeating yourself."""
        a = {"record_id": "A", "computation": {"method": {
            "code": "VASP", "code_version": "6.3.2", "functional_name": "RPBE"}}}
        b = {"record_id": "B", "computation": {"method": {
            "code": "VASP", "code_version": "6.3.2", "functional_name": "BEEF-vdW"}}}
        ka = {k for t, k in discovery._cause_signature(a) if t == 2}
        kb = {k for t, k in discovery._cause_signature(b) if t == 2}
        assert ka and kb and not (ka & kb)

    def test_same_functional_with_UNKNOWN_code_is_robustness_not_correlation(self):
        """The exchange-correlation approximation is the dominant systematic error in DFT and
        does not care which program applied it - but it is a weaker claim than one identical
        setup, so it lands in the robustness tier rather than the correlated one."""
        a = {"record_id": "A", "computation": {"method": {"functional_name": "PBE"}}}
        b = {"record_id": "B", "computation": {"method": {"functional_name": "PBE"}}}
        assert not {k for t, k in discovery._cause_signature(a) if t == 2}
        ka = {k for t, k in discovery._cause_signature(a) if t == 3}
        kb = {k for t, k in discovery._cause_signature(b) if t == 3}
        assert ka and ka == kb

    def test_a_different_functional_shares_nothing_even_with_code_unknown(self):
        a = {"record_id": "A", "computation": {"method": {"functional_name": "PBE"}}}
        b = {"record_id": "B", "computation": {"method": {"functional_name": "RPBE"}}}
        ka = {k for t, k in discovery._cause_signature(a) if t == 3}
        kb = {k for t, k in discovery._cause_signature(b) if t == 3}
        assert ka and kb and not (ka & kb)

    def test_a_computed_and_a_measured_record_never_share_a_cause(self):
        a = {"record_id": "A", "computation": {"method": {
            "code": "VASP", "code_version": "6", "functional_name": "RPBE"}}}
        b = self._rec("B", inst="Gamry_G_300", sess="S1")
        ka = {k for t, k in discovery._cause_signature(a) if t == 2}
        kb = {k for t, k in discovery._cause_signature(b) if t == 2}
        assert ka and kb and not (ka & kb)

    def test_absent_provenance_yields_NO_signature_and_stays_independent(self):
        """Falsifier F3: inferring correlation from missing data is the sigma-0 error again."""
        assert discovery._cause_signature(self._rec("A")) == set()
        assert discovery._cause_signature({}) == set()

    def test_placeholder_provenance_is_treated_as_absent_not_as_a_match(self):
        """Two records both saying 'not_specified_in_source' are not thereby the same lab."""
        a = self._rec("A", fac="not_specified_in_source", inst="unknown", sess="")
        b = self._rec("B", fac="not_specified_in_source", inst="unknown", sess="")
        assert discovery._cause_signature(a) == set()
        assert discovery._cause_signature(b) == set()

    def test_different_facilities_never_collide(self):
        a = self._rec("A", fac="LBNL")
        b = self._rec("B", fac="SLAC")
        f = {k for t, k in discovery._cause_signature(a) if t == 3}
        g = {k for t, k in discovery._cause_signature(b) if t == 3}
        assert f and g and not (f & g)

    def test_alias_and_canonical_organisation_names_MATCH(self):
        """Was a documented gap; now fixed in the controlled vocabulary. 'LBNL' and
        'Lawrence Berkeley National Laboratory' are one lab and must produce one key."""
        a = self._rec("A", fac="LBNL")
        b = self._rec("B", fac="Lawrence Berkeley National Laboratory")
        f = {k for t, k in discovery._cause_signature(a) if t == 3}
        g = {k for t, k in discovery._cause_signature(b) if t == 3}
        assert f and f == g

    def test_placeholders_from_the_vocabulary_are_treated_as_absent(self):
        for junk in ("not_specified_in_source", "TBD", "literature", "unknown"):
            r = self._rec("A", fac=junk)
            assert not {k for t, k in discovery._cause_signature(r) if t == 3}, junk

    def test_every_canonical_organisation_carries_a_ROR_id(self):
        """Rigour means a registry, not a spelling. Each id below was resolved against the
        live ROR API when the vocabulary was written."""
        import ontology
        vocab = ontology.load_vocabulary() or {}
        orgs = None
        for sec in vocab.values():
            if isinstance(sec, dict) and "system.organizations" in sec:
                orgs = sec["system.organizations"]["values"]
        assert orgs, "organization vocabulary missing"
        for name, ror in orgs.items():
            assert str(ror).startswith("https://ror.org/"), (name, ror)

    def test_every_alias_resolves_to_a_canonical_organisation(self):
        import ontology
        vocab = ontology.load_vocabulary() or {}
        orgs = aliases = None
        for sec in vocab.values():
            if isinstance(sec, dict):
                orgs = sec.get("system.organizations", {}).get("values", orgs)
                aliases = sec.get("system.organization_aliases", {}).get("map", aliases)
        assert orgs and aliases
        for k, v in aliases.items():
            assert v in orgs, "alias %r points at %r which is not canonical" % (k, v)

    def test_ORIGINAL_GAP_unnormalised_organisation_names_do_not_match(self):
        """The gap this class shipped with, kept as a regression test: an organisation NOT in
        the alias map still produces its own key, so a new spelling silently reduces measured
        correlation rather than inventing it. That is the safe direction, and it is the reason
        the validator emits a vocabulary signal for unknown organisations."""
        a = self._rec("A", fac="Institute of Something Unlisted")
        b = self._rec("B", fac="Inst. of Something Unlisted")
        f = {k for t, k in discovery._cause_signature(a) if t == 3}
        g = {k for t, k in discovery._cause_signature(b) if t == 3}
        assert not (f & g)

    def test_a_lookup_failure_never_blocks_scoring(self, monkeypatch):
        def boom(ids):
            raise RuntimeError("records store down")
        monkeypatch.setattr(discovery.database, "get_records_batch", boom)
        assert discovery._cause_signatures(["A", "B"]) == (set(), set())

    def test_the_gate_is_registered_and_current(self):
        assert tp.POLICY_SHARED_CAUSE == 65
        assert tp.CURRENT_POLICY_VERSION == tp.POLICY_SHARED_CAUSE

    def test_the_manifest_states_the_rule_and_advertises_its_version(self):
        man = discovery.get_manifest()
        node = man.get("contract", man)
        assert node["policy_version"] == tp.CURRENT_POLICY_VERSION
        assert node["version"].startswith("0.73-")
        src = open(discovery.__file__.replace(".pyc", ".py")).read()
        assert "independence_is_shared_cause_not_shared_identifier" in src
        assert "policy_version >= 65" in src

    def test_the_clause_and_the_signature_name_no_domain(self):
        """STANDING: the manifest must be generic for any scientific discovery."""
        src = open(discovery.__file__.replace(".pyc", ".py")).read()
        i = src.index("independence_is_shared_cause_not_shared_identifier")
        clause = src[i:i + 2600].lower()
        import inspect
        code = inspect.getsource(discovery._cause_signature).lower()
        for banned in ("cu-ag", "co2rr", "faradaic", "catalys", "electrode", "potentiostat",
                       "gamry", "vasp", "lbnl", "slac"):
            assert banned not in clause, "clause leaked a domain term: %s" % banned
            assert banned not in code, "signature leaked a domain term: %s" % banned
