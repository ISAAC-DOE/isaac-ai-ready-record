"""The repository steward's deterministic battery (tools/steward/battery.py) counts what it claims to count.

It runs daily and before and after every change, so its numbers are compared across runs: each metric here is
pinned on fixtures where the right count is known, including the canonical-class share (which once credited
technique prefixes such as 'xrd') and a same_sample_id basis the records do not support.
"""
import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))
sys.path.insert(0, str(REPO / "tools" / "steward"))

import battery  # noqa: E402

BASE = json.loads((REPO / "tests" / "adversarial" / "R00_control_valid.json").read_text())


def _rec(rid, owner="lab-a", **changes):
    r = copy.deepcopy(BASE)
    r["record_id"] = rid
    r.update(changes)
    return {"_meta": {"owner": owner, "created_at": "2026-10-04T00:00:00", "version": 1, "content_hash": rid}, **r}


A, B, C = "01KSTEWARD00000000000000A1", "01KSTEWARD00000000000000B1", "01KSTEWARD00000000000000C1"


def test_links_are_counted_by_what_the_records_support():
    a = _rec(A, links=[{"rel": "same_sample_as", "target": B, "basis": "same_sample_id", "notes": "Auto-link: same paper"}])
    b = _rec(B, links=[{"rel": "derived_from", "target": "01KSTEWARD0000000000000ZZZ", "basis": "unspecified"}])
    m = battery.measure([a, b])
    assert m["links"]["same_sample_id_basis_unsupported"] == 1 and m["links"]["auto_linker"] == 1
    assert m["links"]["dangling"] == 1 and m["links"]["same_sample_as_one_way"] == 1
    a["sample"]["sample_id"] = b["sample"]["sample_id"] = "IGSN:10.58052/TEST0001"
    assert battery.measure([a, b])["links"].get("same_sample_id_basis_unsupported", 0) == 0


def test_a_repeated_value_is_a_duplicate_and_a_new_condition_is_not():
    a, b = _rec(A), _rec(B)
    assert battery.measure([a, b])["per_uploader"]["lab-a"]["duplicate_values"] >= 1
    b["context"] = dict(b.get("context") or {}, temperature_K=350.0)
    assert battery.measure([a, b])["per_uploader"]["lab-a"].get("duplicate_values", 0) == 0


def test_canonical_class_share_ignores_technique_prefixes():
    r = _rec(C)
    r["descriptors"] = {"outputs": [{"descriptors": [
        {"name": "xrd.rwp", "kind": "absolute", "value": 6.0, "unit": "percent"},
        {"name": "lattice_parameter.a", "kind": "absolute", "value": 4.157, "unit": "angstrom"}]}]}
    c = battery.measure([r])["per_uploader"]["lab-a"]
    assert c["values"] == 2 and c["values_canonical_name"] == 1
    assert "xrd" not in battery.CANONICAL and "lattice_parameter" in battery.CANONICAL


def test_the_diff_reports_new_edited_and_deleted_records():
    before = [_rec(A), _rec(B)]
    after = [_rec(A), _rec(C)]
    after[0]["_meta"]["version"] = 2
    d = battery.diff(before, after, battery.measure(before), battery.measure(after))
    assert (d["new"], d["deleted"], d["edited"]) == (1, 1, 1)
