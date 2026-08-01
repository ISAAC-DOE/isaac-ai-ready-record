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
