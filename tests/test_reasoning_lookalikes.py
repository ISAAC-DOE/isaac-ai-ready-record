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


# --- competing hypotheses named in a note (warning, 2026-10-01) -----------------------------------
# The live sentences, and the forms a curator may use for them.
RIVAL_HYPOTHESES = [
    "Supports the INTERFACE rival: CO2 activated at the In2O3-x/Cu-In boundary and hydrogenated there.",
    "Metal-interface rival. Selectivity RISES as conversion falls.",
    "Baseline for the vacancy-count rival",
    "XPS ruled out the vacancy-count rival.",
    "The trend favoured the Metal-interface rival",
]
# "Rival" for a competing material or technology, and conclusions the source states: three
# independent reviewers proposed these, and none may warn.
RIVAL_CHEMISTRY = [
    "activity rivaling commercial Pt/C", "rivals IrO2 in stability", "a rival to iridium oxide",
    "its closest rival", "a Pt-free rival to IrO2", "rival technologies such as PEM electrolysis",
    "outperforms its rivals", "the commercial rival catalyst", "Ni-Fe rival of Ir",
    "consistent with the Langmuir-Hinshelwood mechanism reported in the source",
    "the alternative explanation proposed by the authors is surface reconstruction",
    "Cu-free rival catalyst", "low-cost rival technology", "state-of-the-art rival material",
    "industry-leading rival", "a rival pathway to methanol", "rival of the commercial catalyst",
    "consistent with the alternative explanation proposed by the authors",
    "Baseline for rival technologies such as PEM electrolysis", "The rival catalyst lost 20 mV",
    "Ni-Fe rival showed a lower overpotential", "this Pt-free rival catalyst matches IrO2",
    "low-cost rival to commercial IrO2", "state-of-the-art rival in acid", "HER rival catalysts such as MoS2",
    "carbon-supported rival in the same cell", "Supports the hypothesis that the rate is first order",
    "XPS rules out the alternative explanation of residual nitrate",
    "Pt-free rival exhibited stability in acid", "Ni-Mo rival in alkaline media",
    "outperformed the state-of-the-art rival catalyst",
    "consistent with the Mars-van Krevelen hypothesis cited in the source",
    "provides an experimental baseline for future catalyst screening",
    "HER rival technologies such as AEM electrolysis", "tested against the Cu-based rival under identical conditions",
    "outperformed the Cu-Zn rival", "compared with the Fe-N-C rival.", "Data refutes prior claims. The rival catalyst is Pt.",
]


def _warning_codes(note):
    r = copy.deepcopy(BASE)
    r["sample"]["material"]["notes"] = note
    res = validation.validate_record_full(r)
    return res["valid"], {w.get("code") for w in res.get("warnings") or []}


@pytest.mark.parametrize("note", RIVAL_HYPOTHESES)
def test_a_named_competing_hypothesis_warns_and_is_accepted(note):
    valid, codes = _warning_codes(note)
    assert valid and "COMPETING_HYPOTHESIS_LANGUAGE" in codes, note


@pytest.mark.parametrize("note", RIVAL_CHEMISTRY)
def test_rival_as_chemistry_does_not_warn(note):
    valid, codes = _warning_codes(note)
    assert valid and "COMPETING_HYPOTHESIS_LANGUAGE" not in codes, note


def test_source_text_is_not_scanned():
    r = copy.deepcopy(BASE)
    r.setdefault("assets", []).append({"asset_id": "src", "content_role": "documentation", "uri": "https://doi.org/10.1000/x",
                                       "sha256": "x", "citation": {"title": "A Metal-interface rival.", "relation": "reference"}})
    assert "COMPETING_HYPOTHESIS_LANGUAGE" not in {w.get("code") for w in validation.validate_record_full(r).get("warnings") or []}
