"""Benchmark and hypothesis reasoning stays out of records; ordinary chemistry gets through.

REASONING_IN_RECORD and TAG_ENCODES_USE block text that belongs to whatever uses the records
(hypothesis labels, verdicts, benchmark case and item identifiers). On 2026-09-30 they also
rejected ordinary chemistry, because H2 is molecular hydrogen as well as a hypothesis label:
"favours H2 evolution", "stable against H2 reduction", a tag "h2". Both lists below are checked
on every change.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))

import validation  # noqa: E402

BASE = json.loads((REPO / "tests" / "adversarial" / "R00_control_valid.json").read_text())

CHEMISTRY = [
    "The surface favours H2 evolution at low overpotential.",
    "The catalyst is stable against H2 reduction up to 500 C.",
    "Pt supports H2 dissociation; the catalyst was reduced in H2 at 500 C.",
    "H2 is supported by spillover onto the ceria.",
    "H2's low solubility limits the rate.",
    "Case 3 of the reactor setup (case_3 in the lab log) was used.",
    "This record is included in the lab's 2025 dataset release.",
    "Samples were frozen at -80 C before XPS.",
    "The Heyrovsky mechanism H2 evolution step is rate limiting.",
]
REASONING = [
    "This is decisive against H4.",
    "H3's prediction fails for this catalyst.",
    "Supports hypothesis H2.",
    "The frozen-set record (case_16-LIT-5003) keeps the original.",
    "This record tests the residual hypothesis.",
]


def _codes(note=None, tag=None):
    r = copy.deepcopy(BASE)
    if note:
        r["sample"]["material"]["notes"] = note
    if tag:
        r["tags"] = [tag]
    return {e.get("code") for e in validation.validate_record_full(r)["errors"]}


@pytest.mark.parametrize("note", CHEMISTRY)
def test_ordinary_chemistry_is_not_reasoning(note):
    assert "REASONING_IN_RECORD" not in _codes(note=note), note


@pytest.mark.parametrize("note", REASONING)
def test_benchmark_and_hypothesis_reasoning_is_rejected(note):
    assert "REASONING_IN_RECORD" in _codes(note=note), note


def test_tags_name_data_not_use():
    for tag in ("h2", "frozen-samples", "hydrogen-evolution", "cu-ag-stripes-2026"):
        assert "TAG_ENCODES_USE" not in _codes(tag=tag), tag
    for tag in ("case_16-LIT-5003", "hypothesis-h2", "answer-key", "frozen-set"):
        assert "TAG_ENCODES_USE" in _codes(tag=tag), tag
