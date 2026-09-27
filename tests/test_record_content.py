"""
A record is knowledge, not reasoning about it.

On 2026-09-27, 173 records carried hypothesis labels, verdicts and benchmark machinery in
their curator-written text, and 436 carried benchmark item ids as tags. The repository
holds knowledge; reasoning about it belongs to whatever uses it. These tests pin the rule,
and pin that ordinary scientific prose (H2, residual current, a hypothesis quoted from a
paper in an asset) is left alone.
"""

import copy
import json
import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(PORTAL))

import validation  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
BASE = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())


def _codes(record):
    return [e.get("code") for e in validation.validate_record_full(record)["errors"]]


def _with_notes(text, where="sample"):
    r = copy.deepcopy(BASE)
    if where == "sample":
        r["sample"]["material"]["notes"] = text
    elif where == "qc":
        r["measurement"].setdefault("qc", {})["notes"] = text
    elif where == "definition":
        r["descriptors"]["outputs"][0]["descriptors"][0]["definition"] = text
    return r


def test_verdicts_and_hypothesis_labels_are_rejected():
    for text in ("That is what makes this record decisive against H4.",
                 "Supplies the mechanism H3 needs: the basal plane binds H.",
                 "Tests the residual's central claim directly.",
                 "The local frozen-set record (case_20-LIT-5004) retains the original.",
                 "This record was included because it isolates the support effect."):
        assert "REASONING_IN_RECORD" in _codes(_with_notes(text)), text


def test_the_rule_covers_qc_notes_and_definitions():
    assert "REASONING_IN_RECORD" in _codes(_with_notes("Keeps H5 from being scored as strong.", "qc"))
    assert "REASONING_IN_RECORD" in _codes(_with_notes("Decisive evidence for H2 over H1.", "definition"))


def test_ordinary_scientific_prose_is_left_alone():
    for text in ("H2 evolution competes below -0.9 V; the residual current was subtracted.",
                 "Hypothesis-free survey of H2 and CO production at 25 C.",
                 "Case study of Cu electrodes; LIT review cited in the paper.",
                 "Faradaic efficiency of H2 decreases with Cu fraction."):
        assert "REASONING_IN_RECORD" not in _codes(_with_notes(text)), text


def test_a_quote_of_the_source_in_an_asset_is_not_scanned():
    r = copy.deepcopy(BASE)
    r.setdefault("assets", []).append({"asset_id": "quote_1", "content_role": "documentation",
                                       "uri": "https://doi.org/10.0000/example",
                                       "notes": "The authors state that hypothesis H1 is supported by the data."})
    assert "REASONING_IN_RECORD" not in _codes(r)


def test_tags_describe_data_not_its_use():
    r = copy.deepcopy(BASE)
    r["tags"] = ["case_3-LIT-0001"]
    assert "TAG_ENCODES_USE" in _codes(r)
    r["tags"] = ["jcap-hte", "xu-2026-cuag-stripes", "lisa-cu-au-2026"]
    assert "TAG_ENCODES_USE" not in _codes(r)
