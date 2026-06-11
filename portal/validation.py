"""
ISAAC AI-Ready Record — unified validation.

THE single source of truth for record validation. Every ingestion path —
the REST API, the Streamlit validator page, the Streamlit record form,
and any future tool — validates through this module. The enforcement
point is database.save_record(), which calls validate_record_full()
internally and refuses to persist a failing record, so a new upload path
added later is guarded automatically even if its author forgets to
validate.

To change what validation does, change it here (or in the schema /
vocabulary files this module loads). All upload paths pick up the change
simultaneously.

Layers:
  1. JSON Schema  (schema/isaac_record_v1.json, Draft 2020-12)
  2. Vocabulary   (ontology.validate_record_vocabulary — living vocabulary)
  3. Semantic     (ontology.validate_semantic_integrity — cross-field rules)
"""

import json
import logging
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

# Make sibling modules importable regardless of caller CWD (same pattern
# as api.py / app.py, which run with different working directories).
_portal_dir = Path(__file__).resolve().parent
if str(_portal_dir) not in sys.path:
    sys.path.insert(0, str(_portal_dir))

import ontology  # noqa: E402

logger = logging.getLogger("isaac-validation")

# ---------------------------------------------------------------------------
# Schema (loaded once at import)
# ---------------------------------------------------------------------------
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "isaac_record_v1.json"
with open(SCHEMA_PATH) as f:
    ISAAC_SCHEMA = json.load(f)

# FIX (2026-06-11): FormatChecker was never passed, so every `format:
# "date-time"` in the schema was decorative — empty strings and
# space-separated timestamps passed. Requires the rfc3339-validator
# package (in requirements.txt) for date-time to actually be checked;
# we assert its presence so the enforcement can never silently vanish.
import rfc3339_validator  # noqa: F401  (presence assertion — see above)
ISAAC_VALIDATOR = Draft202012Validator(ISAAC_SCHEMA, format_checker=FormatChecker())

# ---------------------------------------------------------------------------
# Canonical forms (Decisions A & B, 2026-06-11) — loaded from the vocabulary
# single source of truth. Deprecated unit spellings and product tokens are
# REJECTED with a message naming the canonical replacement.
# ---------------------------------------------------------------------------
VOCAB_PATH = Path(__file__).resolve().parent.parent / "data" / "vocabulary.json"
try:
    with open(VOCAB_PATH) as f:
        _VOCAB = json.load(f)
    UNIT_ALIASES = _VOCAB.get("Units", {}).get("units.aliases", {}).get("map", {})
    PRODUCT_ALIASES = _VOCAB.get("Descriptors", {}).get("descriptors.product_aliases", {}).get("map", {})
except Exception as _exc:  # degrade gracefully; canonical checks become no-ops
    logger.warning("Could not load canonical-form maps from %s: %s", VOCAB_PATH, _exc)
    UNIT_ALIASES, PRODUCT_ALIASES = {}, {}

PRODUCT_CLASS_PREFIXES = (
    "faradaic_efficiency.", "partial_current_density.", "production_rate.",
    "initial_faradaic_efficiency.", "final_faradaic_efficiency.",
)

# Canonical product tokens (Decision B). Unknown tokens (typos, ad-hoc
# inventions) are rejected; known aliases get a rename message instead.
try:
    CANONICAL_PRODUCTS = set(
        _VOCAB.get("Descriptors", {})
        .get("descriptors.faradaic_efficiency_products", {})
        .get("values", [])
    )
except Exception:
    CANONICAL_PRODUCTS = set()

# Grandfathered non-product suffixes pending a wave-2 decision (derived
# metric stored as a token). Documented in the improvement plan.
GRANDFATHERED_PRODUCT_TOKENS = {"ratio_CH4_to_C2plus"}


def _canonical_form_errors(record: dict) -> list:
    """
    Enforce Decision A (slash-form unit grammar) and Decision B (formula-style
    product tokens): any unit string or product-token suffix found in the
    deprecation maps is an error pointing at the canonical replacement.
    """
    errors = []

    def unit_err(path, u):
        return {"path": path,
                "message": f"Unit '{u}' is a deprecated alias; use canonical "
                           f"'{UNIT_ALIASES[u]}' (slash-form unit grammar, see "
                           f"Controlled-Vocabulary wiki)."}

    outputs = (record.get("descriptors") or {}).get("outputs") or []
    for oi, o in enumerate(outputs):
        for di, d in enumerate(o.get("descriptors") or [] if isinstance(o, dict) else []):
            nm = d.get("name", "") or ""
            for p in PRODUCT_CLASS_PREFIXES:
                if nm.startswith(p):
                    suffix = nm[len(p):]
                    if suffix in PRODUCT_ALIASES:
                        errors.append({
                            "path": f"descriptors/outputs/{oi}/descriptors/{di}/name",
                            "message": f"Product token '{suffix}' is a deprecated alias; "
                                       f"use canonical '{PRODUCT_ALIASES[suffix]}' "
                                       f"(formula-style tokens, see Controlled-Vocabulary wiki).",
                        })
                    elif (CANONICAL_PRODUCTS
                          and suffix not in CANONICAL_PRODUCTS
                          and suffix not in GRANDFATHERED_PRODUCT_TOKENS):
                        errors.append({
                            "path": f"descriptors/outputs/{oi}/descriptors/{di}/name",
                            "message": f"Product token '{suffix}' is not in the canonical "
                                       f"product vocabulary (descriptors.faradaic_efficiency_products). "
                                       f"If this is a genuinely new product, request a vocabulary "
                                       f"addition; do not invent tokens.",
                        })
                    break
            # FE physics: fraction-encoded values must be physical. A value
            # above 1.5 is almost certainly percent-encoded in a fraction
            # field (e.g. 91 instead of 0.91).
            if nm.startswith(("faradaic_efficiency.", "total_faradaic_efficiency")):
                val = d.get("value")
                if isinstance(val, (int, float)) and (val < 0 or val > 1.5):
                    errors.append({
                        "path": f"descriptors/outputs/{oi}/descriptors/{di}/value",
                        "message": f"Faradaic efficiency value {val} is outside [0, 1.5]. "
                                   f"FE is a fraction (0-1); values like 91 are percent-encoded "
                                   f"— divide by 100.",
                    })
            u = d.get("unit")
            if u in UNIT_ALIASES:
                errors.append(unit_err(f"descriptors/outputs/{oi}/descriptors/{di}/unit", u))
            uu = (d.get("uncertainty") or {}).get("unit")
            if uu in UNIT_ALIASES:
                errors.append(unit_err(f"descriptors/outputs/{oi}/descriptors/{di}/uncertainty/unit", uu))

    series = (record.get("measurement") or {}).get("series") or []
    for si, s in enumerate(series):
        for kind in ("channels", "independent_variables"):
            for ci, ch in enumerate(s.get(kind) or []):
                u = ch.get("unit")
                if u in UNIT_ALIASES:
                    errors.append(unit_err(f"measurement/series/{si}/{kind}/{ci}/unit", u))

    return errors


class ValidationError(Exception):
    """
    Raised by the persistence chokepoint (database.save_record) when a
    record fails validation. Carries the full structured result so callers
    can render per-layer errors.
    """

    def __init__(self, result: dict):
        self.result = result
        n = len(result.get("errors", []))
        super().__init__(f"Record failed ISAAC validation with {n} error(s)")


def validate_record_full(record: dict) -> dict:
    """
    Run ALL validation layers against a record dict.

    Returns the canonical result shape (identical to the public
    /portal/api/validate response):

        {
          "valid": bool,
          "schema_valid": bool, "vocabulary_valid": bool, "semantic_valid": bool,
          "schema_errors": [...], "vocabulary_errors": [...],
          "semantic_errors": [...], "errors": [...],
        }

    Vocabulary and semantic layers degrade gracefully (log + empty list)
    on internal failure, matching the API's historical behavior; the JSON
    Schema layer never degrades.
    """
    schema_errors = [
        {
            "path": "/".join(str(p) for p in err.absolute_path) or "(root)",
            "message": err.message,
        }
        for err in ISAAC_VALIDATOR.iter_errors(record)
    ]

    # FIX (2026-06-11): degradation is no longer invisible. The layers still
    # fail open (fail-closed is a pending policy decision), but the response
    # now carries a `degraded` flag and the degradation reason so callers,
    # logs, and monitors can SEE that a layer did not actually run.
    degraded = []
    try:
        vocabulary_errors = ontology.validate_record_vocabulary(record)
    except Exception as exc:
        logger.error("VOCABULARY VALIDATION DEGRADED — layer did not run: %s", exc)
        vocabulary_errors = []
        degraded.append({"layer": "vocabulary", "reason": str(exc)[:200]})

    # Canonical-form enforcement (Decisions A & B) — deterministic, never
    # degrades, lives in the vocabulary layer of the response.
    vocabulary_errors = vocabulary_errors + _canonical_form_errors(record)

    try:
        semantic_errors = ontology.validate_semantic_integrity(record)
    except Exception as exc:
        logger.error("SEMANTIC VALIDATION DEGRADED — layer did not run: %s", exc)
        semantic_errors = []
        degraded.append({"layer": "semantic", "reason": str(exc)[:200]})

    errors = schema_errors + vocabulary_errors + semantic_errors
    result = {
        "valid": not errors,
        "schema_valid": not schema_errors,
        "vocabulary_valid": not vocabulary_errors,
        "semantic_valid": not semantic_errors,
        "schema_errors": schema_errors,
        "vocabulary_errors": vocabulary_errors,
        "semantic_errors": semantic_errors,
        "errors": errors,
    }
    if degraded:
        result["degraded"] = degraded
    return result


def format_errors_flat(result: dict) -> list:
    """Flatten a validation result into 'path: message' strings for UIs."""
    return [f"{e['path']}: {e['message']}" for e in result.get("errors", [])]
