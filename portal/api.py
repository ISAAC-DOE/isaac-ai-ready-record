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
