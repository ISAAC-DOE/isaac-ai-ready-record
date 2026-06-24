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
        cur.execute(
            """SELECT * FROM hyp_events WHERE project_id=%s
               ORDER BY created_at DESC LIMIT 200""",
            (project_id,))
        events = cur.fetchall()
        return {"project": project, "hypotheses": hypotheses, "events": events,
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
                      falsification_criterion=None, actor=None) -> str | None:
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
                  reference_condition, magnitude, output_quantity, falsification_criterion)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (prediction_id, hypothesis_id, label, descriptor_name, direction,
             reference_condition, magnitude, output_quantity, falsification_criterion))
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
                      rationale=%s, mlflow_run_url=%s, updated_at=NOW()
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
