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

from jsonschema import Draft202012Validator

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
ISAAC_VALIDATOR = Draft202012Validator(ISAAC_SCHEMA)


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

    try:
        vocabulary_errors = ontology.validate_record_vocabulary(record)
    except Exception as exc:
        logger.warning("Vocabulary validation degraded: %s", exc)
        vocabulary_errors = []

    try:
        semantic_errors = ontology.validate_semantic_integrity(record)
    except Exception as exc:
        logger.warning("Semantic integrity validation degraded: %s", exc)
        semantic_errors = []

    errors = schema_errors + vocabulary_errors + semantic_errors
    return {
        "valid": not errors,
        "schema_valid": not schema_errors,
        "vocabulary_valid": not vocabulary_errors,
        "semantic_valid": not semantic_errors,
        "schema_errors": schema_errors,
        "vocabulary_errors": vocabulary_errors,
        "semantic_errors": semantic_errors,
        "errors": errors,
    }


def format_errors_flat(result: dict) -> list:
    """Flatten a validation result into 'path: message' strings for UIs."""
    return [f"{e['path']}: {e['message']}" for e in result.get("errors", [])]
