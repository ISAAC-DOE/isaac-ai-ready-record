"""A checksum that is no checksum, and same_sample_as on a basis two specimens can share (warnings, 2026-10-06).

On 2026-10-06, 842 stored records carried an all-zero checksum or a word such as "pending" in its place, and
45 carried same_sample_as with a basis that names something different specimens share (a material batch, a
method, an absorber edge); 42 of those were the team's own. The tutorial taught both: invented checksums,
two of them not hexadecimal, and a same_sample_as link on "identical GDE batch". A complete record shown in
the wiki is copied as shown, so it must carry no warning and equal its file in examples/.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))
sys.path.insert(0, str(REPO / "tools"))

import validation  # noqa: E402
import generate_validation_docs as docs  # noqa: E402

CO2RR = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())


def _codes(record):
    return {w["code"] for w in validation._checksum_and_link_basis_warnings(record)}


def _with_checksum(value):
    return {"assets": [{"asset_id": "a", "content_role": "raw_data", "uri": "s3://lab/run.h5", "sha256": value}]}


@pytest.mark.parametrize("value", ["0" * 64, "0" * 40, "f" * 64, "0" * 64 + "\n", "pending", "TBD",
                                   "generated_at_extraction_time",
                                   "g3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b234",
                                   "25f563605c31278152283734135544710156784381389"])
def test_a_value_that_is_no_sha256_is_flagged(value):
    assert "CHECKSUM_NOT_SHA256" in _codes(_with_checksum(value))


@pytest.mark.parametrize("value", ["9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                                   "9F86D081884C7D659A2FEAA0C55AD015A3BF4F1B2B0B822CD15D6C15B0F00A08",
                                   "not_available_literature_source", "not_available"])
def test_a_sha256_or_a_placeholder_passes(value):
    assert "CHECKSUM_NOT_SHA256" not in _codes(_with_checksum(value))


def test_the_message_names_the_count_and_the_placeholders():
    record = {"assets": [_with_checksum("0" * 64)["assets"][0], _with_checksum("pending")["assets"][0]]}
    w = next(x for x in validation._checksum_and_link_basis_warnings(record) if x["code"] == "CHECKSUM_NOT_SHA256")
    assert w["message"].startswith("2 assets have a sha256") and "not_available_literature_source" in w["message"]
    one = next(x for x in validation._checksum_and_link_basis_warnings(_with_checksum("pending")))
    assert one["message"].startswith("1 asset has a sha256")


def _linked(rel, basis, notes="", calculation=False):
    record = {"links": [{"rel": rel, "target": "01JFH7K2W6P1D9A3R4ZQ2M8T5V", "basis": basis, "notes": notes}]}
    if calculation:
        record.update(system={"domain": "computational"}, context={"environment": "in_silico"})
    return record


@pytest.mark.parametrize("basis", sorted(validation._SHARED_NOT_IDENTITY))
def test_same_sample_as_on_a_shared_basis_is_flagged(basis):
    assert "SAME_SAMPLE_ON_A_SHARED_BASIS" in _codes(_linked("same_sample_as", basis))


def test_a_link_on_the_sample_id_adds_nothing():
    """Identical sample_ids already make one specimen; one warning per record, counting its links."""
    assert _codes(_linked("same_sample_as", "same_sample_id")) == {"SAME_SAMPLE_LINK_ON_SAMPLE_ID"}
    two = {"links": [_linked("same_sample_as", "same_sample_id")["links"][0],
                     dict(_linked("same_sample_as", "same_sample_id")["links"][0], target="01JFH7K2W6P1D9A3R4ZQ2M8T5W")]}
    found = [w for w in validation._checksum_and_link_basis_warnings(two) if w["code"] == "SAME_SAMPLE_LINK_ON_SAMPLE_ID"]
    assert len(found) == 1 and found[0]["message"].startswith("These 2 same_sample_as links use")
    assert "paper slug is not a sample_id" in found[0]["message"]


@pytest.mark.parametrize("rel, basis", [("intended_comparison_target", "same_sample_id"),
                                        ("intended_comparison_target", "shared_material_batch"),
                                        ("intended_comparison_target", "same_absorber_edge")])
def test_other_links_pass(rel, basis):
    assert not _codes(_linked(rel, basis))


def test_identical_geometry_is_identity_for_a_calculation_only():
    assert not _codes(_linked("same_sample_as", "identical_geometry", calculation=True))
    assert "SAME_SAMPLE_ON_A_SHARED_BASIS" in _codes(_linked("same_sample_as", "identical_geometry"))


def test_unspecified_needs_its_passage():
    """The shared-basis warning's remedy is basis 'unspecified' with the passage; without it the hole reopens."""
    assert _codes(_linked("same_sample_as", "unspecified")) == {"SAME_SAMPLE_WITHOUT_PASSAGE"}
    assert _codes(_linked("same_sample_as", "unspecified", notes="   ")) == {"SAME_SAMPLE_WITHOUT_PASSAGE"}
    assert not _codes(_linked("same_sample_as", "unspecified", notes="Section 2.3: one electrode was used for all potentials."))


def test_every_example_record_is_clean():
    for path in sorted((REPO / "examples").glob("*.json")):
        data = json.loads(path.read_text())
        for record in data if isinstance(data, list) else [data]:
            if isinstance(record, dict) and record.get("record_type"):
                assert not _codes(record), path.name


def _wiki(tmp_path, record):
    page = tmp_path / "Write-Your-First-Record.md"
    page.write_text("# Tutorial\n\n```json\n" + json.dumps(record, indent=1) + "\n```\n")
    return tmp_path


def test_a_wiki_record_identical_to_its_example_and_clean_passes(tmp_path):
    assert docs.embedded_record_failures(_wiki(tmp_path, CO2RR)) == []


def test_a_wiki_record_with_a_warning_or_drift_is_reported(tmp_path):
    taught = copy.deepcopy(CO2RR)
    taught["assets"][0]["sha256"] = "0" * 64
    found = docs.embedded_record_failures(_wiki(tmp_path, taught))
    assert any("carries warnings ['CHECKSUM_NOT_SHA256']" in line for line in found)
    assert any("differs from examples/co2rr_performance_record.json" in line for line in found)
