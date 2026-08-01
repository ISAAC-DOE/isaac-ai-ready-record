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
        assert gaps["models_seen"] == {"claude-opus-5": 1}

    def test_thin_reasoning_step_is_flagged(self):
        gaps = tp.trace_gaps([
            {"id": 3, "event_type": "reasoning_step", "decision": {"chose": "a"}},
        ])
        assert gaps["reasoning_steps_without_decision"] == [3]

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
