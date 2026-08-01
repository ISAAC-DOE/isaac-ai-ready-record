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
    # PostgreSQL rejects \u0000 inside jsonb. One stray NUL from a client would fail the
    # INSERT and LOSE the whole event, so strip NULs and other C0 controls (keeping tab,
    # newline, carriage return) before the value can ever reach the ledger.
    v = "".join(ch for ch in v if ch >= " " or ch in "\t\n\r")
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


def trace_gaps(events, *, sample_cap: int = 20) -> dict:
    """Audit an event stream for attribution and decision completeness.

    Advisory only — this NEVER touches scoring. It feeds `method_compliance` so an
    agent can see, and close, its own gaps.

    Events written before trace provenance existed carry no actor_model by construction,
    so the gap lists are COUNTS plus a capped sample rather than an exhaustive dump: a
    briefing that lists two thousand legacy ids is noise, and noise is how a compliance
    surface gets ignored.

    events: iterable of dicts with at least `event_type`; optionally `id`,
            `actor_model`, `decision`.
    """
    unattributed, thin, models = [], [], {}
    n_unattributed = n_thin = 0
    for e in (events or []):
        etype = (e or {}).get("event_type")
        eid = (e or {}).get("id")
        am = (e or {}).get("actor_model")
        if isinstance(am, dict) and am.get("model_id"):
            # Key by provider/model_id: two vendors can ship the same short model name,
            # and collapsing them would silently understate how many models ran.
            key = "%s/%s" % (am.get("provider") or "?", am["model_id"])
            models[key] = models.get(key, 0) + 1
        elif etype in BELIEF_CHANGING:
            n_unattributed += 1
            if len(unattributed) < sample_cap:
                unattributed.append(eid)
        if etype == "reasoning_step" and not is_complete_decision((e or {}).get("decision")):
            n_thin += 1
            if len(thin) < sample_cap:
                thin.append(eid)
    return {
        "unattributed_belief_changing": unattributed,
        "unattributed_belief_changing_count": n_unattributed,
        "reasoning_steps_with_incomplete_decision": thin,
        "reasoning_steps_with_incomplete_decision_count": n_thin,
        "models_seen": models,
        # None (not True) when nothing is attributed at all: "one model" and "no idea
        # which model" are different states and must not look alike.
        "single_model_trace": (len(models) == 1) if models else None,
    }


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------
# The trace contract version currently in force. A project is stamped with this at
# creation and held to it for life; a project created before enforcement carries NULL
# and is never retro-enforced. This is what lets the contract improve without either
# breaking old projects or watering down the rules for new ones.
CURRENT_POLICY_VERSION = 60

# The portal itself is an actor. Several belief-changing events originate SERVER-side
# (a project being created, a dataset being declared, a ranking recomputed), and they are
# genuinely not the agent's reasoning. Signing them honestly keeps the compliance surface
# meaningful: a project that opens with a phantom "unattributed" flag it cannot clear
# teaches agents to ignore the flag.
SERVER_ACTOR = {"provider": "isaac", "model_id": "portal", "harness": "server",
                "identity_trust": "client_attested"}


def enforcement_error(project_policy_version, event_type, actor_model, decision):
    """Return a human-readable rejection reason, or None if the write is acceptable.

    Enforced ONLY for projects born at or after CURRENT_POLICY_VERSION. Legacy projects
    (NULL) are advisory-only, exactly as before.

    Two structural requirements, both cheap for a compliant agent and both impossible to
    reconstruct after the fact if skipped:
      * a belief-changing write must say WHICH MODEL made it
      * a reasoning_step must carry a decision object

    A THIN decision (chose with no grounds) is accepted here and flagged in
    method_compliance instead: completeness is a quality judgement, and a hard gate on it
    would push agents toward writing nothing rather than writing something partial.
    """
    try:
        pv = int(project_policy_version)
    except (TypeError, ValueError):
        return None                      # legacy project: advisory only
    if pv < CURRENT_POLICY_VERSION:
        return None
    if event_type in BELIEF_CHANGING and not normalize_actor_model(actor_model):
        return ("policy_version %d requires `actor_model` on belief-changing events "
                "(event_type=%s). Send {provider, model_id, model_version} naming the "
                "model that is reasoning. An unsigned write cannot be attributed later."
                % (pv, event_type))
    if event_type == "reasoning_step" and not normalize_decision(decision):
        return ("policy_version %d requires `decision` on reasoning_step. Send "
                "{chose, rejected, because, blocked_on}: the branch you did NOT take "
                "cannot be reconstructed from the outcome." % pv)
    return None
