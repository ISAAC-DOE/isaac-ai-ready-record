"""
ISAAC AI-Ready Record - Flask REST API
Sidecar API for the Streamlit portal, providing programmatic access
to record validation and CRUD operations.

Endpoints are served under /portal/api/ to avoid conflict with
Authentik's /api path at the domain level.

Run standalone:  python portal/api.py
Run with gunicorn:  gunicorn -b 0.0.0.0:8502 portal.api:app
"""

import os
import sys
import json
import time
import logging
import functools
from pathlib import Path

import requests as http_requests
from flask import Flask, jsonify, request, g
from flask_cors import CORS
from jsonschema import Draft202012Validator

# ---------------------------------------------------------------------------
# Ensure the portal package directory is importable so we can do `import database`
# just like app.py does when Streamlit sets the CWD to portal/.
# ---------------------------------------------------------------------------
_portal_dir = Path(__file__).resolve().parent
if str(_portal_dir) not in sys.path:
    sys.path.insert(0, str(_portal_dir))

import database  # noqa: E402  (same import style as app.py)
import ontology  # noqa: E402
import discovery  # noqa: E402  (isolated isaac_discovery data-access)
import literature  # noqa: E402  (Edison literature gateway/proxy)

# ---------------------------------------------------------------------------
# Flask app setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
# Restrict CORS to the portal origin (bearer-token API; server-to-server
# callers like migration scripts and converters ignore CORS entirely).
_ALLOWED_ORIGINS = os.environ.get(
    "ISAAC_CORS_ORIGINS", "https://isaac.slac.stanford.edu").split(",")
CORS(app, origins=[o.strip() for o in _ALLOWED_ORIGINS if o.strip()])
# Reject oversized bodies before buffering (memory-exhaustion DoS guard).
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("ISAAC_MAX_BODY_BYTES", str(5 * 1024 * 1024)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("isaac-portal-api")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PORT = int(os.environ.get("PORT", 8502))
AUTHENTIK_INTERNAL_URL = os.environ.get(
    "AUTHENTIK_INTERNAL_URL",
    "http://authentik-server.authentik.svc.cluster.local:9000",
)
ALLOWED_GROUPS = {"admin", "researcher"}
ADMIN_GROUPS = {"admin"}

# ---------------------------------------------------------------------------
# Startup: ensure DB tables exist and vocabulary cache is current
# ---------------------------------------------------------------------------
if database.is_db_configured():
    database.init_tables()
    _ok, _msg = ontology.sync_file_to_db()
    logger.info("Vocabulary sync on import: %s — %s", _ok, _msg)

# Isolated discovery DB (discovery feature) — independent of the records DB;
# a no-op when DISCOVERY_* is unset.
if database.is_discovery_db_configured():
    database.init_discovery_tables()

# In-memory token cache: token -> {"user": str, "groups": list, "expires": float}
_token_cache: dict = {}
_TOKEN_CACHE_TTL = 300  # 5 minutes

# ---------------------------------------------------------------------------
# Validation: delegated to the shared portal/validation.py module — the
# single source of truth used by ALL ingestion paths (API + Streamlit UI).
# database.save_record() re-enforces the same validation internally.
# ---------------------------------------------------------------------------
import validation  # noqa: E402  (same import style as database/ontology)

ISAAC_SCHEMA = validation.ISAAC_SCHEMA
ISAAC_VALIDATOR = validation.ISAAC_VALIDATOR

logger.info("Loaded ISAAC schema via shared validation module (%s)", validation.SCHEMA_PATH)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------
def _validate_bearer_token(token: str) -> dict | None:
    """
    Validate a Bearer token against Authentik.

    Calls GET /api/v3/core/users/me/ with the token.  Returns a dict with
    'user' (username) and 'groups' (list of group names) on success, or
    None if the token is invalid / Authentik is unreachable.
    Results are cached for 5 minutes to reduce load on Authentik.
    """
    now = time.monotonic()

    # Check cache
    cached = _token_cache.get(token)
    if cached and cached["expires"] > now:
        return {"user": cached["user"], "groups": cached["groups"]}

    # Evict expired entries (cheap linear scan — cache is small)
    expired_keys = [k for k, v in _token_cache.items() if v["expires"] <= now]
    for k in expired_keys:
        del _token_cache[k]

    try:
        resp = http_requests.get(
            f"{AUTHENTIK_INTERNAL_URL}/api/v3/core/users/me/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
    except Exception as exc:
        logger.error("Authentik token validation request failed: %s", exc)
        return None

    if resp.status_code != 200:
        logger.info("Authentik rejected token (HTTP %d)", resp.status_code)
        return None

    try:
        user_data = resp.json()
        username = user_data["user"]["username"]
        # /api/v3/core/users/me/ returns "groups" (list of {name, pk} dicts),
        # NOT "groups_obj" which only appears on the admin /users/ endpoint.
        groups = [g["name"] for g in user_data["user"].get("groups", [])]
    except (KeyError, TypeError, ValueError):
        logger.warning("Unexpected Authentik /users/me/ response: %s", resp.text[:200])
        return None

    _token_cache[token] = {"user": username, "groups": groups, "expires": now + _TOKEN_CACHE_TTL}
    return {"user": username, "groups": groups}


def _get_auth_info():
    """
    Extract and validate authentication from the request.

    Validates Bearer tokens against Authentik's /api/v3/core/users/me/.
    Returns a dict with 'method' and 'user', or None if unauthenticated.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        token_info = _validate_bearer_token(token)
        if token_info:
            if any(g in ALLOWED_GROUPS for g in token_info["groups"]):
                return {"method": "bearer_token", "user": token_info["user"]}
            logger.warning(
                "Token valid for user %s but groups %s not in %s",
                token_info["user"], token_info["groups"], ALLOWED_GROUPS,
            )
            # Return a special marker so _require_auth can return 403 vs 401
            return {"method": "bearer_token", "user": token_info["user"], "forbidden": True}
        # Token present but invalid — return None so _require_auth rejects it
        return None

    return None


@app.before_request
def _usage_clock_start():
    g.usage_t0 = time.time()
    g.usage_user = None


@app.after_request
def _usage_log(response):
    """Persist API usage (Dimos dashboard, 2026-06-14). Never breaks a request."""
    try:
        path = request.url_rule.rule if request.url_rule else request.path
        if path.startswith("/portal/api") and not path.endswith("/health"):
            dur = (time.time() - getattr(g, "usage_t0", time.time())) * 1000.0
            # Real client IP behind the ingress/proxy: first hop of
            # X-Forwarded-For, else the direct peer.
            xff = request.headers.get("X-Forwarded-For", "")
            client_ip = (xff.split(",")[0].strip() if xff else None) or request.remote_addr
            database.log_api_request(getattr(g, "usage_user", None), request.method,
                                      path, response.status_code, round(dur, 1), ip=client_ip)
    except Exception:
        pass
    return response


def _log_request(auth_info):
    """Log incoming request with auth context."""
    if auth_info:
        logger.info(
            "%s %s [auth=%s user=%s]",
            request.method,
            request.path,
            auth_info.get("method"),
            auth_info.get("user"),
        )
        g.usage_user = auth_info.get("user")
    else:
        logger.info("%s %s [unauthenticated]", request.method, request.path)


def _require_auth(fn):
    """Decorator that enforces authentication on an endpoint."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        auth_info = _get_auth_info()
        _log_request(auth_info)
        if auth_info is None:
            return jsonify({
                "error": "authentication_required",
                "message": (
                    "Provide a valid Bearer token in the Authorization header. "
                    "Generate one from the API Keys page in the ISAAC Portal."
                ),
            }), 401
        if auth_info.get("forbidden"):
            return jsonify({
                "error": "insufficient_permissions",
                "message": "Your account is not in an authorized group. Contact an administrator.",
            }), 403
        request.auth_info = auth_info
        return fn(*args, **kwargs)
    return wrapper


def _caller_is_admin() -> bool:
    """True if the request's Bearer token belongs to an admin group."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    info = _validate_bearer_token(auth[7:])
    return bool(info and any(g in ADMIN_GROUPS for g in info.get("groups", [])))


def _require_admin(fn):
    """Decorator that enforces admin-group membership."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        auth_info = _get_auth_info()
        _log_request(auth_info)
        if auth_info is None:
            return jsonify({
                "error": "authentication_required",
                "message": "Provide a valid Bearer token in the Authorization header.",
            }), 401
        if auth_info.get("forbidden"):
            return jsonify({
                "error": "insufficient_permissions",
                "message": "Your account is not in an authorized group.",
            }), 403
        # Check admin group
        token = request.headers.get("Authorization", "")[7:]
        token_info = _validate_bearer_token(token)
        if not token_info or not any(g in ADMIN_GROUPS for g in token_info["groups"]):
            return jsonify({
                "error": "admin_required",
                "message": "This action requires admin privileges.",
            }), 403
        request.auth_info = auth_info
        return fn(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------
# Validation helpers now live in portal/validation.py (shared by every
# ingestion path). These thin wrappers are kept for backward compatibility
# with any external callers of the module-level functions.

def _validate_record(data: dict) -> list:
    """Schema layer only — delegates to the shared validation module."""
    return validation.validate_record_full(data)["schema_errors"]


def _validate_semantic_integrity(data: dict) -> list:
    """Semantic layer only — delegates to the shared validation module."""
    return validation.validate_record_full(data)["semantic_errors"]


def _validate_vocabulary(data: dict) -> list:
    """Vocabulary layer only — delegates to the shared validation module."""
    return validation.validate_record_full(data)["vocabulary_errors"]


# ===========================================================================
# Endpoints
# ===========================================================================

# --- Health check ----------------------------------------------------------

@app.route("/portal/api/health", methods=["GET"])
def health():
    """Health check for Kubernetes liveness/readiness probes."""
    return jsonify({"status": "healthy", "service": "isaac-portal-api"})


# --- Combined schema (base + vocabulary enums) ----------------------------

@app.route("/portal/api/schema", methods=["GET"])
def get_schema():
    """
    Return the ISAAC record JSON Schema with vocabulary enum
    constraints merged in.

    This is a public endpoint (no auth required) so that clients
    can fetch the authoritative schema and validate locally.
    """
    merged = ontology.merge_vocabulary_into_schema(ISAAC_SCHEMA)
    return jsonify(merged), 200


# --- Ontology / vocabulary -------------------------------------------------

@app.route("/portal/api/ontology", methods=["GET"])
def get_ontology():
    """
    Return the live ontology/vocabulary as a JSON dict.

    Structure: { section: { category_key: { description, values } } }

    Optional query param:
      ?section=Sample   — return only the named section.
    """
    vocab = ontology.load_vocabulary()

    section = request.args.get("section")
    if section:
        if section not in vocab:
            return jsonify({
                "error": f"Unknown section: {section}",
                "available_sections": list(vocab.keys()),
            }), 404
        return jsonify({section: vocab[section]}), 200

    return jsonify(vocab), 200


# --- Validate (dry-run, no DB write) --------------------------------------

@app.route("/portal/api/validate", methods=["POST"])
@_require_auth
def validate():
    """
    Validate a JSON body against the ISAAC record schema.
    Does NOT persist anything to the database.
    """

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({
            "valid": False,
            "errors": [{"path": "(root)", "message": "Request body is not valid JSON"}],
        }), 400

    # One call to the shared validation module — identical result shape.
    return jsonify(validation.validate_record_full(data)), 200


# --- Create record ---------------------------------------------------------

@app.route("/portal/api/records", methods=["POST"])
@_require_auth
def create_record():
    """
    Validate and persist a new ISAAC record.
    """

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({
            "success": False,
            "reason": "invalid_json",
            "message": "Request body is not valid JSON",
        }), 400

    # Schema + vocabulary + semantic validation (shared module, one call)
    result = validation.validate_record_full(data)
    if not result["valid"]:
        return jsonify({
            "success": False,
            "reason": "validation_failed",
            "schema_errors": result["schema_errors"],
            "vocabulary_errors": result["vocabulary_errors"],
            "semantic_errors": result["semantic_errors"],
            "errors": result["errors"],
        }), 400

    # Persist via shared database module (save_record re-validates
    # internally — the chokepoint guarantee — at negligible cost).
    try:
        # Attribution: stamped inside the chokepoint from the authenticated identity.
        auth_info = _get_auth_info()
        caller = (auth_info or {}).get("user")
        # POST is INSERT-only: a caller may NOT overwrite an existing record by
        # supplying its id (use PUT to edit your OWN record). Admins may opt into
        # upsert with ?allow_update=true for ingestion/migration tooling.
        allow_update = _caller_is_admin() and request.args.get("allow_update") == "true"
        record_id = database.save_record(
            data, uploaded_by=caller, mode=("upsert" if allow_update else "insert"))
        resp = {"success": True, "record_id": record_id}
        # Warnings tier: accepted-but-improvable feedback travels with the 201
        if result.get("warnings"):
            resp["warnings"] = result["warnings"]
        if result.get("info"):
            resp["info"] = result["info"]
        return jsonify(resp), 201
    except database.RecordExistsError:
        return jsonify({
            "success": False,
            "reason": "record_exists",
            "message": f"Record {data.get('record_id')} already exists. You cannot overwrite "
                       f"it via POST. To edit a record you submitted, use PUT "
                       f"/portal/api/records/<record_id>.",
        }), 409
    except validation.ValidationError as ve:
        # Unreachable unless validation rules changed between the check
        # above and the save; report identically to the pre-save failure.
        return jsonify({
            "success": False,
            "reason": "validation_failed",
            **ve.result,
        }), 400
    except ValueError as ve:
        # Missing required fields that passed schema but failed DB check
        return jsonify({
            "success": False,
            "reason": "validation_failed",
            "errors": [{"path": "(root)", "message": str(ve)}],
        }), 400
    except Exception as exc:
        logger.exception("Database error saving record")
        return jsonify({
            "success": False,
            "reason": "database_error",
            "message": str(exc),
        }), 500


# --- List records ----------------------------------------------------------

_LIST_PARAMS = {"limit", "offset", "record_type", "record_domain", "reaction",
                "material_contains", "created_after", "created_before", "full"}


@app.route("/portal/api/records", methods=["GET"])
@_require_auth
def list_records():
    """
    List records with optional server-side filters.

    Query params: limit, offset, record_type, record_domain, reaction,
    material_contains, created_after, created_before, full=true.
    Unknown params are REJECTED (400) — silently ignoring filters made
    clients believe they had filtered when they had not.
    Response: JSON list (backward compatible); X-Total-Count header
    carries the total matching count for pagination.
    """
    unknown = set(request.args.keys()) - _LIST_PARAMS
    if unknown:
        return jsonify({"error": f"Unknown query parameter(s): {sorted(unknown)}. "
                                 f"Supported: {sorted(_LIST_PARAMS)}"}), 400
    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "limit and offset must be integers"}), 400

    full = request.args.get("full", "").lower() == "true"
    if full:
        limit = min(limit, 50)  # full records are heavy; cap the page size

    filters = {k: request.args.get(k) for k in
               ("record_type", "record_domain", "reaction", "material_contains",
                "created_after", "created_before") if request.args.get(k)}
    try:
        rows, total = database.list_records(limit=limit, offset=offset,
                                            filters=filters, full=full)
        resp = jsonify(rows)
        resp.headers["X-Total-Count"] = str(total)
        return resp, 200
    except Exception as exc:
        logger.exception("Database error listing records")
        return jsonify({"error": "internal server error"}), 500


@app.route("/portal/api/records/batch", methods=["POST"])
@_require_auth
def records_batch():
    """Bulk hydration: {"record_ids": [...]} -> full records (max 200 per call)."""
    body = request.get_json(silent=True) or {}
    ids = body.get("record_ids")
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "Body must be {\"record_ids\": [<ulid>, ...]}"}), 400
    if len(ids) > 200:
        return jsonify({"error": "Max 200 record_ids per call"}), 400
    try:
        records = database.get_records_batch(ids)
        return jsonify({"records": records, "requested": len(ids),
                        "returned": len(records)}), 200
    except Exception as exc:
        logger.exception("Database error in batch fetch")
        return jsonify({"error": "internal server error"}), 500


@app.route("/portal/api/records/query", methods=["POST"])
@_require_admin
def records_query():
    """
    Guarded read-only SQL: {"sql": "SELECT ...", "max_rows": 100}.
    SELECT/WITH only, statement timeout, row cap 500 — delegates to
    database.execute_readonly_query. The records table schema:
    records(record_id CHAR(26), record_type, record_domain, data JSONB,
    created_at). JSONB paths: data->'context'->'electrochemistry'->>'reaction' etc.
    """
    body = request.get_json(silent=True) or {}
    sql = body.get("sql", "")
    max_rows = min(int(body.get("max_rows", 100)), 500)
    if not sql.strip():
        return jsonify({"error": "Body must include non-empty 'sql'"}), 400
    try:
        rows = database.execute_readonly_query(sql, max_rows=max_rows)
        return jsonify({"rows": rows, "row_count": len(rows),
                        "truncated_at": max_rows if len(rows) >= max_rows else None}), 200
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as exc:
        logger.exception("Read-only query failed")
        return jsonify({"error": "internal server error"}), 500


@app.route("/portal/api/records/<record_id>/quality", methods=["GET"])
@_require_auth
def record_quality(record_id):
    """
    Recompute the CURRENT validation report (errors + warnings + info)
    for a stored record. Warnings are deterministic functions of the
    record, so they are never stale and never lost — pipelines that
    ignored the 201 response can be audited here any time.
    """
    try:
        record = database.get_record(record_id)
    except Exception as exc:
        return jsonify({"error": "internal server error"}), 500
    if record is None:
        return jsonify({"error": "Record not found"}), 404
    result = validation.validate_record_full(record)
    return jsonify({"record_id": record_id, **result}), 200


@app.route("/portal/api/records/<record_id>/suggestions", methods=["GET"])
@_require_auth
def record_suggestions(record_id):
    """
    Cross-record fine-tuning suggestions for a VALID record (Dimos, 2026-06-14:
    'passing the validation but maybe can be fine-tuned'). Unlike /quality
    (deterministic per-record warnings), these rules look across the database:
    kinship candidates, derivable values, family-norm gaps.
    """
    try:
        record = database.get_record(record_id)
    except Exception as exc:
        return jsonify({"error": "internal server error"}), 500
    if record is None:
        return jsonify({"error": "Record not found"}), 404

    suggestions = []
    ec = ((record.get("context") or {}).get("electrochemistry") or {})
    mat_name = ((record.get("sample") or {}).get("material") or {}).get("name")
    sample_id = (record.get("sample") or {}).get("sample_id")

    # S1: kinship — other records of the same material, unlinked
    if mat_name:
        try:
            kin = database.find_records_by_material(mat_name, record_id)
        except Exception:
            kin = []
        linked = {l.get("target") for l in record.get("links") or []}
        unlinked = [k for k in kin if k and k not in linked]
        if unlinked:
            suggestions.append({
                "code": "KINSHIP_CANDIDATES",
                "message": f"{len(unlinked)} other record(s) measure material '{mat_name}' but are not linked: "
                           f"{unlinked[:4]}. If the same physical sample, share a sample.sample_id and add "
                           f"same_sample_as links; if comparable conditions, consider intended_comparison_target.",
            })
    if not sample_id and mat_name:
        suggestions.append({
            "code": "NO_SAMPLE_ID",
            "message": "sample.sample_id is unset. A stable physical-sample identifier is what makes "
                       "same_sample_as links meaningful across records.",
        })

    # S2: derivable RHE value
    pvr = ec.get("potential_vs_RHE") or {}
    ref = ec.get("reference_electrode") or {}
    if (not pvr or pvr.get("value_V") is None) and             isinstance(ec.get("potential_setpoint_V"), (int, float)) and             isinstance(ref.get("offset_V_vs_SHE"), (int, float)) and             isinstance(ec.get("pH"), (int, float)) and ec.get("potential_scale") not in ("RHE", None):
        e_rhe = ec["potential_setpoint_V"] + ref["offset_V_vs_SHE"] + 0.0591 * ec["pH"]
        suggestions.append({
            "code": "RHE_DERIVABLE",
            "message": f"potential_vs_RHE is empty but derivable: {ec['potential_setpoint_V']} + "
                       f"{ref['offset_V_vs_SHE']} + 0.0591*{ec['pH']} = {e_rhe:.3f} V_RHE "
                       f"(rhe_basis: derived_nominal). Populating it puts this record on the canonical query axis.",
        })

    # S3: computable partial current densities
    has_fe = has_pcd = False
    j_total = None
    for o in (record.get("descriptors") or {}).get("outputs") or []:
        for d in o.get("descriptors") or [] if isinstance(o, dict) else []:
            nm = d.get("name") or ""
            if nm.startswith("faradaic_efficiency."):
                has_fe = True
            if nm.startswith("partial_current_density."):
                has_pcd = True
            if nm == "steady_state_current_density" and isinstance(d.get("value"), (int, float)):
                j_total = d["value"]
    if j_total is None and isinstance(ec.get("current_setpoint_mA_cm2"), (int, float)):
        j_total = ec["current_setpoint_mA_cm2"]
    if has_fe and not has_pcd and isinstance(j_total, (int, float)):
        suggestions.append({
            "code": "PCD_COMPUTABLE",
            "message": f"FE descriptors exist and total current density is known ({j_total} mA/cm2): "
                       f"partial_current_density.{{product}} = FE x j_total are computable, queryable "
                       f"additions (signed, IUPAC).",
        })

    return jsonify({"record_id": record_id, "suggestions": suggestions,
                    "note": "Suggestions are advisory fine-tuning hints; the record is unchanged."}), 200


@app.route("/portal/api/usage/summary", methods=["GET"])
@_require_admin
def usage_summary():
    """API usage aggregates (?days=30): daily series, by user, by endpoint."""
    try:
        days = min(int(request.args.get("days", 30)), 365)
        return jsonify(database.get_api_usage_stats(days)), 200
    except Exception as exc:
        logger.exception("usage summary failed")
        return jsonify({"error": "internal server error"}), 500


@app.route("/portal/api/quality/summary", methods=["GET"])
@_require_admin
def quality_summary():
    """
    Database-wide curation dashboard: counts of records by warning/info
    code, plus validator pass/fail totals. Recomputed live (expensive —
    seconds for thousands of records); intended for curators and agents,
    not hot paths.
    """
    try:
        rows, total = database.list_records(limit=100000, offset=0, full=True)
    except Exception as exc:
        return jsonify({"error": "internal server error"}), 500
    from collections import Counter
    warn_counts, info_counts = Counter(), Counter()
    fail = 0
    for rec in rows:
        result = validation.validate_record_full(rec)
        if not result["valid"]:
            fail += 1
        for w in result.get("warnings", []):
            warn_counts[w["code"]] += 1
        for i in result.get("info", []):
            info_counts[i["code"]] += 1
    return jsonify({
        "total_records": total,
        "validator_failing": fail,
        "validator_passing": total - fail,
        "warning_counts": dict(warn_counts.most_common()),
        "info_counts": dict(info_counts.most_common()),
    }), 200


# --- Get single record -----------------------------------------------------

@app.route("/portal/api/records/<record_id>", methods=["GET"])
@_require_auth
def get_record(record_id):
    """
    Retrieve the full JSON for a single record by its ULID.
    """

    try:
        record = database.get_record(record_id)
    except Exception as exc:
        logger.exception("Database error fetching record %s", record_id)
        return jsonify({"error": "internal server error"}), 500

    if record is None:
        return jsonify({"error": "Record not found"}), 404

    return jsonify(record), 200


# --- Edit a record you submitted (owner or admin) -------------------------

@app.route("/portal/api/records/<record_id>", methods=["PUT"])
@_require_auth
def update_record(record_id):
    """
    Update an EXISTING record. Authorization: the caller must be the record's
    uploaded_by (the submitter) OR an admin. Prior content is archived to
    record_history first. This is the ONLY way a non-admin may change a stored
    record, and only their own — never anyone else's, and never a delete.
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"success": False, "reason": "invalid_json",
                        "message": "Request body is not valid JSON"}), 400

    caller = (_get_auth_info() or {}).get("user")
    try:
        existing = database.get_record(record_id)
    except Exception:
        logger.exception("Database error loading record %s", record_id)
        return jsonify({"error": "internal server error"}), 500
    if existing is None:
        return jsonify({"success": False, "reason": "not_found",
                        "message": "Record not found. Use POST to create a new record."}), 404

    owner = (existing.get("attribution") or {}).get("uploaded_by")
    if not _caller_is_admin() and (owner is None or owner != caller):
        return jsonify({
            "success": False, "reason": "forbidden",
            "message": "You may only edit records you submitted. This record is owned by "
                       f"'{owner or 'unknown'}'.",
        }), 403

    # Force the path id; preserve the ORIGINAL owner (an edit does not transfer
    # ownership). Validation happens inside save_record (the chokepoint).
    data["record_id"] = record_id
    result = validation.validate_record_full(data)
    if not result["valid"]:
        return jsonify({"success": False, "reason": "validation_failed",
                        "schema_errors": result["schema_errors"],
                        "vocabulary_errors": result["vocabulary_errors"],
                        "semantic_errors": result["semantic_errors"],
                        "errors": result["errors"]}), 400

    database.archive_record(record_id, existing, "update", caller)
    try:
        database.save_record(data, uploaded_by=owner, mode="update")
    except database.RecordNotFoundError:
        return jsonify({"success": False, "reason": "not_found"}), 404
    except Exception:
        logger.exception("Database error updating record %s", record_id)
        return jsonify({"error": "internal server error"}), 500

    logger.info("Record %s updated by %s (owner %s)", record_id, caller, owner)
    resp = {"success": True, "record_id": record_id, "updated": True}
    if result.get("warnings"):
        resp["warnings"] = result["warnings"]
    return jsonify(resp), 200


# --- Delete record (admin only) -------------------------------------------

@app.route("/portal/api/records/<record_id>", methods=["DELETE"])
@_require_admin
def delete_record(record_id):
    """
    Delete a record by its ULID. Requires admin privileges. Regular users
    (including a record's own submitter) CANNOT delete — only edit via PUT.
    Prior content is archived to record_history.
    """

    try:
        deleted = database.delete_record(record_id, actor=request.auth_info.get("user"))
    except Exception as exc:
        logger.exception("Database error deleting record %s", record_id)
        return jsonify({"error": "internal server error"}), 500

    if not deleted:
        return jsonify({"error": "Record not found"}), 404

    logger.info("Record %s deleted by %s", record_id, request.auth_info.get("user"))
    return jsonify({"success": True, "record_id": record_id, "deleted": True}), 200


# ===========================================================================
# Discovery endpoints (hypothesis-driven reasoning workbench)
# ===========================================================================
# All under /portal/api/, reuse @_require_auth (Bearer -> Authentik -> group
# gate). Identity is server-stamped from the validated username; any
# client-supplied identity is ignored. Writes go ONLY to the isolated
# isaac_discovery DB via discovery.* — never the records DB. Cross-DB FKs do not
# exist; evidence_record_ids are plain strings into the records DB.

def _disc_identity():
    return (request.auth_info or {}).get("user")


@app.route("/portal/api/literature/search", methods=["POST"])
@_require_auth
def literature_search():
    """Literature gateway: submit a cited-literature query (Edison/PaperQA3). The
    portal holds the Edison key server-side; agents use their normal portal token.
    Async — returns a task_id; poll GET /literature/search/{task_id}."""
    if not literature.is_configured():
        return jsonify({"error": "literature gateway not configured "
                                 "(EDISON_PLATFORM_API_KEY unset on the server)"}), 503
    d = request.get_json(silent=True) or {}
    if not d.get("query"):
        return jsonify({"error": "query is required"}), 400
    try:
        task_id = literature.submit(d["query"], d.get("job", "literature"))
    except Exception as exc:
        logger.exception("Edison submit failed")
        return jsonify({"error": f"literature submit failed: {exc}"}), 502
    return jsonify({"task_id": task_id, "status": "submitted",
                    "poll": f"/portal/api/literature/search/{task_id}"}), 202


@app.route("/portal/api/literature/search/<task_id>", methods=["GET"])
@_require_auth
def literature_poll(task_id):
    if not literature.is_configured():
        return jsonify({"error": "literature gateway not configured"}), 503
    try:
        return jsonify(literature.poll(task_id)), 200
    except Exception as exc:
        logger.exception("Edison poll failed")
        return jsonify({"error": f"literature poll failed: {exc}"}), 502


@app.route("/portal/api/discovery/manifest", methods=["GET"])
def discovery_manifest():
    """Public, no-auth bootstrap: how to operate on ISAAC discovery projects.
    An agent's FIRST call — it learns the protocol, state machines, endpoints and
    field shapes here, so the platform is self-describing rather than relying on a
    human to paste a spec."""
    return jsonify(discovery.get_manifest()), 200


@app.route("/portal/api/projects", methods=["POST"])
@_require_auth
def discovery_create_project():
    d = request.get_json(silent=True) or {}
    if not d.get("title"):
        return jsonify({"error": "title is required"}), 400
    pid = discovery.create_project(
        _disc_identity(), d["title"], goal=d.get("goal"),
        material_system=d.get("material_system"), reaction=d.get("reaction"))
    return jsonify({"project_id": pid}), 201


@app.route("/portal/api/projects", methods=["GET"])
@_require_auth
def discovery_list_projects():
    return jsonify(discovery.list_projects(_disc_identity())), 200


@app.route("/portal/api/projects/<project_id>", methods=["GET"])
@_require_auth
def discovery_get_project(project_id):
    proj = discovery.get_project(project_id, owner_identity=_disc_identity())
    if proj is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(proj), 200


@app.route("/portal/api/projects/<project_id>/briefing", methods=["GET"])
@_require_auth
def discovery_briefing(project_id):
    """The curated 'universal truth' digest the agent should read at the start of
    every turn and reconcile to. Compact by design."""
    brief = discovery.get_briefing(project_id, owner_identity=_disc_identity())
    if brief is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(brief), 200


@app.route("/portal/api/projects/<project_id>/evidence", methods=["GET"])
@_require_auth
def discovery_evidence(project_id):
    """Exhaustive descriptor-keyed evidence index (element-matched candidates,
    reaction annotated). ?descriptor=<name> narrows to one — the lookup the agent
    runs when evaluating a prediction so it never reasons 'no data' from memory."""
    ev = discovery.get_evidence(project_id, owner_identity=_disc_identity(),
                                descriptor=request.args.get("descriptor"))
    if ev is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(ev), 200


@app.route("/portal/api/projects/<project_id>/evidence_overrides", methods=["PUT"])
@_require_auth
def discovery_evidence_overrides(project_id):
    d = request.get_json(silent=True) or {}
    ok = discovery.set_evidence_overrides(
        project_id, include=d.get("include"), exclude=d.get("exclude"),
        owner_identity=_disc_identity())
    if not ok:
        return jsonify({"error": "not found or not yours"}), 404
    return jsonify({"ok": True}), 200


@app.route("/portal/api/projects/<project_id>", methods=["DELETE"])
@_require_auth
def discovery_delete_project(project_id):
    ok = discovery.delete_project(
        project_id, owner_identity=_disc_identity(), is_admin=_caller_is_admin())
    if not ok:
        return jsonify({"error": "not found or not yours"}), 404
    return jsonify({"ok": True, "deleted": project_id}), 200


@app.route("/portal/api/predictions/<prediction_id>/status", methods=["PUT"])
@_require_auth
def discovery_set_prediction_status(prediction_id):
    d = request.get_json(silent=True) or {}
    ws = d.get("work_status")
    if ws not in discovery.WORK_STATUSES:
        return jsonify({"error": f"work_status must be one of "
                                 f"{sorted(discovery.WORK_STATUSES)}"}), 400
    ok = discovery.set_prediction_status(
        prediction_id, ws, mlflow_run_url=d.get("mlflow_run_url"),
        actor=_disc_identity())
    if not ok:
        return jsonify({"error": "prediction not found"}), 404
    return jsonify({"ok": True}), 200


@app.route("/portal/api/projects/<project_id>/hypotheses", methods=["POST"])
@_require_auth
def discovery_create_hypothesis(project_id):
    d = request.get_json(silent=True) or {}
    if not d.get("statement"):
        return jsonify({"error": "statement is required"}), 400
    hid = discovery.create_hypothesis(
        project_id, d["statement"], label=d.get("label"),
        hypothesis_type=d.get("hypothesis_type"), mechanism=d.get("mechanism"),
        origin=d.get("origin"), created_by=_disc_identity())
    if hid is None:
        return jsonify({"error": "project not found"}), 404
    return jsonify({"hypothesis_id": hid}), 201


@app.route("/portal/api/hypotheses/<hypothesis_id>", methods=["PUT"])
@_require_auth
def discovery_update_hypothesis(hypothesis_id):
    d = request.get_json(silent=True) or {}
    ok = discovery.update_hypothesis(
        hypothesis_id, status=d.get("status"), confidence=d.get("confidence"),
        confidence_basis=d.get("confidence_basis"), actor=_disc_identity())
    if not ok:
        return jsonify({"error": "not found or no fields to update"}), 404
    return jsonify({"ok": True}), 200


@app.route("/portal/api/hypotheses/<hypothesis_id>/predictions", methods=["POST"])
@_require_auth
def discovery_create_prediction(hypothesis_id):
    d = request.get_json(silent=True) or {}
    if not d.get("descriptor_name"):
        return jsonify({"error": "descriptor_name is required"}), 400
    pid = discovery.create_prediction(
        hypothesis_id, d["descriptor_name"], label=d.get("label"),
        direction=d.get("direction"), reference_condition=d.get("reference_condition"),
        magnitude=d.get("magnitude"), output_quantity=d.get("output_quantity"),
        falsification_criterion=d.get("falsification_criterion"),
        discriminates=d.get("discriminates"), actor=_disc_identity())
    if pid is None:
        return jsonify({"error": "hypothesis not found"}), 404
    return jsonify({"prediction_id": pid}), 201


@app.route("/portal/api/hypotheses/<hypothesis_id>/relations", methods=["POST"])
@_require_auth
def discovery_add_relation(hypothesis_id):
    d = request.get_json(silent=True) or {}
    rel = discovery.normalize_relation(d.get("relation_type"))
    if not d.get("to_hypothesis_id") or rel not in discovery.RELATION_TYPES:
        return jsonify({"error": f"to_hypothesis_id required; relation_type one of "
                                 f"{sorted(discovery.RELATION_TYPES)} "
                                 f"(synonyms like co_operates_with are accepted)"}), 400
    ok = discovery.add_relation(hypothesis_id, d["to_hypothesis_id"],
                                rel, note=d.get("note"), actor=_disc_identity())
    if not ok:
        return jsonify({"error": "hypothesis not found"}), 404
    return jsonify({"ok": True}), 201


@app.route("/portal/api/predictions/<prediction_id>/runs", methods=["POST"])
@_require_auth
def discovery_create_run(prediction_id):
    d = request.get_json(silent=True) or {}
    rid = discovery.create_compute_run(
        prediction_id, backend=d.get("backend"), engine=d.get("engine"),
        resource=d.get("resource"), slurm_job_id=d.get("slurm_job_id"),
        mlflow_run_url=d.get("mlflow_run_url"), status=d.get("status", "queued"),
        params=d.get("params"), metrics=d.get("metrics"), note=d.get("note"),
        actor=_disc_identity())
    if rid is None:
        return jsonify({"error": "prediction not found or invalid status"}), 404
    return jsonify({"run_id": rid}), 201


@app.route("/portal/api/runs/<run_id>", methods=["DELETE"])
@_require_auth
def discovery_delete_run(run_id):
    if not discovery.delete_compute_run(run_id):
        return jsonify({"error": "run not found"}), 404
    return jsonify({"ok": True, "deleted": run_id}), 200


@app.route("/portal/api/runs/<run_id>", methods=["PUT"])
@_require_auth
def discovery_update_run(run_id):
    d = request.get_json(silent=True) or {}
    ok = discovery.update_compute_run(
        run_id, status=d.get("status"), metrics=d.get("metrics"),
        mlflow_run_url=d.get("mlflow_run_url"), slurm_job_id=d.get("slurm_job_id"),
        note=d.get("note"), actor=_disc_identity())
    if not ok:
        return jsonify({"error": "run not found or no valid fields"}), 404
    return jsonify({"ok": True}), 200


@app.route("/portal/api/predictions/<prediction_id>/evaluate", methods=["PUT"])
@_require_auth
def discovery_evaluate_prediction(prediction_id):
    d = request.get_json(silent=True) or {}
    if not d.get("verdict"):
        return jsonify({"error": "verdict is required"}), 400
    ok = discovery.evaluate_prediction(
        prediction_id, d["verdict"], strength=d.get("strength"),
        evidence_record_ids=d.get("evidence_record_ids"), rationale=d.get("rationale"),
        mlflow_run_url=d.get("mlflow_run_url"), actor=_disc_identity())
    if not ok:
        return jsonify({"error": "prediction not found"}), 404
    return jsonify({"ok": True}), 200


@app.route("/portal/api/projects/<project_id>/events", methods=["POST"])
@_require_auth
def discovery_add_event(project_id):
    d = request.get_json(silent=True) or {}
    etype, summary = d.get("event_type"), d.get("summary")
    if not etype or not summary:
        return jsonify({"error": "event_type and summary are required"}), 400
    if etype not in discovery.EVENT_TYPES:
        return jsonify({"error": f"unknown event_type; allowed: "
                                 f"{sorted(discovery.EVENT_TYPES)}"}), 400
    eid = discovery.add_event(
        project_id, etype, summary, detail=d.get("detail"),
        hypothesis_id=d.get("hypothesis_id"),
        evidence_record_ids=d.get("evidence_record_ids"),
        mlflow_run_url=d.get("mlflow_run_url"), actor=_disc_identity())
    if eid is None:
        return jsonify({"error": "project not found"}), 404
    return jsonify({"event_id": eid}), 201


@app.route("/portal/api/projects/<project_id>/next_experiment", methods=["PUT"])
@_require_auth
def discovery_set_next_experiment(project_id):
    # REPLACE semantics: the full payload is stored (all keys preserved); send the
    # complete object each PUT.
    d = request.get_json(silent=True) or {}
    ok = discovery.set_next_experiment(project_id, d, actor=_disc_identity())
    if not ok:
        return jsonify({"error": "project not found or invalid payload"}), 404
    return jsonify({"ok": True}), 200


# ===========================================================================
# Entrypoint
# ===========================================================================

if __name__ == "__main__":
    if not database.is_db_configured():
        logger.warning(
            "Database not configured (PGHOST not set). "
            "Running without persistence -- DB endpoints will fail."
        )

    logger.info("Starting ISAAC Portal API on port %d", PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)  # never enable the Werkzeug debugger (RCE)
