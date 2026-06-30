"""
Stability + correctness gate for record content hashing (portal/record_provenance.py).

The MOST important test in the editing/versioning feature: if the hash is not stable
across a PostgreSQL JSONB round-trip, drift detection produces false alarms (cosmetic
re-save reads as a change) or misses real edits. These tests pin that contract.
"""
import copy
import json
import pytest

from portal import record_provenance as rp


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
