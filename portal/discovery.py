"""
ISAAC Discovery — hypothesis-driven reasoning workbench (data-access layer).

ISOLATION CONTRACT (read before editing):
- Every function here talks ONLY to the isolated isaac_discovery database via
  database.get_discovery_db_connection(). It must NEVER call
  database.get_db_connection() (the privileged records connection). The single
  exception is read-only provenance lookups, which use
  database.get_readonly_db_connection() (records, READ ONLY) to resolve a record
  title for display — see resolve_record_summaries().
- Hypotheses/projects/predictions are NOT ISAAC records and never enter the
  records table or the frozen standard. They live only in isaac_discovery.
- discovery_user is least-privilege and physically cannot reach the records DB,
  so this contract is also enforced at the credential level; the rule above
  keeps the application code honest on top of that.

Table DDL lives in database.init_discovery_tables() (Dean's marker), created on
startup. This module is the CRUD + activity-feed + provenance surface that the
Discovery page and the /portal/api/* discovery endpoints call.
"""

import json
import logging
import re
import secrets
import time

import database

logger = logging.getLogger("isaac-discovery")

# Crockford base32 (a subset of [0-9A-Z]); ULID-style 26-char ids, generated
# server-side. Discovery ids are independent of records ULIDs (separate DB).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Allowed activity-feed event types (the agent posts these via POST .../events;
# structured writes below also auto-append the matching one).
EVENT_TYPES = {
    "hypothesis_created", "prediction_added", "prediction_evaluated",
    "ranking_updated", "status_changed", "next_experiment_proposed",
    "evidence_ingested", "agent_message", "project_created",
    "compute_submitted", "compute_running",
}

# Prediction workflow lifecycle (distinct from `verdict`, the scientific
# outcome). Drives the Validation board (Section B). Order = pipeline order.
WORK_STATUSES = {
    "awaiting_evidence", "more_work_pending", "compute_submitted",
    "compute_running", "evaluated",
}
WORK_STATUS_ORDER = ["awaiting_evidence", "more_work_pending", "compute_submitted",
                     "compute_running", "evaluated"]

# Hypothesis status + prediction verdict vocabularies (documented for agents;
# not hard-enforced yet while the reasoning loop is still being learned).
HYPOTHESIS_STATUSES = ["proposed", "supported", "eliminated", "needs_more_data",
                       "superseded"]
VERDICTS = ["supports", "contradicts", "neutral", "insufficient"]

# v1: hypotheses form a graph, not a list.
RELATION_TYPES = {"supersedes", "derived_from", "competes_with", "co_operating"}
# v1: a prediction has many compute runs; backends are data, not enum-locked.
COMPUTE_STATUSES = {"queued", "running", "completed", "failed", "resubmitted"}

# Accept-and-normalize: agents reach for natural words. We map common synonyms to
# the canonical vocabulary on write (teach, don't block) so the briefing's
# categorization stays correct. (Lesson from the first live agent run.)
VERDICT_SYNONYMS = {
    "refutes": "contradicts", "refute": "contradicts", "refuted": "contradicts",
    "rejects": "contradicts", "contradict": "contradicts", "against": "contradicts",
    "support": "supports", "supported": "supports", "confirms": "supports",
    "inconclusive": "neutral", "ambiguous": "neutral", "mixed": "neutral",
    "no_data": "insufficient", "incompatible": "insufficient", "none": "insufficient",
}
RELATION_SYNONYMS = {
    "co_operates_with": "co_operating", "cooperates_with": "co_operating",
    "cooperating": "co_operating", "cooperates": "co_operating",
    "competes": "competes_with", "supersede": "supersedes",
    "derives_from": "derived_from", "derived": "derived_from",
}


def normalize_verdict(v):
    if not v:
        return v
    k = str(v).strip().lower()
    return VERDICT_SYNONYMS.get(k, k)


def normalize_relation(r):
    if not r:
        return r
    k = str(r).strip().lower()
    return RELATION_SYNONYMS.get(k, k)


def get_manifest() -> dict:
    """Self-describing contract: the bootstrap an agent fetches to learn how to
    operate on ISAAC discovery projects. PROVISIONAL (v0.1) — refined as the real
    reasoning loop is pinned down with the practitioners."""
    return {
        "name": "ISAAC Discovery — Agent Operating Protocol",
        "version": "0.9-provisional",
        "base_path": "https://isaac.slac.stanford.edu/portal/api",
        "endpoint_paths_note": "Every endpoint `path` below is relative to "
            "`base_path` (e.g. base_path + '/projects'), NOT to this manifest's own "
            "URL. Do not prepend '/discovery/' to them — the manifest just happens "
            "to live under /discovery/.",
        "prime_directive": [
            "READ before you act: GET /projects/{id}/briefing at the start of every "
            "turn; treat it as authoritative current state and reconcile to it.",
            "WRITE after you act: every hypothesis, prediction, verdict, status "
            "change and compute run is an API write. If it is not on the dashboard, "
            "it did not happen — never hold project state only in your context.",
            "One project = one ground truth. Do not fork reality in your head.",
        ],
        "resume_protocol": "To CONTINUE an existing project from a cold start (a "
            "fresh agent with no prior memory): GET /projects to find it, then GET "
            "/projects/{id}/context — a single call returning the full current state "
            "PLUS the entire step-by-step reasoning history (every hypothesis, "
            "prediction, verdict, compute run, with detail) PLUS the briefing. Read it "
            "all to reconstruct exactly how the project got here before you act. The "
            "briefing alone is a per-turn digest, not the full history — use /context "
            "to resume.",
        "auth": {"scheme": "Bearer", "header": "Authorization: Bearer <token>",
                 "obtain": "portal API Keys page; user must be in an allowed group"},
        "getting_started": {
            "what": "Discovery is a hypothesis-driven scientific-discovery workbench. "
                "You drive it with an AI agent (any LLM/agent with web access) that "
                "reads this manifest and self-configures — you don't need to learn the "
                "API yourself.",
            "steps": [
                "Generate a Bearer token from the portal's API Keys page.",
                "Paste the `agent_prompt` below into your agent, with your token.",
                "Your agent fetches this manifest, learns the whole protocol, and is "
                "ready to start a new discovery project or continue a shared one.",
            ],
            "agent_prompt": (
                "You are connecting to the ISAAC Discovery platform — a "
                "hypothesis-driven scientific-discovery workbench at SLAC. Bootstrap "
                "yourself:\n\n"
                "1. Read the self-describing operating manual (it defines everything: "
                "auth, all endpoints, the reasoning protocol, how to record decisions, "
                "and the literature/compute integrations):\n"
                "   GET https://isaac.slac.stanford.edu/portal/api/discovery/manifest\n"
                "   Read ALL of it and follow it exactly.\n\n"
                "2. Authenticate every request with:\n"
                "   Authorization: Bearer <PASTE_YOUR_PORTAL_API_TOKEN_HERE>\n\n"
                "3. Verify access and list projects:\n"
                "   GET https://isaac.slac.stanford.edu/portal/api/projects\n"
                "   (the projects you own or that are shared with you).\n\n"
                "4. To CONTINUE an existing project (recommended for a fresh agent): "
                "GET /projects/{id}/context — one call returns the FULL state + the "
                "ENTIRE step-by-step reasoning history (with detail) + the briefing, so "
                "you can reconstruct exactly where the project stands. To START a new "
                "one: POST /projects.\n\n"
                "5. Prime directive: the dashboard is the single source of truth. Each "
                "turn GET the project's /briefing and reconcile to it; write every "
                "hypothesis, prediction, verdict and reasoning step back via the API — "
                "if it isn't written to the dashboard, it didn't happen.\n\n"
                "Then tell me, in your own words, the current state and full history of "
                "the project (or the workflow for a new one), and what to do next."
            ),
        },
        "object_model": "project -> hypotheses -> predictions; append-only events "
                        "journal; one next_experiment per project. evidence_record_ids "
                        "are plain ISAAC record IDs (read-only cross-reference).",
        "state_machines": {
            "hypothesis_status": HYPOTHESIS_STATUSES,
            "hypothesis_relation_types": sorted(RELATION_TYPES),
            "prediction_work_status": WORK_STATUS_ORDER,
            "prediction_verdict": VERDICTS,
            "compute_run_status": sorted(COMPUTE_STATUSES),
            "note": "work_status = where in the pipeline; verdict = the scientific "
                    "outcome (set at 'evaluated'). They are orthogonal. A prediction "
                    "may have MANY compute_runs (failed + resubmit). Hypotheses form a "
                    "graph via relations (supersedes/derived_from/competes_with/"
                    "co_operating), not a flat list. A prediction's `discriminates` "
                    "([{hypothesis_label, expected}]) declares what each hypothesis "
                    "predicts for that measurable; the server aggregates these into the "
                    "cross-hypothesis discrimination matrix.",
        },
        "event_types": sorted(EVENT_TYPES),
        "endpoints": [
            {"m": "GET", "path": "/projects/{id}/briefing",
             "purpose": "Curated ground-truth digest (incl. evidence-index summary, "
                        "discrimination matrix) — READ THIS FIRST each turn."},
            {"m": "GET", "path": "/projects/{id}/context",
             "purpose": "ONE-SHOT RESUME bundle: full state + the ENTIRE step-by-step "
                        "reasoning history (every event, with detail) + the briefing. "
                        "A fresh agent with no prior context calls this FIRST to fully "
                        "reconstruct an existing project before continuing."},
            {"m": "GET", "path": "/projects/{id}/evidence",
             "purpose": "Exhaustive descriptor-keyed evidence index (element-matched "
                        "candidates, reaction annotated). ?descriptor=<name> to narrow. "
                        "Query this by a prediction's descriptor before saying 'no data'."},
            {"m": "PUT", "path": "/projects/{id}/evidence_overrides",
             "purpose": "Curate the auto candidates: {include:[record_id], exclude:[...]}."},
            {"m": "POST", "path": "/projects", "purpose": "Create a project."},
            {"m": "GET", "path": "/projects", "purpose": "List your projects."},
            {"m": "GET", "path": "/projects/{id}", "purpose": "Full project view."},
            {"m": "POST", "path": "/projects/{id}/hypotheses",
             "purpose": "Add a hypothesis (statement, label, origin, mechanism)."},
            {"m": "PUT", "path": "/hypotheses/{id}",
             "purpose": "Update status / confidence / confidence_basis."},
            {"m": "POST", "path": "/hypotheses/{id}/predictions",
             "purpose": "Add a prediction (descriptor_name, direction, falsification, "
                        "output_quantity, discriminates:[{hypothesis_label,expected}])."},
            {"m": "POST", "path": "/hypotheses/{id}/relations",
             "purpose": "Link hypotheses {to_hypothesis_id, relation_type, note}."},
            {"m": "PUT", "path": "/predictions/{id}/status",
             "purpose": "Advance the prediction work_status lane."},
            {"m": "POST", "path": "/predictions/{id}/runs",
             "purpose": "Register a compute run {backend, engine, resource, "
                        "slurm_job_id, mlflow_run_url, status, params, metrics}. "
                        "IDEMPOTENT on (prediction_id, slurm_job_id): re-POSTing the "
                        "same job updates it, never duplicates."},
            {"m": "PUT", "path": "/runs/{run_id}",
             "purpose": "Update a compute run {status, metrics, mlflow_run_url, ...}."},
            {"m": "DELETE", "path": "/runs/{run_id}",
             "purpose": "Delete a compute run (e.g. a stray duplicate)."},
            {"m": "PUT", "path": "/predictions/{id}/evaluate",
             "purpose": "Terminal: set verdict + strength + evidence + mlflow_run_url. "
                        "GATE on methodological compatibility (output_quantity / "
                        "functional / corrections) before trusting an evidence record."},
            {"m": "POST", "path": "/projects/{id}/events",
             "purpose": "Append a reasoning-transcript entry (one per step)."},
            {"m": "PUT", "path": "/projects/{id}/next_experiment",
             "purpose": "Propose the discriminating next experiment."},
            {"m": "POST", "path": "/projects/{id}/share",
             "purpose": "Owner shares the project (read) with another portal identity "
                        "{identity, access}; it then appears in that user's tab."},
            {"m": "DELETE", "path": "/projects/{id}/share/{identity}",
             "purpose": "Revoke a share."},
        ],
        "per_turn_loop": [
            "GET /briefing", "reason", "write each move (hypotheses/predictions/"
            "evaluate/status)", "POST /events per step", "PUT /next_experiment"],
        "compute_loop": [
            "submit NERSC/DFT/MLIP/microkinetics job",
            "PUT /predictions/{id}/status {work_status:'compute_submitted', mlflow_run_url}",
            "PUT ... {work_status:'compute_running'} when it starts",
            "PUT /predictions/{id}/evaluate with verdict + evidence + final mlflow_run_url"],
        "field_shapes": {
            "origin": {"type": "agent_reasoning|literature|prior_result|human",
                       "summary": "str", "reasoning": "str",
                       "sources": "[{record_id|doi|hypothesis}]"},
            "event": {"event_type": "(required, from event_types)",
                      "summary": "(REQUIRED, one line)", "detail": "(optional, long)",
                      "hypothesis_id": "optional", "evidence_record_ids": "optional",
                      "mlflow_run_url": "optional"},
            "next_experiment": {"_semantics": "PUT REPLACES the whole object (not a "
                                "merge); send the complete payload each time. ALL keys "
                                "you send are stored — nothing is dropped.",
                                "descriptor": "str", "facility": "str", "method": "str",
                                "rationale": "str",
                                "predicted_outcomes": "[{hypothesis_label, expected}]"},
        },
        "auditability": "Record EVERY decision point in BOTH places (dual-write): "
            "(1) POST an `event` to the dashboard with a `detail` carrying the full "
            "reasoning — this is canonical and drives the briefing; (2) mirror the same "
            "step to MLflow (see integrations.experiment_tracking). Put the decision "
            "logic in each prediction's `rationale` (method-compat check + direction + "
            "magnitude-vs-falsification + replication). One-line summaries are not "
            "enough — if it isn't recorded in full, it can't be audited.",
        "integrations": {
            "experiment_tracking_mlflow": {
                "purpose": "MLflow is the unified experiment-replay trace — it logs "
                    "the COMPUTE *and*, now, the full REASONING, so an MLflow run is a "
                    "self-contained record of the whole discovery cycle.",
                "convention": "One MLflow experiment per project, named "
                    "`ISAAC-Discovery-<project_id>`; one run per hypothesis (or per "
                    "project for the reasoning stream).",
                "log_every_thinking_step": "After you POST each dashboard event "
                    "(canonical), MIRROR it to MLflow: mlflow.log_text(json.dumps(step), "
                    "f'reasoning/{n:04d}.json') and increment a 'reasoning_step' metric, "
                    "so the run holds the complete, ordered decision sequence — every "
                    "hypothesis formed, every prediction, every verdict and why.",
                "compute": "Log params (functional, slab, …), metrics (E_ads, scores), "
                    "and the Slurm job IDs as tags; put the run URL on the dashboard "
                    "compute_run.mlflow_run_url so the two cross-link.",
                "cross_link": "Tag every MLflow run with project_id + the dashboard URL; "
                    "store mlflow_run_url back on the dashboard event/run.",
                "source_of_truth": "The DASHBOARD is canonical (the briefing reads it). "
                    "MLflow mirrors for replay — write the dashboard FIRST, then mirror, "
                    "so they never diverge.",
            },
            "literature_search": {
                "provider": "Edison Scientific (FutureHouse PaperQA3)",
                "via": "portal_proxy — the portal holds the Edison key server-side; "
                    "you never see it.",
                "submit": "POST /literature/search {query, job} -> {task_id} (202). "
                    "job ∈ literature | literature_high | precedent | analysis.",
                "poll": "GET /literature/search/{task_id} -> {status, done, answer, "
                    "sources}. Async: PaperQA3 takes ~2-5 min; poll until done.",
                "auth": "your existing portal Bearer token (no Edison key needed).",
                "use_when": "forming a hypothesis `origin`, or cross-checking a "
                    "prediction against published work.",
                "note": "The OLD api.edisonsci.com host is DECOMMISSIONED. Direct REST "
                    "(only if the proxy is ever unavailable) is "
                    "api.platform.edisonscientific.com with an api_key->JWT exchange at "
                    "/auth/login. Prefer the proxy.",
            },
        },
        "vocabulary_is_normalized": "Verdicts and relation_types are accept-and-"
            "normalized: synonyms (e.g. 'refutes'->'contradicts', "
            "'co_operates_with'->'co_operating', 'inconclusive'->'neutral') are "
            "mapped to canonical on write. Prefer the canonical terms above.",
        "invariant": "If it is not on the dashboard, it did not happen.",
    }


def new_ulid() -> str:
    """26-char Crockford-base32 ULID (48-bit time + 80-bit randomness)."""
    val = ((int(time.time() * 1000) & ((1 << 48) - 1)) << 80) | secrets.randbits(80)
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[val & 0x1F])
        val >>= 5
    return "".join(reversed(out))


def _conn():
    return database.get_discovery_db_connection()


def _append_event(cur, project_id, event_type, summary, *, detail=None,
                  hypothesis_id=None, evidence_record_ids=None,
                  mlflow_run_url=None, actor=None):
    """Insert one activity-feed row. Caller owns the transaction/commit."""
    cur.execute(
        """INSERT INTO hyp_events
             (project_id, hypothesis_id, event_type, summary, detail,
              evidence_record_ids, mlflow_run_url, actor_identity)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (project_id, hypothesis_id, event_type, summary, detail,
         evidence_record_ids, mlflow_run_url, actor))
    return cur.fetchone()["id"]


def _project_of_hypothesis(cur, hypothesis_id):
    cur.execute("SELECT project_id FROM hyp_hypotheses WHERE hypothesis_id=%s",
                (hypothesis_id,))
    row = cur.fetchone()
    return row["project_id"] if row else None


def _is_owner(cur, project_id, identity):
    cur.execute("SELECT 1 FROM hyp_projects WHERE project_id=%s AND owner_identity=%s",
                (project_id, identity))
    return cur.fetchone() is not None


def _can_read(cur, project_id, identity):
    """Owner OR anyone the project is shared with."""
    if _is_owner(cur, project_id, identity):
        return True
    cur.execute("SELECT 1 FROM hyp_project_shares WHERE project_id=%s AND identity=%s",
                (project_id, identity))
    return cur.fetchone() is not None


def share_project(project_id, identity, *, access="read", owner_identity=None) -> bool:
    """Owner-only: grant another portal identity access to this project."""
    if not identity:
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        if owner_identity is not None and not _is_owner(cur, project_id, owner_identity):
            return False
        cur.execute(
            """INSERT INTO hyp_project_shares (project_id, identity, access, granted_by)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (project_id, identity)
               DO UPDATE SET access=EXCLUDED.access""",
            (project_id, identity.strip(), access if access in ("read", "write") else "read",
             owner_identity))
        _append_event(cur, project_id, "status_changed",
                      f"Project shared with {identity.strip()} ({access})",
                      actor=owner_identity)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def unshare_project(project_id, identity, *, owner_identity=None) -> bool:
    conn = _conn()
    cur = conn.cursor()
    try:
        if owner_identity is not None and not _is_owner(cur, project_id, owner_identity):
            return False
        cur.execute("DELETE FROM hyp_project_shares WHERE project_id=%s AND identity=%s "
                    "RETURNING id", (project_id, identity))
        ok = cur.fetchone() is not None
        conn.commit()
        return ok
    finally:
        cur.close()
        conn.close()


# --- Projects --------------------------------------------------------------

def create_project(owner_identity, title, goal=None, material_system=None,
                   reaction=None) -> str:
    conn = _conn()
    cur = conn.cursor()
    try:
        project_id = new_ulid()
        cur.execute(
            """INSERT INTO hyp_projects
                 (project_id, owner_identity, title, goal, material_system, reaction)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (project_id, owner_identity, title, goal, material_system, reaction))
        _append_event(cur, project_id, "project_created",
                      f"Project created: {title}", actor=owner_identity)
        conn.commit()
        return project_id
    finally:
        cur.close()
        conn.close()


def list_projects(owner_identity) -> list:
    """Project cards visible to a user: ones they OWN plus ones SHARED with them.
    Each row carries `is_owner` + `owner_identity` so the UI can mark shared ones."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT p.project_id, p.title, p.goal, p.status, p.material_system,
                      p.reaction, p.updated_at, p.owner_identity,
                      (p.owner_identity = %s) AS is_owner,
                      COUNT(h.hypothesis_id) AS n_hypotheses
                 FROM hyp_projects p
                 LEFT JOIN hyp_hypotheses h ON h.project_id = p.project_id
                WHERE p.owner_identity = %s
                   OR p.project_id IN (SELECT project_id FROM hyp_project_shares
                                       WHERE identity = %s)
                GROUP BY p.id
                ORDER BY p.updated_at DESC""",
            (owner_identity, owner_identity, owner_identity))
        projects = cur.fetchall()
        for p in projects:
            cur.execute(
                """SELECT label, statement, confidence, status
                     FROM hyp_hypotheses
                    WHERE project_id = %s
                    ORDER BY confidence DESC NULLS LAST LIMIT 1""",
                (p["project_id"],))
            p["leading_hypothesis"] = cur.fetchone()
        return projects
    finally:
        cur.close()
        conn.close()


def get_project(project_id, owner_identity=None) -> dict | None:
    """Full project view: hypotheses (each with predictions), events, next_exp.

    `owner_identity` here is the REQUESTER. Returns None unless they can read the
    project (owner OR shared-with). API scoping is enforced by the caller too."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM hyp_projects WHERE project_id=%s", (project_id,))
        project = cur.fetchone()
        if project is None:
            return None
        if owner_identity is not None and not _can_read(cur, project_id, owner_identity):
            return None
        cur.execute("SELECT identity, access FROM hyp_project_shares WHERE project_id=%s "
                    "ORDER BY created_at", (project_id,))
        project["shared_with"] = cur.fetchall()
        cur.execute(
            """SELECT * FROM hyp_hypotheses WHERE project_id=%s
               ORDER BY confidence DESC NULLS LAST, created_at""",
            (project_id,))
        hypotheses = cur.fetchall()
        for h in hypotheses:
            cur.execute(
                """SELECT * FROM hyp_predictions WHERE hypothesis_id=%s
                   ORDER BY created_at""",
                (h["hypothesis_id"],))
            h["predictions"] = cur.fetchall()
            for p in h["predictions"]:
                cur.execute(
                    """SELECT * FROM hyp_compute_runs WHERE prediction_id=%s
                       ORDER BY created_at""", (p["prediction_id"],))
                p["compute_runs"] = cur.fetchall()
        cur.execute(
            """SELECT * FROM hyp_hypothesis_relations WHERE project_id=%s
               ORDER BY created_at""", (project_id,))
        relations = cur.fetchall()
        cur.execute(
            """SELECT * FROM hyp_events WHERE project_id=%s
               ORDER BY created_at DESC LIMIT 200""",
            (project_id,))
        events = cur.fetchall()
        return {"project": project, "hypotheses": hypotheses, "events": events,
                "relations": relations,
                "next_experiment": project.get("next_experiment")}
    finally:
        cur.close()
        conn.close()


def set_next_experiment(project_id, payload, actor=None) -> bool:
    """REPLACE the project's next_experiment with the full payload the agent
    sends — ALL keys preserved (no silent drop), plus a server proposed_at. PUT
    is replace-not-merge: send the complete object each time."""
    if not isinstance(payload, dict):
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        stored = dict(payload)
        stored["proposed_at"] = _now_iso()
        cur.execute(
            "UPDATE hyp_projects SET next_experiment=%s, updated_at=NOW() "
            "WHERE project_id=%s",
            (json.dumps(stored), project_id))
        if cur.rowcount == 0:
            return False
        desc = payload.get("descriptor") or payload.get("title") or "experiment"
        _append_event(cur, project_id, "next_experiment_proposed",
                      f"Next experiment proposed: {desc} "
                      f"({payload.get('method', '')} @ {payload.get('facility', '')})",
                      detail=payload.get("rationale"), actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# --- Hypotheses ------------------------------------------------------------

def create_hypothesis(project_id, statement, *, label=None, hypothesis_type=None,
                      mechanism=None, origin=None, created_by=None) -> str | None:
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM hyp_projects WHERE project_id=%s", (project_id,))
        if cur.fetchone() is None:
            return None
        hypothesis_id = new_ulid()
        cur.execute(
            """INSERT INTO hyp_hypotheses
                 (hypothesis_id, project_id, label, statement, hypothesis_type,
                  mechanism, origin, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (hypothesis_id, project_id, label, statement, hypothesis_type,
             json.dumps(mechanism) if mechanism is not None else None,
             json.dumps(origin) if origin is not None else None, created_by))
        _append_event(cur, project_id, "hypothesis_created",
                      f"Hypothesis {label or ''} added: {statement[:120]}",
                      hypothesis_id=hypothesis_id, actor=created_by)
        cur.execute("UPDATE hyp_projects SET updated_at=NOW() WHERE project_id=%s",
                    (project_id,))
        conn.commit()
        return hypothesis_id
    finally:
        cur.close()
        conn.close()


def update_hypothesis(hypothesis_id, *, status=None, confidence=None,
                      confidence_basis=None, actor=None) -> bool:
    sets, vals = [], []
    if status is not None:
        sets.append("status=%s"); vals.append(status)
    if confidence is not None:
        sets.append("confidence=%s"); vals.append(confidence)
    if confidence_basis is not None:
        sets.append("confidence_basis=%s"); vals.append(confidence_basis)
    if not sets:
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        project_id = _project_of_hypothesis(cur, hypothesis_id)
        if project_id is None:
            return False
        vals.append(hypothesis_id)
        cur.execute(
            f"UPDATE hyp_hypotheses SET {', '.join(sets)}, updated_at=NOW() "
            f"WHERE hypothesis_id=%s", vals)
        bits = []
        if status is not None:
            bits.append(f"status → {status}")
        if confidence is not None:
            bits.append(f"confidence → {confidence:.2f}")
        _append_event(cur, project_id, "status_changed",
                      f"Hypothesis updated: {', '.join(bits)}",
                      detail=confidence_basis, hypothesis_id=hypothesis_id, actor=actor)
        cur.execute("UPDATE hyp_projects SET updated_at=NOW() WHERE project_id=%s",
                    (project_id,))
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# --- Predictions -----------------------------------------------------------

def create_prediction(hypothesis_id, descriptor_name, *, label=None, direction=None,
                      reference_condition=None, magnitude=None, output_quantity=None,
                      falsification_criterion=None, discriminates=None,
                      actor=None) -> str | None:
    conn = _conn()
    cur = conn.cursor()
    try:
        project_id = _project_of_hypothesis(cur, hypothesis_id)
        if project_id is None:
            return None
        prediction_id = new_ulid()
        cur.execute(
            """INSERT INTO hyp_predictions
                 (prediction_id, hypothesis_id, label, descriptor_name, direction,
                  reference_condition, magnitude, output_quantity,
                  falsification_criterion, discriminates)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (prediction_id, hypothesis_id, label, descriptor_name, direction,
             reference_condition, magnitude, output_quantity, falsification_criterion,
             json.dumps(discriminates) if discriminates is not None else None))
        _append_event(cur, project_id, "prediction_added",
                      f"Prediction added: {descriptor_name} ({direction or '?'})",
                      hypothesis_id=hypothesis_id, actor=actor)
        conn.commit()
        return prediction_id
    finally:
        cur.close()
        conn.close()


def evaluate_prediction(prediction_id, verdict, *, strength=None,
                        evidence_record_ids=None, rationale=None,
                        mlflow_run_url=None, actor=None) -> bool:
    verdict = normalize_verdict(verdict)
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT p.hypothesis_id, h.project_id, p.descriptor_name
                 FROM hyp_predictions p
                 JOIN hyp_hypotheses h ON h.hypothesis_id = p.hypothesis_id
                WHERE p.prediction_id = %s""",
            (prediction_id,))
        row = cur.fetchone()
        if row is None:
            return False
        cur.execute(
            """UPDATE hyp_predictions
                  SET verdict=%s, strength=%s, evidence_record_ids=%s,
                      rationale=%s, mlflow_run_url=%s, work_status='evaluated',
                      updated_at=NOW()
                WHERE prediction_id=%s""",
            (verdict, strength, evidence_record_ids, rationale, mlflow_run_url,
             prediction_id))
        _append_event(cur, row["project_id"], "prediction_evaluated",
                      f"Prediction evaluated: {row['descriptor_name']} → "
                      f"{verdict} ({strength or '?'})",
                      detail=rationale, hypothesis_id=row["hypothesis_id"],
                      evidence_record_ids=evidence_record_ids,
                      mlflow_run_url=mlflow_run_url, actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def set_prediction_status(prediction_id, work_status, *, mlflow_run_url=None,
                          actor=None) -> bool:
    """Advance a prediction through its workflow lifecycle (compute_submitted /
    compute_running / more_work_pending / awaiting_evidence). Use evaluate() to
    reach the terminal 'evaluated' state with a verdict."""
    if work_status not in WORK_STATUSES:
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT p.hypothesis_id, h.project_id, p.descriptor_name
                 FROM hyp_predictions p
                 JOIN hyp_hypotheses h ON h.hypothesis_id = p.hypothesis_id
                WHERE p.prediction_id = %s""", (prediction_id,))
        row = cur.fetchone()
        if row is None:
            return False
        if mlflow_run_url is not None:
            cur.execute("UPDATE hyp_predictions SET work_status=%s, "
                        "mlflow_run_url=%s, updated_at=NOW() WHERE prediction_id=%s",
                        (work_status, mlflow_run_url, prediction_id))
        else:
            cur.execute("UPDATE hyp_predictions SET work_status=%s, "
                        "updated_at=NOW() WHERE prediction_id=%s",
                        (work_status, prediction_id))
        etype = ("compute_submitted" if work_status == "compute_submitted"
                 else "compute_running" if work_status == "compute_running"
                 else "status_changed")
        _append_event(cur, row["project_id"], etype,
                      f"Prediction {row['descriptor_name']} → {work_status}",
                      hypothesis_id=row["hypothesis_id"],
                      mlflow_run_url=mlflow_run_url, actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# --- Briefing (the curated "universal truth" digest the agent reads first) --

def get_briefing(project_id, owner_identity=None) -> dict | None:
    """A compact, server-curated summary of where the project stands RIGHT NOW —
    the canonical ground truth both the human header and the agent consume.

    Deliberately NOT the full firehose: as a project grows, handing back
    everything makes an agent MORE likely to drift. This is the digest the agent
    must read at the start of each turn and reconcile its reasoning to."""
    data = get_project(project_id, owner_identity=owner_identity)
    if data is None:
        return None
    proj, hyps, events = data["project"], data["hypotheses"], data["events"]

    def _oneline(s):
        s = s or ""
        return (s.split(":", 1)[0] if ":" in s[:60] else s)[:90]

    ranking, validated, invalidated, open_q, pending_compute = [], [], [], [], []
    supported, eliminated = [], []
    matrix = []
    for h in hyps:
        ranking.append({"label": h["label"], "status": h["status"],
                        "confidence": h["confidence"], "statement": _oneline(h["statement"])})
        if h["status"] == "supported":
            supported.append(h["label"])
        elif h["status"] == "eliminated":
            eliminated.append(h["label"])
        for p in h["predictions"]:
            if p.get("discriminates"):
                matrix.append({"prediction": p.get("label") or p.get("descriptor_name"),
                               "descriptor": p.get("descriptor_name"),
                               "owner_hypothesis": h["label"],
                               "expected_by_hypothesis": p["discriminates"]})
            ws = p.get("work_status")
            item = {"hypothesis_label": h["label"], "descriptor": p.get("descriptor_name"),
                    "work_status": ws, "verdict": p.get("verdict"),
                    "mlflow_run_url": p.get("mlflow_run_url")}
            if ws == "evaluated":
                nv = normalize_verdict(p.get("verdict"))
                (validated if nv == "supports"
                 else invalidated if nv == "contradicts"
                 else open_q).append(item)
            elif ws in ("compute_submitted", "compute_running"):
                pending_compute.append(item)
            else:
                open_q.append(item)

    elements = extract_elements(proj.get("material_system"))
    ov = proj.get("evidence_overrides") or {}
    evidence_index = build_evidence_index(elements, include_ids=ov.get("include"),
                                          exclude_ids=ov.get("exclude"))

    return {
        "project_id": project_id,
        "title": proj["title"],
        "goal": proj.get("goal"),
        "material_system": proj.get("material_system"),
        "reaction": proj.get("reaction"),
        "elements": elements,
        "as_of": _now_iso(),
        "ranking": ranking,
        "settled": {"supported": supported, "eliminated": eliminated},
        "validated_predictions": validated,
        "invalidated_predictions": invalidated,
        "open_questions": open_q,
        "pending_compute": pending_compute,
        "discrimination_matrix": matrix,
        "evidence_index": _evidence_summary(evidence_index),
        "literature": "For published-evidence cross-checks (Edison/PaperQA3): "
                      "POST /portal/api/literature/search {query, job}, then poll "
                      "GET /portal/api/literature/search/{task_id}.",
        "next_experiment": proj.get("next_experiment"),
        "recent_journal": [{"event_type": e["event_type"], "summary": e["summary"],
                            "at": (e["created_at"].isoformat()
                                   if hasattr(e["created_at"], "isoformat")
                                   else str(e["created_at"]))}
                           for e in events[:8]],
        "_note": ("Authoritative current state of this project. Reconcile your "
                  "reasoning to it before acting; write every change back here — "
                  "if it is not on this dashboard, it did not happen."),
    }


def delete_project(project_id, owner_identity=None, is_admin=False) -> bool:
    """Delete a project and all its children (events, predictions, hypotheses,
    messages). Scoped to the owner unless is_admin. Discovery DB only."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT owner_identity FROM hyp_projects WHERE project_id=%s",
                    (project_id,))
        row = cur.fetchone()
        if row is None:
            return False
        if not is_admin and owner_identity is not None and \
                row["owner_identity"] != owner_identity:
            return False
        # Order matters: clear FK children before parents.
        cur.execute("""DELETE FROM hyp_compute_runs WHERE prediction_id IN
                         (SELECT prediction_id FROM hyp_predictions WHERE hypothesis_id IN
                            (SELECT hypothesis_id FROM hyp_hypotheses WHERE project_id=%s))""",
                    (project_id,))
        cur.execute("DELETE FROM hyp_predictions WHERE hypothesis_id IN "
                    "(SELECT hypothesis_id FROM hyp_hypotheses WHERE project_id=%s)",
                    (project_id,))
        cur.execute("DELETE FROM hyp_hypothesis_relations WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_events WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_messages WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_project_shares WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_hypotheses WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_projects WHERE project_id=%s", (project_id,))
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# --- Events (the agent's reasoning transcript) -----------------------------

def add_event(project_id, event_type, summary, *, detail=None, hypothesis_id=None,
              evidence_record_ids=None, mlflow_run_url=None, actor=None) -> int | None:
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM hyp_projects WHERE project_id=%s", (project_id,))
        if cur.fetchone() is None:
            return None
        eid = _append_event(cur, project_id, event_type, summary, detail=detail,
                            hypothesis_id=hypothesis_id,
                            evidence_record_ids=evidence_record_ids,
                            mlflow_run_url=mlflow_run_url, actor=actor)
        conn.commit()
        return eid
    finally:
        cur.close()
        conn.close()


# --- Hypothesis relations (the hypothesis graph) ---------------------------

def add_relation(from_hypothesis_id, to_hypothesis_id, relation_type, *,
                 note=None, actor=None) -> bool:
    relation_type = normalize_relation(relation_type)
    if relation_type not in RELATION_TYPES:
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        project_id = _project_of_hypothesis(cur, from_hypothesis_id)
        proj_to = _project_of_hypothesis(cur, to_hypothesis_id)
        if project_id is None or proj_to is None:
            return False
        cur.execute(
            """INSERT INTO hyp_hypothesis_relations
                 (project_id, from_hypothesis_id, to_hypothesis_id, relation_type, note)
               VALUES (%s,%s,%s,%s,%s)""",
            (project_id, from_hypothesis_id, to_hypothesis_id, relation_type, note))
        _append_event(cur, project_id, "status_changed",
                      f"Relation added: {relation_type}",
                      detail=note, hypothesis_id=from_hypothesis_id, actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# --- Compute runs (many per prediction; real lifecycle) --------------------

def create_compute_run(prediction_id, *, backend=None, engine=None, resource=None,
                       slurm_job_id=None, mlflow_run_url=None, status="queued",
                       params=None, metrics=None, note=None, actor=None) -> str | None:
    if status not in COMPUTE_STATUSES:
        return None
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT p.hypothesis_id, h.project_id, p.descriptor_name
                 FROM hyp_predictions p
                 JOIN hyp_hypotheses h ON h.hypothesis_id = p.hypothesis_id
                WHERE p.prediction_id = %s""", (prediction_id,))
        row = cur.fetchone()
        if row is None:
            return None
        # Idempotent on (prediction_id, slurm_job_id): a re-POST of the same job
        # UPDATES the existing run rather than duplicating it.
        if slurm_job_id:
            cur.execute("SELECT run_id FROM hyp_compute_runs "
                        "WHERE prediction_id=%s AND slurm_job_id=%s",
                        (prediction_id, slurm_job_id))
            ex = cur.fetchone()
            if ex:
                cur.execute(
                    """UPDATE hyp_compute_runs SET status=%s,
                         mlflow_run_url=COALESCE(%s, mlflow_run_url),
                         metrics=COALESCE(%s, metrics), note=COALESCE(%s, note),
                         updated_at=NOW() WHERE run_id=%s""",
                    (status, mlflow_run_url,
                     json.dumps(metrics) if metrics is not None else None,
                     note, ex["run_id"]))
                conn.commit()
                return ex["run_id"]
        run_id = new_ulid()
        cur.execute(
            """INSERT INTO hyp_compute_runs
                 (run_id, prediction_id, backend, engine, resource, slurm_job_id,
                  mlflow_run_url, status, params, metrics, note)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (run_id, prediction_id, backend, engine, resource, slurm_job_id,
             mlflow_run_url, status,
             json.dumps(params) if params is not None else None,
             json.dumps(metrics) if metrics is not None else None, note))
        _append_event(cur, row["project_id"], "compute_submitted",
                      f"Compute {backend or ''} {status} for {row['descriptor_name']}",
                      detail=note, hypothesis_id=row["hypothesis_id"],
                      mlflow_run_url=mlflow_run_url, actor=actor)
        conn.commit()
        return run_id
    finally:
        cur.close()
        conn.close()


def delete_compute_run(run_id) -> bool:
    """Delete a single compute run (e.g. a duplicate)."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM hyp_compute_runs WHERE run_id=%s RETURNING run_id",
                    (run_id,))
        ok = cur.fetchone() is not None
        conn.commit()
        return ok
    finally:
        cur.close()
        conn.close()


def update_compute_run(run_id, *, status=None, metrics=None, mlflow_run_url=None,
                       slurm_job_id=None, note=None, actor=None) -> bool:
    if status is not None and status not in COMPUTE_STATUSES:
        return False
    sets, vals = [], []
    for col, v in [("status", status), ("mlflow_run_url", mlflow_run_url),
                   ("slurm_job_id", slurm_job_id), ("note", note)]:
        if v is not None:
            sets.append(f"{col}=%s"); vals.append(v)
    if metrics is not None:
        sets.append("metrics=%s"); vals.append(json.dumps(metrics))
    if not sets:
        return False
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT r.prediction_id, p.hypothesis_id, h.project_id, p.descriptor_name
                 FROM hyp_compute_runs r
                 JOIN hyp_predictions p ON p.prediction_id = r.prediction_id
                 JOIN hyp_hypotheses h ON h.hypothesis_id = p.hypothesis_id
                WHERE r.run_id = %s""", (run_id,))
        row = cur.fetchone()
        if row is None:
            return False
        vals.append(run_id)
        cur.execute(f"UPDATE hyp_compute_runs SET {', '.join(sets)}, updated_at=NOW() "
                    f"WHERE run_id=%s", vals)
        etype = "compute_running" if status == "running" else "status_changed"
        _append_event(cur, row["project_id"], etype,
                      f"Compute run {status or 'updated'} for {row['descriptor_name']}",
                      detail=note, hypothesis_id=row["hypothesis_id"],
                      mlflow_run_url=mlflow_run_url, actor=actor)
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# --- Evidence index (descriptor-keyed, element-matched; READ-ONLY records) --
# Per the practitioner spec: candidates by composition ELEMENT (not material
# string), reaction ANNOTATED not gated, indexed BY DESCRIPTOR so "is there
# FE(CO) data?" is an exhaustive lookup, not a recall. The per-record annotation
# doubles as the methodological-compatibility ledger (output_quantity/functional).

_ELEMENTS = {
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si",
    "P", "S", "Cl", "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co",
    "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
    "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I",
    "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pt", "Au", "Hg", "Pb", "Bi", "W",
    "Re", "Os", "Ir", "Ta", "Hf",
}


def extract_elements(text) -> list:
    """Pull valid element symbols out of a material_system / formula / name."""
    if not text:
        return []
    out, seen = [], set()
    for m in re.findall(r"[A-Z][a-z]?", str(text)):
        if m in _ELEMENTS and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _elements_from_composition(comp) -> set:
    """Element symbols from composition KEYS by WHOLE-TOKEN match (split on
    non-letters), so `Cu_geometric_area_fraction` -> {Cu} but `CO_producing_metal`
    -> {} (CO is not an element symbol; case-sensitive, so it is not Co either)."""
    out = set()
    if isinstance(comp, dict):
        for k in comp:
            for tok in re.split(r"[^A-Za-z]+", k):
                if tok in _ELEMENTS:
                    out.add(tok)
    return out


def _role_from_elements(rec_elems, project_elements):
    rec, proj = set(rec_elems), set(project_elements)
    present, foreign = rec & proj, rec - proj
    if not present or foreign:
        return "analog"
    return "exact_system" if present == proj else "baseline"


def _system_role(record_text, project_elements):
    return _role_from_elements(extract_elements(record_text), project_elements)


def build_evidence_index(project_elements, *, include_ids=None, exclude_ids=None) -> dict:
    """descriptor_name -> [ {record_id, material, reaction, domain, value, unit,
    output_quantity, functional, system_role} ]. Read-only against records;
    degrades to {} on any error so it can never break the briefing."""
    if not project_elements:
        return {}
    include_ids = list(include_ids or [])
    exclude_ids = set(exclude_ids or [])
    try:
        conn = database.get_readonly_db_connection()
    except Exception:
        return {}
    cur = conn.cursor()
    try:
        cur.execute(
            """
            WITH cand AS (
              SELECT record_id, record_domain, data FROM records
              WHERE record_id = ANY(%(inc)s)
                 OR EXISTS (
                   SELECT 1 FROM unnest(%(elems)s::text[]) e
                   WHERE data->'sample'->'material'->>'formula' ~ ('(^|[^A-Za-z])'||e||'([^a-z]|$)')
                      OR data->'sample'->'material'->>'name'    ~ ('(^|[^A-Za-z])'||e||'([^a-z]|$)')
                      OR COALESCE((data->'sample'->'composition')::text,'') ~ ('"[^"]*'||e||'[^"]*"')
                 )
            )
            SELECT c.record_id,
                   c.data->'sample'->'material'->>'name'    AS material,
                   c.data->'sample'->'material'->>'formula' AS formula,
                   c.data->'sample'->'composition'          AS composition,
                   c.data->'context'->'electrochemistry'->>'reaction' AS reaction,
                   c.record_domain AS domain,
                   c.data->'computation'->'method'->>'functional' AS functional,
                   d->>'name' AS descriptor_name, d->>'value' AS value,
                   d->>'unit' AS unit, d->>'output_quantity' AS output_quantity
            FROM cand c,
                 jsonb_array_elements(COALESCE(c.data->'descriptors'->'outputs','[]'::jsonb)) o,
                 jsonb_array_elements(COALESCE(o->'descriptors','[]'::jsonb)) d
            LIMIT 4000
            """,
            {"elems": list(project_elements), "inc": include_ids})
        rows = cur.fetchall()
    except Exception as exc:
        logger.warning("evidence index query failed: %s", exc)
        return {}
    finally:
        cur.close()
        conn.close()

    index = {}
    for r in rows:
        rid = r["record_id"]
        if rid in exclude_ids:
            continue
        name = r["descriptor_name"]
        if not name:
            continue
        # Classify by the COMPOSITION element-set (whole-token) + formula (regex),
        # NOT the free-text name — "Interdigitated" must not read as Indium, and the
        # key "CO_producing_metal" must not read as C+O. Fall back to the name only
        # if a record has neither composition nor formula.
        rec_elems = set(extract_elements(r.get("formula") or ""))
        rec_elems |= _elements_from_composition(r.get("composition"))
        if not rec_elems:
            rec_elems = set(extract_elements(r.get("material") or ""))
        role = _role_from_elements(rec_elems, project_elements)
        index.setdefault(name, []).append({
            "record_id": rid, "material": r.get("material"),
            "reaction": r.get("reaction"), "domain": r.get("domain"),
            "value": r.get("value"), "unit": r.get("unit"),
            "output_quantity": r.get("output_quantity"),
            "functional": r.get("functional"),
            "system_role": role,
        })
    return index


def _evidence_summary(index) -> dict:
    """Compact per-descriptor rollup for the briefing (the full lists are served
    on demand by GET /projects/{id}/evidence)."""
    summary = {}
    for name, items in index.items():
        roles, reactions, methods = {}, set(), set()
        for it in items:
            roles[it["system_role"]] = roles.get(it["system_role"], 0) + 1
            if it.get("reaction"):
                reactions.add(it["reaction"])
            methods.add(it.get("output_quantity") or it.get("functional")
                        or ("experimental" if it.get("domain") != "simulation" else "?"))
        summary[name] = {"n": len(items), "by_role": roles,
                         "reactions": sorted(reactions),
                         "methods": sorted(m for m in methods if m)}
    return summary


def set_evidence_overrides(project_id, *, include=None, exclude=None,
                           owner_identity=None) -> bool:
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT owner_identity FROM hyp_projects WHERE project_id=%s",
                    (project_id,))
        row = cur.fetchone()
        if row is None or (owner_identity is not None
                           and row["owner_identity"] != owner_identity):
            return False
        cur.execute("UPDATE hyp_projects SET evidence_overrides=%s, updated_at=NOW() "
                    "WHERE project_id=%s",
                    (json.dumps({"include": include or [], "exclude": exclude or []}),
                     project_id))
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


def get_evidence(project_id, owner_identity=None, descriptor=None) -> dict | None:
    """Full descriptor-keyed evidence index for a project (optionally filtered to
    one descriptor) — the exhaustive lookup the agent runs when evaluating."""
    data = get_project(project_id, owner_identity=owner_identity)
    if data is None:
        return None
    proj = data["project"]
    elems = extract_elements(proj.get("material_system"))
    ov = proj.get("evidence_overrides") or {}
    index = build_evidence_index(elems, include_ids=ov.get("include"),
                                 exclude_ids=ov.get("exclude"))
    if descriptor:
        index = {descriptor: index.get(descriptor, [])}
    return {"project_id": project_id, "elements": elems, "evidence_index": index}


def get_context(project_id, owner_identity=None) -> dict | None:
    """ONE-SHOT complete context for a COLD-STARTING agent resuming a project it
    has never seen: full current state + the ENTIRE step-by-step reasoning history
    (every event, with detail, chronological — not the briefing's recent slice) +
    the curated briefing (which carries the evidence-index summary and the
    discrimination matrix). Read-access enforced via get_project."""
    data = get_project(project_id, owner_identity=owner_identity)
    if data is None:
        return None
    conn = _conn()
    cur = conn.cursor()
    try:
        # FULL history (no 200 cap), oldest -> newest, with detail.
        cur.execute("SELECT * FROM hyp_events WHERE project_id=%s "
                    "ORDER BY created_at ASC LIMIT 5000", (project_id,))
        history = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return {
        "resume_note": "Complete project context for a fresh agent. Read the full "
            "state and the entire `history` below to reconstruct how the project got "
            "here, then follow the prime directive: GET /briefing each turn, write "
            "every step back. If it isn't on the dashboard, it didn't happen.",
        "project": data["project"],
        "hypotheses": data["hypotheses"],   # each with predictions + compute_runs
        "relations": data["relations"],
        "next_experiment": data["next_experiment"],
        "n_history": len(history),
        "history": history,                 # ALL steps, chronological, full detail
        "briefing": get_briefing(project_id, owner_identity=owner_identity),
    }


# --- Provenance (READ-ONLY against the records DB) -------------------------

def resolve_record_summaries(record_ids) -> dict:
    """Map ISAAC record_ids -> a short display summary, via the records RO
    connection. Cross-DB by design: discovery stores record_ids as plain
    strings; we look them up read-only for the provenance throughline. Never
    writes, never uses the privileged connection. Degrades to {} on any issue."""
    if not record_ids:
        return {}
    try:
        conn = database.get_readonly_db_connection()
    except Exception:
        return {}
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT record_id,
                      data->'sample'->'material'->>'name' AS material,
                      data->'context'->'electrochemistry'->>'reaction' AS reaction,
                      record_domain
                 FROM records WHERE record_id = ANY(%s)""",
            (list(record_ids),))
        out = {}
        for r in cur.fetchall():
            rid = r["record_id"] if isinstance(r, dict) else r[0]
            if isinstance(r, dict):
                out[rid] = {"material": r["material"], "reaction": r["reaction"],
                            "domain": r["record_domain"]}
            else:
                out[rid] = {"material": r[1], "reaction": r[2], "domain": r[3]}
        return out
    except Exception:
        return {}
    finally:
        cur.close()
        conn.close()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
