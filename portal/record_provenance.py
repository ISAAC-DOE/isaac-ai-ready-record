"""
Record provenance primitives — content hashing + material/cosmetic classification.

PURE LOGIC (no DB, no Flask) so it can be unit-tested offline and reused by both the
records store (versioning) and the discovery side (drift detection). The contract:

  * content_hash(record) is STABLE: the same scientific content always hashes the same,
    INCLUDING after a round-trip through PostgreSQL JSONB (which reorders keys and
    coerces numbers). This is what makes drift detection trustworthy — a cosmetic
    re-save must not change the hash; a real change always must.
  * The hash is computed over a WHITELIST of scientific blocks only. Volatile metadata
    (attribution/ownership, timestamps, record_id, routing, tags, schema version) is
    EXCLUDED — so an owner-reassign or a tag edit is "cosmetic" by construction and can
    never spuriously trigger downstream re-examination.

Reviewed (3 adversarial sub-agents, 2026-06-30): whitelist-not-blacklist; hash the
JSONB-stored form; NFC unicode; preserve nulls (null != missing) and list order; no
float rounding; hash asset CHECKSUMS not URIs.
"""
from __future__ import annotations
import hashlib
import json
import unicodedata

# Scientific blocks that constitute the record's MEANING. Whitelist (not blacklist) so a
# future metadata block added to the schema can never silently leak into the hash.
SCIENTIFIC_BLOCKS = (
    "source_type", "sample", "system", "context",
    "measurement", "links", "assets", "descriptors", "computation",
)

# Within an asset, fields that locate the bytes but are not the bytes. A re-host (new URI,
# same checksum) is cosmetic; a new checksum is material.
_ASSET_LOCATOR_KEYS = {"uri", "url", "href", "location", "path", "filepath", "filename"}


def _canon(obj):
    """Recursively normalize a JSON value into a JSONB-stable, hashable form.

    * dict  -> dict with normalized values (keys are sorted at serialization time).
    * list  -> list with normalized items, ORDER PRESERVED (descriptor/series order is
               scientific; we never reorder lists).
    * str   -> NFC-normalized (so 'Å' composed vs decomposed hash identically).
    * float -> integer-valued floats collapse to int (1.0 -> 1) to match how PostgreSQL
               JSONB coerces numbers on round-trip; other floats are left exact (NO
               rounding — a real 1.0 -> 1.00001 must still change the hash).
    * bool / int / None -> unchanged (None is preserved: explicit null != missing key).
    """
    if isinstance(obj, dict):
        return {k: _canon(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_canon(v) for v in obj]
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, bool):  # bool is a subclass of int — handle before int/float
        return obj
    if isinstance(obj, float):
        return int(obj) if obj.is_integer() else obj
    return obj


def _project_assets(assets):
    """Project assets to their content identity (checksums), dropping locator fields so a
    re-host with an identical checksum does not read as a scientific change."""
    if not isinstance(assets, list):
        return assets
    out = []
    for a in assets:
        if isinstance(a, dict):
            out.append({k: v for k, v in a.items() if k.lower() not in _ASSET_LOCATOR_KEYS})
        else:
            out.append(a)
    return out


def scientific_projection(record: dict) -> dict:
    """The whitelist projection of a record that defines its scientific identity."""
    if not isinstance(record, dict):
        return {}
    proj = {}
    for block in SCIENTIFIC_BLOCKS:
        if block in record:  # presence matters: a block going from present->absent is material
            value = record[block]
            proj[block] = _project_assets(value) if block == "assets" else value
    return _canon(proj)


def canonical_json(record: dict) -> str:
    """Deterministic serialization of the scientific projection."""
    return json.dumps(
        scientific_projection(record),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def content_hash(record: dict) -> str:
    """sha256 hex of the record's scientific content. Stable across JSONB round-trips."""
    return hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()


def is_material(old: dict, new: dict) -> bool:
    """True iff the scientific content changed (the only thing that should trigger
    downstream re-examination). Owner/attribution/timestamp/tag edits return False."""
    return content_hash(old) != content_hash(new)


def classify_change(old: dict, new: dict) -> str:
    """Server-computed change class — authoritative; never trust a client-supplied class
    for the drift gate. Returns 'material' or 'metadata'."""
    return "material" if is_material(old, new) else "metadata"


_MISSING = object()


def diff_paths(old: dict, new: dict) -> list:
    """Field-level changes between two whole records, as [{path, old, new}]. Compares the
    full record (not just the scientific projection) so a human/agent sees every change."""
    changes = []

    def walk(o, n, path):
        if isinstance(o, dict) or isinstance(n, dict):
            od = o if isinstance(o, dict) else {}
            nd = n if isinstance(n, dict) else {}
            for k in sorted(set(od) | set(nd)):
                walk(od.get(k, _MISSING), nd.get(k, _MISSING), f"{path}.{k}" if path else k)
        elif isinstance(o, list) or isinstance(n, list):
            ol = o if isinstance(o, list) else []
            nl = n if isinstance(n, list) else []
            for i in range(max(len(ol), len(nl))):
                walk(ol[i] if i < len(ol) else _MISSING,
                     nl[i] if i < len(nl) else _MISSING, f"{path}[{i}]")
        elif o != n:
            changes.append({"path": path,
                            "old": None if o is _MISSING else o,
                            "new": None if n is _MISSING else n})

    walk(old or {}, new or {}, "")
    return changes


def evidence_drift(predictions, current_hashes) -> list:
    """Detect cited evidence that was MATERIALLY edited since it was used to reason.

    Only considers predictions that carry a VERDICT — i.e. evidence actually used for a
    hypothesis. Records merely browsed in a project (no verdict) are never flagged.

    predictions:    [{prediction_id, hypothesis, verdict,
                      evidence_pins:[{record_id, version, content_hash}]}]
    current_hashes: {record_id: current_content_hash}  (None => unknown, stay silent)
    Returns one entry per drifted (prediction, record): {prediction_id, hypothesis,
    record_id, pinned_version, pinned_hash, current_hash}.
    """
    out = []
    for p in predictions or []:
        if not p.get("verdict"):
            continue  # not used for a hypothesis verdict -> no warning
        for pin in (p.get("evidence_pins") or []):
            rid, pinned = pin.get("record_id"), pin.get("content_hash")
            if not rid or not pinned:
                continue  # legacy/unpinned -> cannot detect, stay silent
            cur = current_hashes.get(rid)
            if cur is not None and cur != pinned:
                out.append({"prediction_id": p.get("prediction_id"),
                            "hypothesis": p.get("hypothesis"),
                            "record_id": rid,
                            "pinned_version": pin.get("version"),
                            "pinned_hash": pinned, "current_hash": cur})
    return out
