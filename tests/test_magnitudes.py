"""Classes stored as a non-negative magnitude, and the electrode types agents could not name.

On 2026-09-30 four agents built records from one fictional HER paper: three stored its 38 mV
overpotential as +0.038 V and one as -0.038 V, and all of them found no electrode type for Ni foam.
"""
import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))

import validation  # noqa: E402

BASE = json.loads((REPO / "tests" / "adversarial" / "R00_control_valid.json").read_text())
VOCAB = json.loads((REPO / "data" / "vocabulary.json").read_text())


def _with_descriptor(name, value, unit):
    r = copy.deepcopy(BASE)
    r["descriptors"]["outputs"][0]["descriptors"].append({
        "name": name, "kind": "absolute", "source": "imported", "value": value, "unit": unit,
        "uncertainty": {"sigma": None, "basis": "not_reported"}})
    return r


def _warnings(r):
    return {w["code"] for w in validation.validate_record_full(r).get("warnings") or []}


def test_every_magnitude_class_is_a_canonical_class():
    assert validation.MAGNITUDE_CLASSES
    assert validation.MAGNITUDE_CLASSES <= validation.CANONICAL_CLASSES


def test_a_negative_magnitude_warns_and_is_accepted():
    r = _with_descriptor("tafel_slope", -120.0, "mV/dec")
    assert "NEGATIVE_MAGNITUDE" in _warnings(r)
    assert validation.validate_record_full(r)["valid"]


def test_a_positive_magnitude_and_a_signed_class_do_not_warn():
    assert "NEGATIVE_MAGNITUDE" not in _warnings(_with_descriptor("tafel_slope", 120.0, "mV/dec"))
    assert "NEGATIVE_MAGNITUDE" not in _warnings(_with_descriptor("adsorption_energy.CO", -1.62, "eV"))


def test_foam_is_an_electrode_type():
    assert "foam" in VOCAB["Sample"]["sample.electrode_type"]["values"]
