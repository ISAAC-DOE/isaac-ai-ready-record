"""A calculation carries what it takes to rerun it (ledger change 20): an input only its producer can open is
flagged, inline content is checked against its sha256 and its size, and the look-alikes pass."""
import copy
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))
import validation  # noqa: E402

OCEAN = json.loads((REPO / "examples" / "ocean_xas_calibrated_record.json").read_text())


def _codes(r):
    return {w["code"] for w in validation.validate_record_full(r).get("warnings", [])}


def _asset(r, asset_id):
    return next(a for a in r["assets"] if a["asset_id"] == asset_id)


def test_inline_inputs_carry_their_text_and_its_hash():
    a = _asset(OCEAN, "input_structure")
    assert a["content"].count("\n") > 5
    assert a["sha256"] == hashlib.sha256(a["content"].encode("utf-8")).hexdigest()
    assert not {"INPUT_NOT_REACHABLE", "INLINE_CONTENT_CHECKSUM_MISMATCH"} & _codes(OCEAN)


def test_an_input_only_its_producer_can_open_is_caught():
    r = copy.deepcopy(OCEAN)
    a = _asset(r, "input_structure")
    del a["content"]
    a["uri"], a["sha256"] = "file:///global/cfs/cdirs/m0000/someone/CONTCAR", "0" * 63 + "1"
    assert "INPUT_NOT_REACHABLE" in _codes(r)


def test_a_public_copy_passes_and_so_does_a_literature_calculation():
    r = copy.deepcopy(OCEAN)
    a = _asset(r, "input_structure")
    del a["content"]
    a["uri"] = "https://doi.org/10.5281/zenodo.0000000"
    assert "INPUT_NOT_REACHABLE" not in _codes(r)
    a["uri"] = "repo://private-workflow/CONTCAR"
    assert "INPUT_NOT_REACHABLE" in _codes(r)
    r["source_type"] = "literature"
    assert "INPUT_NOT_REACHABLE" not in _codes(r)


def test_an_output_at_a_private_path_is_not_an_input():
    r = copy.deepcopy(OCEAN)
    r["assets"].append({"asset_id": "outcar", "content_role": "raw_data_pointer", "uri": "file:///scratch/OUTCAR",
                        "sha256": "not_available"})
    assert "INPUT_NOT_REACHABLE" not in _codes(r)


def test_inline_content_that_does_not_match_its_hash_is_caught():
    r = copy.deepcopy(OCEAN)
    _asset(r, "ocean_input")["content"] += "\n# edited after hashing\n"
    assert "INLINE_CONTENT_CHECKSUM_MISMATCH" in _codes(r)


def test_inline_content_needs_its_hash():
    r = copy.deepcopy(OCEAN)
    _asset(r, "ocean_input")["sha256"] = "not_available"
    assert "INLINE_CONTENT_CHECKSUM_MISMATCH" in _codes(r)


def test_a_database_record_is_not_asked_for_inputs_its_uploader_never_ran():
    r = copy.deepcopy(OCEAN)
    a = _asset(r, "input_structure")
    del a["content"]
    a["uri"], a["sha256"] = "file:///Users/someone/Desktop/structure.traj", "not_available"
    r["source_type"] = "database"
    assert "INPUT_NOT_REACHABLE" not in _codes(r)


def test_a_calculation_that_names_no_inputs_is_caught():
    r = copy.deepcopy(OCEAN)
    r["assets"] = [a for a in r["assets"] if a["content_role"] not in ("input_structure", "workflow_recipe")]
    assert "INPUT_NOT_REACHABLE" in _codes(r)


def test_inline_content_above_the_limit_is_caught():
    r = copy.deepcopy(OCEAN)
    text = "x" * 100_001
    a = _asset(r, "ocean_input")
    a["content"], a["sha256"] = text, hashlib.sha256(text.encode()).hexdigest()
    assert "INLINE_CONTENT_TOO_LARGE" in _codes(r)
