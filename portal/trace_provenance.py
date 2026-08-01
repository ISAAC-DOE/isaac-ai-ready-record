"""
Trace provenance primitives — WHO reasoned, and WHY.

PURE LOGIC (no DB, no Flask) so the whole contract is unit-testable offline, matching
the pattern of `record_provenance` (content hashing) and `record_authz` (edit rights).

Two objects travel with a discovery event:

  * `actor_model` — WHICH model produced this step. A multi-account, multi-vendor
    reproducibility study is uninterpretable without it, and a fine-tuning corpus built
    from unattributed traces is worthless. HONESTY IS THE POINT: the portal can verify
    the *principal* (server-stamped from the Bearer token) but it cannot verify which
    model a client claims to be running. So model identity is stamped
    `identity_trust='client_attested'` and must never be presented as verified.

  * `decision` — WHY. Not just the state change: what was CHOSEN, what was REJECTED and
    on what grounds, and what the step is BLOCKED on. A trace that records only what
    happened cannot teach anything; the rejected branch is often the informative one.

Both are optional at the API boundary and normalized here. Anything unrecognised is
dropped rather than stored, so a malformed client cannot poison the ledger.
"""
from __future__ import annotations

# Client-attested model identity. Kept deliberately small: provider/id/version are
# stable and portable across vendors; per-vendor telemetry is not, and would rot.
_MODEL_KEYS = ("provider", "model_id", "model_version", "harness", "harness_version")

# How much the platform can vouch for the identity. Only ever these two today:
#   client_attested — the caller told us (default; never present as verified)
#   gateway_stamped — an authenticating gateway asserted it (reserved; not yet issued)
_TRUST_VALUES = ("client_attested", "gateway_stamped")

_DECISION_SCALARS = ("chose",)
_DECISION_LISTS = ("rejected", "because", "blocked_on")

_MAX_STR = 2000
_MAX_LIST = 40


def _clean_str(v):
    if v is None:
        return None
    if not isinstance(v, str):
        v = str(v)
    v = v.strip()
    return v[:_MAX_STR] or None


def _clean_list(v):
    """A list of short strings. Accepts a bare string as a one-item list, because
    agents reliably send one when they mean one."""
    if v is None:
        return None
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return None
    out = []
    for item in v:
        s = _clean_str(item)
        if s:
            out.append(s)
        if len(out) >= _MAX_LIST:
            break
    return out or None


def normalize_actor_model(raw):
    """Normalize a client-attested model claim, or None.

    `identity_trust` is FORCED, never taken from the caller: a client cannot promote
    its own claim to 'gateway_stamped'. That is the whole point of the field.
    """
    if not isinstance(raw, dict):
        return None
    out = {}
    for k in _MODEL_KEYS:
        s = _clean_str(raw.get(k))
        if s:
            out[k] = s
    if not out:
        return None
    out["identity_trust"] = "client_attested"
    return out


def normalize_decision(raw):
    """Normalize a decision record, or None.

    Shape: {chose, rejected[], because[], blocked_on[]}. A decision with nothing but
    `chose` is accepted — recording the choice without the discarded alternatives is
    still better than recording neither — but `is_complete_decision` marks it thin so
    the briefing can ask for the rest.
    """
    if not isinstance(raw, dict):
        return None
    out = {}
    for k in _DECISION_SCALARS:
        s = _clean_str(raw.get(k))
        if s:
            out[k] = s
    for k in _DECISION_LISTS:
        lst = _clean_list(raw.get(k))
        if lst:
            out[k] = lst
    return out or None


def is_complete_decision(decision) -> bool:
    """A decision is COMPLETE when it records the road not taken as well as the road
    taken: what was chosen, and either why, or what was rejected. `blocked_on` alone
    is a stall, not a decision."""
    if not isinstance(decision, dict):
        return False
    if not decision.get("chose"):
        return False
    return bool(decision.get("because") or decision.get("rejected"))


# Event types that CHANGE what the project believes. These are the ones whose
# authorship matters: a reasoning note with no model attached costs little, a verdict
# with no model attached makes a multi-model comparison unreadable.
BELIEF_CHANGING = frozenset({
    "hypothesis_created", "prediction_added", "prediction_evaluated",
    "next_experiment_proposed", "status_changed", "ranking_updated",
    "evidence_ingested", "human_directive", "project_created",
})


def trace_gaps(events) -> dict:
    """Audit a project's event stream for attribution and decision completeness.

    Advisory only — this NEVER touches scoring. It feeds `method_compliance` so an
    agent can see, and close, its own gaps.

    events: iterable of dicts with at least `event_type`; optionally `id`,
            `actor_model`, `decision`.
    """
    unattributed, thin, models = [], [], {}
    for e in (events or []):
        etype = (e or {}).get("event_type")
        eid = (e or {}).get("id")
        am = (e or {}).get("actor_model")
        if isinstance(am, dict) and am.get("model_id"):
            models[am["model_id"]] = models.get(am["model_id"], 0) + 1
        elif etype in BELIEF_CHANGING:
            unattributed.append(eid)
        if etype == "reasoning_step" and not is_complete_decision((e or {}).get("decision")):
            thin.append(eid)
    return {
        "unattributed_belief_changing": unattributed,
        "reasoning_steps_without_decision": thin,
        "models_seen": models,
        "single_model_trace": len(models) <= 1,
    }
