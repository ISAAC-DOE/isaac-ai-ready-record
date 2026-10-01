"""Warnings for patterns a literature pipeline repeated on 2026-09-29, and three look-alike fixes.

One pipeline's 147 records each passed every hard rule and each came back with a warning; none
was acted on. Two patterns had no warning at all: a value the curator labels as quoted from a
review ("SECOND-HAND (review table)"), and a measurement condition in the sample name
("Ni-SiO2-ZrO2, 300 C at 50 bar"). Three legitimate look-alikes warned where they should not:
a batch or run label read as a citation, and a wedge film read as a series of samples. These
warnings are what a record must clear before the platform holds back what it cannot trust.
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


def _warnings(name=None, note=None):
    r = copy.deepcopy(BASE)
    if name is not None:
        r["sample"]["material"]["name"] = name
    if note is not None:
        r["sample"]["material"]["notes"] = note
    res = validation.validate_record_full(r)
    assert res["valid"], res["errors"][:2]
    return {w.get("code") for w in res.get("warnings") or []}


SECOND_HAND = [
    "SECOND-HAND (review table).",
    "SECOND-HAND: this value is quoted in a review article, not measured in the cited work.",
    "Conversion and selectivity taken from the review table (Table 3).",
    "Values read from a review article; the original study is cited there.",
    "Review-derived value: 1.2 V.",
    "Data from the review paper's summary table.",
]
NOT_SECOND_HAND = [
    "A second-hand potentiostat was used to record the data.",
    "A second-hand (refurbished) potentiostat was used.",
    "The phrase was quoted from a review article.",
    "The nomenclature was taken from a review paper.",
    "Caption copied from a review table.",
    "Reviewed by the authors before deposition.",
    "As summarized in a review by Smith (cited as reference), the trend holds across supports.",
    "The source is a primary study; a later review is cited as reference.",
    "Measured hand-in-hand with XPS on the same day.",
]
CONDITION_IN_NAME = [
    "Ni-SiO2-ZrO2, 300 C at 50 bar",
    "Pt/H-Beta, 400 C at 1 bar",
    "Pd/HZSM-5, cresol, batch at 20 bar / 200 C",
    "100 nm Cu film (-0.4 V)",
    "Au(111) at 300 K",
    "Cu catalyst at -1.1 V vs RHE",
    "IrO2 after 10 h at 1.6 V",
    "Pt at 300 °C/Ar",
    "Pt, 25 degrees C",
    "NCM811 cycled 2.5-4.3 V versus 2.5-4.2 V",
    "NMC, control electrolyte, 83 cycles to 4.3 V",
    "NMC cycled between 2.8 and 4.1 V",
    "Pt/zeolite, m-cresol at 673-723 K",
    "Ni catalyst stable to 900 C",
]
NAME_ONLY = [
    "TiO2 calcined at 500 °C",
    "Cu foil annealed at 400 C for 2 h",
    "NiFe LDH aged 24 h",
    "Pt/C (20 wt%)",
    "Pt3Ni/C",
    "IrO2 film, 50 nm",
    "K2CO3-promoted Fe",
    "Cu2O nanocubes reduced at 300 C",
    "Co3O4 spinel, hydrothermal synthesis at 180 C",
    "1.6 mm Ni foam",
    "3C-SiC",
    "Fe 3 C",
    "Cabot 300C carbon black",
    "Ti-6Al-4V",
    "Mo/MoS2 domains annealed 600-800 C",
    "Ru-18Ox sputter-deposited at 25 C",
    "Porous hollow PtNi/C (T=333K synthesis)",
]


@pytest.mark.parametrize("note", SECOND_HAND)
def test_a_value_labelled_as_quoted_from_a_review_warns(note):
    assert "SECOND_HAND_SOURCE" in _warnings(note=note), note


@pytest.mark.parametrize("note", NOT_SECOND_HAND)
def test_reviews_and_equipment_are_not_second_hand_values(note):
    assert "SECOND_HAND_SOURCE" not in _warnings(note=note), note


@pytest.mark.parametrize("name", CONDITION_IN_NAME)
def test_a_measurement_condition_in_the_sample_name_warns(name):
    assert "CONDITIONS_IN_SAMPLE_NAME" in _warnings(name=name), name


@pytest.mark.parametrize("name", NAME_ONLY)
def test_how_the_material_was_made_stays_in_the_name(name):
    assert "CONDITIONS_IN_SAMPLE_NAME" not in _warnings(name=name), name


@pytest.mark.parametrize("name", ["Cu foil (Batch 2023)", "IrOx film (Run 2024)", "Pt/C (Lot 2019)",
                                  "Cu foil (May 2024)", "Control film (Spring 2023)",
                                  "TiO2 film of varied thickness (wedge sample)",
                                  "Ni-Fe composition-spread film, varied Fe content"])
def test_labels_and_one_object_with_a_gradient_are_not_flagged(name):
    codes = _warnings(name=name)
    assert not codes & {"SAMPLE_NAME_CITES_A_PAPER", "SAMPLE_NOT_ONE_MATERIAL"}, (name, codes)


@pytest.mark.parametrize("name,code", [
    ("Pt3Ni/C prepared following Stamenkovic et al. 2007", "SAMPLE_NAME_CITES_A_PAPER"),
    ("Cu/ZnO (Behrens, 2012)", "SAMPLE_NAME_CITES_A_PAPER"),
    ("Cu/ZnO (Behrens et al. 2012)", "SAMPLE_NAME_CITES_A_PAPER"),
    ("Pt/C (Xue 2020)", "SAMPLE_NAME_CITES_A_PAPER"),
    ("Supported Pd catalysts, varied crystallite size", "SAMPLE_NOT_ONE_MATERIAL"),
])
def test_citations_and_sample_series_are_still_flagged(name, code):
    assert code in _warnings(name=name), name


def test_an_erratum_is_named_in_the_multiple_sources_remedy():
    r = copy.deepcopy(BASE)
    for doi in ("10.9999/probe.0001", "10.9999/probe.0099"):
        r["assets"].append({"asset_id": doi, "content_role": "documentation", "uri": f"https://doi.org/{doi}",
                            "sha256": "0" * 64, "citation": {"doi": doi, "relation": "source"}})
    msgs = [w["message"] for w in validation.validate_record_full(r).get("warnings") or []
            if w.get("code") == "MULTIPLE_SOURCES"]
    assert msgs and "an erratum or correction" in msgs[0]
