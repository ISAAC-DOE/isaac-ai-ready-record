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
import secrets
import time

import database

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


def get_manifest() -> dict:
    """Self-describing contract: the bootstrap an agent fetches to learn how to
    operate on ISAAC discovery projects. PROVISIONAL (v0.1) — refined as the real
    reasoning loop is pinned down with the practitioners."""
    return {
        "name": "ISAAC Discovery — Agent Operating Protocol",
        "version": "0.2-provisional",
        "prime_directive": [
            "READ before you act: GET /projects/{id}/briefing at the start of every "
            "turn; treat it as authoritative current state and reconcile to it.",
            "WRITE after you act: every hypothesis, prediction, verdict, status "
            "change and compute run is an API write. If it is not on the dashboard, "
            "it did not happen — never hold project state only in your context.",
            "One project = one ground truth. Do not fork reality in your head.",
        ],
        "auth": {"scheme": "Bearer", "header": "Authorization: Bearer <token>",
                 "obtain": "portal API Keys page; user must be in an allowed group"},
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
             "purpose": "Curated ground-truth digest — READ THIS FIRST each turn."},
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
                        "slurm_job_id, mlflow_run_url, status, params, metrics}."},
            {"m": "PUT", "path": "/runs/{run_id}",
             "purpose": "Update a compute run {status, metrics, mlflow_run_url, ...}."},
            {"m": "PUT", "path": "/predictions/{id}/evaluate",
             "purpose": "Terminal: set verdict + strength + evidence + mlflow_run_url. "
                        "GATE on methodological compatibility (output_quantity / "
                        "functional / corrections) before trusting an evidence record."},
            {"m": "POST", "path": "/projects/{id}/events",
             "purpose": "Append a reasoning-transcript entry (one per step)."},
            {"m": "PUT", "path": "/projects/{id}/next_experiment",
             "purpose": "Propose the discriminating next experiment."},
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
            "mlflow_event": {"event_type": "compute_running",
                             "detail": "run_name / what_it_computed / status",
                             "mlflow_run_url": "str"},
        },
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
    """Project cards for one owner, with hypothesis count + current leader."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT p.project_id, p.title, p.goal, p.status, p.material_system,
                      p.reaction, p.updated_at,
                      COUNT(h.hypothesis_id) AS n_hypotheses
                 FROM hyp_projects p
                 LEFT JOIN hyp_hypotheses h ON h.project_id = p.project_id
                WHERE p.owner_identity = %s
                GROUP BY p.id
                ORDER BY p.updated_at DESC""",
            (owner_identity,))
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

    If owner_identity is given, returns None unless the project belongs to them
    (page-level scoping; API scoping is enforced by the caller too)."""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM hyp_projects WHERE project_id=%s", (project_id,))
        project = cur.fetchone()
        if project is None:
            return None
        if owner_identity is not None and project["owner_identity"] != owner_identity:
            return None
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


def set_next_experiment(project_id, descriptor, facility, method, rationale,
                        predicted_outcomes, actor=None) -> bool:
    conn = _conn()
    cur = conn.cursor()
    try:
        payload = {"descriptor": descriptor, "facility": facility,
                   "method": method, "rationale": rationale,
                   "predicted_outcomes": predicted_outcomes,
                   "proposed_at": _now_iso()}
        cur.execute(
            "UPDATE hyp_projects SET next_experiment=%s, updated_at=NOW() "
            "WHERE project_id=%s",
            (json.dumps(payload), project_id))
        if cur.rowcount == 0:
            return False
        _append_event(cur, project_id, "next_experiment_proposed",
                      f"Next experiment proposed: {descriptor} ({method} @ {facility})",
                      detail=rationale, actor=actor)
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
    for h in hyps:
        ranking.append({"label": h["label"], "status": h["status"],
                        "confidence": h["confidence"], "statement": _oneline(h["statement"])})
        if h["status"] == "supported":
            supported.append(h["label"])
        elif h["status"] == "eliminated":
            eliminated.append(h["label"])
        for p in h["predictions"]:
            ws = p.get("work_status")
            item = {"hypothesis_label": h["label"], "descriptor": p.get("descriptor_name"),
                    "work_status": ws, "verdict": p.get("verdict"),
                    "mlflow_run_url": p.get("mlflow_run_url")}
            if ws == "evaluated":
                (validated if p.get("verdict") == "supports"
                 else invalidated if p.get("verdict") == "contradicts"
                 else open_q).append(item)
            elif ws in ("compute_submitted", "compute_running"):
                pending_compute.append(item)
            else:
                open_q.append(item)

    return {
        "project_id": project_id,
        "title": proj["title"],
        "goal": proj.get("goal"),
        "material_system": proj.get("material_system"),
        "reaction": proj.get("reaction"),
        "as_of": _now_iso(),
        "ranking": ranking,
        "settled": {"supported": supported, "eliminated": eliminated},
        "validated_predictions": validated,
        "invalidated_predictions": invalidated,
        "open_questions": open_q,
        "pending_compute": pending_compute,
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
        cur.execute("DELETE FROM hyp_predictions WHERE hypothesis_id IN "
                    "(SELECT hypothesis_id FROM hyp_hypotheses WHERE project_id=%s)",
                    (project_id,))
        cur.execute("DELETE FROM hyp_events WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM hyp_messages WHERE project_id=%s", (project_id,))
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
