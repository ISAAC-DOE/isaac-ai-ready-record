"""The transition operator of a computed spectrum, and the momentum transfer of X-ray Raman scattering (ledger change
21): the examples carry them with no warning, each check catches its case, and the look-alikes pass."""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))
import validation  # noqa: E402

XAS = json.loads((REPO / "examples" / "ocean_xas_calibrated_record.json").read_text())
XRS = json.loads((REPO / "examples" / "ocean_xrs_record.json").read_text())
MEASURED = json.loads((REPO / "examples" / "ex_situ_xanes_cuo2_record.json").read_text())
SCHEMA = json.loads((REPO / "schema" / "isaac_record_v1.json").read_text())
VOCAB = json.loads((REPO / "data" / "vocabulary.json").read_text())
CODES = {"XRS_TECHNIQUE_MISMATCH", "XRS_WITHOUT_Q", "QUADRUPOLE_WITHOUT_WAVE_VECTOR", "Q_CONVERSION_MISMATCH"}


def _codes(r):
    return {w["code"] for w in validation.validate_record_full(r).get("warnings", [])}


def _tr(r):
    return r["computation"]["spectroscopy"]["transition"]


@pytest.mark.parametrize("record", [XAS, XRS], ids=["xas", "xrs"])
def test_the_examples_carry_their_operator_and_no_warning(record):
    res = validation.validate_record_full(record)
    assert res["outcome"] == "publish" and not res.get("warnings"), res.get("warnings")
    assert _tr(record)["operator"] in ("dipole_quadrupole", "momentum_transfer")


def test_xrs_is_a_technique_and_the_operator_list_is_served():
    assert "XRS" in SCHEMA["properties"]["system"]["properties"]["technique"]["enum"]
    assert "XRS" in VOCAB["System"]["system.technique"]["values"]
    enum = SCHEMA["properties"]["computation"]["properties"]["spectroscopy"]["properties"]["transition"]["properties"]["operator"]["enum"]
    assert VOCAB["Computation"]["computation.spectroscopy.transition.operator"]["values"] == enum


@pytest.mark.parametrize("technique,operator", [("XRS", "dipole"), ("XAS", "momentum_transfer"), ("HERFD-XAS", "momentum_transfer")])
def test_a_technique_the_operator_contradicts_is_caught(technique, operator):
    r = copy.deepcopy(XRS if technique == "XRS" else XAS)
    r["system"]["technique"] = technique
    _tr(r)["operator"] = operator
    assert "XRS_TECHNIQUE_MISMATCH" in _codes(r)


def test_xrs_without_q_is_caught_for_a_calculation_and_for_a_measurement():
    r = copy.deepcopy(XRS)
    del _tr(r)["q_inverse_angstrom"], _tr(r)["q_as_written"]
    assert "XRS_WITHOUT_Q" in _codes(r)
    m = copy.deepcopy(MEASURED)
    m["system"]["technique"] = "XRS"
    m["measurement"]["energy_axis"]["detection"] = {"mode": "XRS", "scattering_angle_deg": 120.0}
    assert "XRS_WITHOUT_Q" in _codes(m)
    m["measurement"]["energy_axis"]["detection"]["q_inverse_angstrom"] = 9.1
    assert "XRS_WITHOUT_Q" not in _codes(m)


def test_a_quadrupole_without_its_wave_vectors_is_caught():
    r = copy.deepcopy(XAS)
    _tr(r)["vectors"] = [{"k": [0, 1, 0]}, {"e": [0, 1, 0], "k": [0, 0, 1]}]
    assert "QUADRUPOLE_WITHOUT_WAVE_VECTOR" in _codes(r)
    _tr(r)["vectors"] = [{"e": [1, 0, 0]}, {"e": [0, 1, 0], "k": [0, 0, 1]}]
    assert "QUADRUPOLE_WITHOUT_WAVE_VECTOR" in _codes(r)
    del _tr(r)["vectors"]
    assert "QUADRUPOLE_WITHOUT_WAVE_VECTOR" in _codes(r)


def test_a_dipole_needs_no_wave_vector():
    r = copy.deepcopy(XAS)
    _tr(r)["operator"] = "dipole"
    _tr(r)["vectors"] = [{"e": [1, 0, 0]}]
    assert not CODES & _codes(r)


@pytest.mark.parametrize("value,unit,q,fires", [
    (1.85, "inverse_bohr", 3.496, False),     # the example, converted right
    (1.85, "inverse_bohr", 3.5, False),       # rounded to the precision reported
    (1.85, "inverse_bohr", 1.85, True),       # inverse bohr read as inverse angstrom
    (1.85, "inverse_angstrom", 3.496, True),  # the reverse slip
    (34.96, "inverse_nm", 3.496, False),
])
def test_a_q_whose_two_statements_disagree_is_caught(value, unit, q, fires):
    r = copy.deepcopy(XRS)
    _tr(r)["q_as_written"], _tr(r)["q_inverse_angstrom"] = {"value": value, "unit": unit}, q
    assert ("Q_CONVERSION_MISMATCH" in _codes(r)) is fires


def test_xrs_without_an_energy_axis_is_flagged_like_any_computed_x_ray_spectrum():
    r = copy.deepcopy(XRS)
    del r["measurement"]["energy_axis"]
    r["descriptors"]["outputs"][0]["descriptors"].append({
        "name": "white_line_energy.O_K", "kind": "absolute", "source": "auto", "value": 532.0, "unit": "eV",
        "definition": "maximum of the site-averaged spectrum", "uncertainty": {"basis": "method"}})
    assert "ENERGY_AXIS_NOT_STATED" in _codes(r)


def test_a_measured_xrs_called_xas_is_caught():
    m = copy.deepcopy(MEASURED)
    m["measurement"]["energy_axis"]["detection"] = {"mode": "XRS", "q_inverse_angstrom": 8.5, "scattering_angle_deg": 120.0}
    assert "XRS_TECHNIQUE_MISMATCH" in _codes(m)
    m["system"]["technique"] = "XRS"
    assert "XRS_TECHNIQUE_MISMATCH" not in _codes(m)


def test_the_momentum_transfer_operator_needs_q_whatever_the_label():
    r = copy.deepcopy(XRS)
    r["system"]["technique"] = "XAS"
    del _tr(r)["q_inverse_angstrom"], _tr(r)["q_as_written"]
    assert {"XRS_TECHNIQUE_MISMATCH", "XRS_WITHOUT_Q"} <= _codes(r)


def test_the_momentum_transfer_operator_under_another_technique_is_caught():
    r = copy.deepcopy(XRS)
    r["system"]["technique"] = "XES"
    assert "XRS_TECHNIQUE_MISMATCH" in _codes(r)


@pytest.mark.parametrize("change", ["parallel", "no_energy"])
def test_a_quadrupole_with_e_along_k_or_without_its_energy_is_caught(change):
    r = copy.deepcopy(XAS)
    if change == "parallel":
        _tr(r)["vectors"] = [{"e": [1, 0, 0], "k": [1, 0, 0]}]
    else:
        del _tr(r)["energy_eV"]
    assert "QUADRUPOLE_WITHOUT_WAVE_VECTOR" in _codes(r)
