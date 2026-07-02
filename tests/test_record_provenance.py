"""
Stability + correctness gate for record content hashing (portal/record_provenance.py).

The MOST important test in the editing/versioning feature: if the hash is not stable
across a PostgreSQL JSONB round-trip, drift detection produces false alarms (cosmetic
re-save reads as a change) or misses real edits. These tests pin that contract.
"""
import copy
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "portal"))
import record_provenance as rp  # noqa: E402


def _jsonb_roundtrip(rec):
    """Simulate what PostgreSQL JSONB does to a value on store->read: serialize and
    re-parse (reorders keys; coerces 1.0->1, 0.40->0.4)."""
    return json.loads(json.dumps(rec))


BASE = {
    "record_id": "01ABC0000000000000000000AA",
    "record_type": "performance",
    "record_domain": "electrocatalysis",
    "isaac_record_version": "1.05",
    "source_type": "experimental",
    "timestamps": {"created": "2026-06-04T15:24:31Z"},
    "attribution": {"uploaded_by": "dsokaras",
                    "contributors": [{"name": "A", "orcid": "0000-0001"}]},
    "tags": ["cu-au", "co2rr"],
    "sample": {"name": "Cu-Au stripe", "composition": {"Cu": 0.8, "Au": 0.2}},
    "descriptors": {"faradaic_efficiency": {"C2H4": 0.38, "CO": 0.12},
                    "tafel_slope": None},
    "measurement": {"series": [{"x": 1, "y": 2}, {"x": 2, "y": 3}]},
    "assets": [{"name": "cv.csv", "uri": "s3://bucket/a/cv.csv", "checksum": "abc123"}],
}


def test_hash_is_deterministic():
    assert rp.content_hash(BASE) == rp.content_hash(copy.deepcopy(BASE))


def test_hash_stable_across_jsonb_roundtrip():
    # THE gate: store->read must not change the hash.
    assert rp.content_hash(BASE) == rp.content_hash(_jsonb_roundtrip(BASE))


def test_key_order_irrelevant():
    shuffled = {k: BASE[k] for k in reversed(list(BASE.keys()))}
    shuffled["sample"] = {"composition": {"Au": 0.2, "Cu": 0.8}, "name": "Cu-Au stripe"}
    assert rp.content_hash(shuffled) == rp.content_hash(BASE)


def test_number_coercion_equivalence():
    a = copy.deepcopy(BASE); a["descriptors"]["faradaic_efficiency"]["C2H4"] = 0.40
    b = copy.deepcopy(BASE); b["descriptors"]["faradaic_efficiency"]["C2H4"] = 0.4
    assert rp.content_hash(a) == rp.content_hash(b)
    c = copy.deepcopy(BASE); c["sample"]["composition"]["Cu"] = 1.0
    d = copy.deepcopy(BASE); d["sample"]["composition"]["Cu"] = 1
    assert rp.content_hash(c) == rp.content_hash(d)


# ---- material vs cosmetic -------------------------------------------------

def test_attribution_change_is_cosmetic():
    # The Grushika case: owner reassign must NOT look material.
    edited = copy.deepcopy(BASE); edited["attribution"]["uploaded_by"] = "mahajan"
    assert not rp.is_material(BASE, edited)
    assert rp.classify_change(BASE, edited) == "metadata"


def test_metadata_blocks_are_cosmetic():
    for block, mutate in [
        ("tags", lambda r: r["tags"].append("new")),
        ("timestamps", lambda r: r["timestamps"].update({"updated": "2026-06-30"})),
        ("record_type", lambda r: r.__setitem__("record_type", "characterization")),
        ("isaac_record_version", lambda r: r.__setitem__("isaac_record_version", "1.06")),
    ]:
        edited = copy.deepcopy(BASE); mutate(edited)
        assert not rp.is_material(BASE, edited), f"{block} edit wrongly flagged material"


def test_descriptor_change_is_material():
    edited = copy.deepcopy(BASE)
    edited["descriptors"]["faradaic_efficiency"]["C2H4"] = 0.41
    assert rp.is_material(BASE, edited)
    assert rp.classify_change(BASE, edited) == "material"


def test_null_vs_missing_differ():
    # explicit null ("measured, absent") != missing key ("not addressed") — scientific.
    missing = copy.deepcopy(BASE); del missing["descriptors"]["tafel_slope"]
    assert rp.is_material(BASE, missing)


def test_list_order_is_material():
    edited = copy.deepcopy(BASE)
    edited["measurement"]["series"] = list(reversed(edited["measurement"]["series"]))
    assert rp.is_material(BASE, edited)


def test_asset_uri_change_is_cosmetic_checksum_is_material():
    rehosted = copy.deepcopy(BASE)
    rehosted["assets"][0]["uri"] = "s3://other-bucket/cv.csv"  # same checksum
    assert not rp.is_material(BASE, rehosted)
    changed = copy.deepcopy(BASE)
    changed["assets"][0]["checksum"] = "deadbeef"  # bytes changed
    assert rp.is_material(BASE, changed)


def test_unicode_nfc_equivalence():
    import unicodedata
    a = copy.deepcopy(BASE); a["sample"]["name"] = unicodedata.normalize("NFC", "Å-Cu")
    b = copy.deepcopy(BASE); b["sample"]["name"] = unicodedata.normalize("NFD", "Å-Cu")
    assert rp.content_hash(a) == rp.content_hash(b)


def test_block_presence_change_is_material():
    no_comp = copy.deepcopy(BASE); no_comp.pop("descriptors")
    assert rp.is_material(BASE, no_comp)


def test_diff_paths_reports_field_changes():
    a = {"descriptors": {"x": 1}, "attribution": {"uploaded_by": "a"}}
    b = {"descriptors": {"x": 2}, "attribution": {"uploaded_by": "b"}}
    paths = {c["path"]: (c["old"], c["new"]) for c in rp.diff_paths(a, b)}
    assert paths["descriptors.x"] == (1, 2)
    assert paths["attribution.uploaded_by"] == ("a", "b")


def test_diff_paths_added_key():
    ch = rp.diff_paths({"descriptors": {"x": 1}}, {"descriptors": {"x": 1, "y": 9}})
    assert any(c["path"] == "descriptors.y" and c["old"] is None and c["new"] == 9 for c in ch)
    assert not rp.diff_paths(BASE, copy.deepcopy(BASE))


# ---- evidence drift (discovery integration) -------------------------------

def test_drift_flags_only_cited_for_a_verdict():
    preds = [
        {"prediction_id": "P1", "hypothesis": "H-A", "verdict": "supports",
         "evidence_pins": [{"record_id": "R1", "version": 1, "content_hash": "h1"}]},
        {"prediction_id": "P2", "hypothesis": "H-A", "verdict": None,   # browsed, not used
         "evidence_pins": [{"record_id": "R2", "version": 1, "content_hash": "hX"}]},
    ]
    drift = rp.evidence_drift(preds, {"R1": "h2", "R2": "hY"})  # both records changed
    assert len(drift) == 1 and drift[0]["record_id"] == "R1" and drift[0]["hypothesis"] == "H-A"


def test_drift_silent_when_unchanged():
    preds = [{"prediction_id": "P1", "hypothesis": "H", "verdict": "contradicts",
              "evidence_pins": [{"record_id": "R1", "version": 2, "content_hash": "h"}]}]
    assert rp.evidence_drift(preds, {"R1": "h"}) == []


def test_drift_skips_unpinned_and_unknown():
    preds = [{"prediction_id": "P", "hypothesis": "H", "verdict": "supports",
              "evidence_pins": [{"record_id": "R1", "content_hash": None},   # legacy
                                {"record_id": "R2", "content_hash": "h"}]}]   # current unknown
    assert rp.evidence_drift(preds, {"R2": None}) == []


# --- generation metadata (REAL schema shape) must NOT move the hash ------------
# The BASE fixture above uses a toy descriptors shape with no outputs[]; the real
# schema puts a REQUIRED generated_utc + generated_by inside descriptors.outputs[],
# and those must be excluded from the hash (else every pipeline regeneration reads as
# a material change and false-triggers drift). This is the gap the review flagged.

def _rec_with_gen(gen_utc, agent="auto-catalysis-agent", value=0.31):
    r = copy.deepcopy(BASE)
    r["descriptors"] = {"outputs": [{
        "label": "CO2RR performance",
        "generated_utc": gen_utc,
        "generated_by": {"agent": agent, "version": "1.2.0", "author": "pipeline"},
        "descriptors": [{"name": "overpotential", "value": value, "unit": "V"}],
    }]}
    return r


def test_regeneration_only_is_not_material():
    t0 = _rec_with_gen("2026-06-04T15:24:31Z", agent="agent-A")
    t1 = _rec_with_gen("2026-07-01T09:00:00Z", agent="agent-B")  # new time+agent, SAME numbers
    assert rp.content_hash(t0) == rp.content_hash(t1)
    assert rp.classify_change(t0, t1) == "metadata"
    assert rp.is_material(t0, t1) is False


def test_real_descriptor_value_change_is_material():
    a = _rec_with_gen("2026-06-04T15:24:31Z", value=0.31)
    b = _rec_with_gen("2026-06-04T15:24:31Z", value=0.42)  # same time, different NUMBER
    assert rp.content_hash(a) != rp.content_hash(b)
    assert rp.classify_change(a, b) == "material"


def test_generation_metadata_stable_across_jsonb_roundtrip():
    r = _rec_with_gen("2026-06-04T15:24:31Z")
    assert rp.content_hash(r) == rp.content_hash(_jsonb_roundtrip(r))


# --- hash is versioned; drift compares same-version only -----------------------

def test_content_hash_is_versioned():
    h = rp.content_hash(BASE)
    assert h.startswith("v2:")
    assert rp.hash_algorithm_version(h) == "v2"
    assert rp.hash_algorithm_version("deadbeef" * 8) is None  # legacy bare hex -> no version


def test_drift_skips_cross_version_pins():
    """A legacy (unversioned) pin vs a v2 current hash must NOT read as drift — it just
    needs re-pinning. This keeps the hash-version migration from firing false drift."""
    v2_current = rp.content_hash(BASE)          # 'v2:...'
    legacy_pin = v2_current.split(":", 1)[1]    # bare hex, as older pins were stored
    preds = [{"prediction_id": "p1", "hypothesis": "H", "verdict": "supports",
              "evidence_pins": [{"record_id": "R1", "version": 1, "content_hash": legacy_pin}]}]
    assert rp.evidence_drift(preds, {"R1": v2_current}) == []


def test_drift_fires_within_same_version():
    h_old = rp.content_hash(_rec_with_gen("t", value=0.31))
    h_new = rp.content_hash(_rec_with_gen("t", value=0.99))
    preds = [{"prediction_id": "p1", "hypothesis": "H", "verdict": "supports",
              "evidence_pins": [{"record_id": "R1", "version": 1, "content_hash": h_old}]}]
    assert len(rp.evidence_drift(preds, {"R1": h_new})) == 1   # changed -> drift
    assert rp.evidence_drift(preds, {"R1": h_old}) == []        # same -> none


def test_nested_conversion_metadata_is_stripped():
    """context...converted_utc / converted_by are processing provenance nested inside a
    HASHED block — re-doing a potential conversion restamps them but the science is
    unchanged, so the hash must not move. The conversion CONSTANT changing IS material."""
    a = copy.deepcopy(BASE)
    a["context"] = {"electrochemistry": {"potential_vs_RHE": {
        "rhe_conversion_offset_V": 0.21,
        "converted_utc": "2026-06-01T00:00:00Z", "converted_by": "calc-A"}}}
    b = copy.deepcopy(a)
    b["context"]["electrochemistry"]["potential_vs_RHE"].update(
        converted_utc="2099-09-09T00:00:00Z", converted_by="calc-B")  # provenance only
    assert rp.content_hash(a) == rp.content_hash(b)
    c = copy.deepcopy(a)
    c["context"]["electrochemistry"]["potential_vs_RHE"]["rhe_conversion_offset_V"] = 0.42
    assert rp.content_hash(a) != rp.content_hash(c)  # real conversion constant -> material
