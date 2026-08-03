"""Trace provenance — WHO reasoned and WHY. Pure logic, no DB."""
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
