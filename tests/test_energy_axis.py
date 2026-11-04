"""How a spectrum's energy axis was aligned and calibrated (measurement.energy_axis, ledger change 17): the
examples teach it without a warning, each check catches its case, and the look-alikes pass."""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))
sys.path.insert(0, str(REPO / "tools"))
import validation  # noqa: E402
from generate_validation_docs import REGISTRY  # noqa: E402

OCEAN = json.loads((REPO / "examples" / "ocean_xas_calibrated_record.json").read_text())
MEASURED = json.loads((REPO / "examples" / "ex_situ_xanes_cuo2_record.json").read_text())
SCHEMA = json.loads((REPO / "schema" / "isaac_record_v1.json").read_text())
VOCAB = json.loads((REPO / "data" / "vocabulary.json").read_text())
NEW_CODES = ("TOTAL_OFFSET_ADDED_TO_AXIS", "ENERGY_AXIS_NOT_STATED", "CALIBRATION_INCOMPLETE",
             "TIE_WITHOUT_SCOPE", "SCREENING_GRID_TOO_SMALL", "GAUGE_INCOMPLETE")


def _result(r):
    return validation.validate_record_full(r)


def _codes(r):
    res = _result(r)
    return {e.get("code") for e in res["errors"]} | {w["code"] for w in res.get("warnings", [])}


def _axis(r):
    return r["measurement"]["energy_axis"]


@pytest.mark.parametrize("name", ["ocean_xas_calibrated_record.json", "ex_situ_xanes_cuo2_record.json",
                                  "simulation_xas_record.json"])
def test_the_spectrum_examples_carry_the_block_and_no_warning(name):
    r = json.loads((REPO / "examples" / name).read_text())
    res = _result(r)
    assert res["outcome"] == "publish" and not res.get("warnings"), res.get("warnings")
    assert _axis(r)["status"] == "absolute_calibrated"


def test_every_new_check_is_documented_as_a_warning():
    for code in NEW_CODES:
        assert REGISTRY[code][0] == "warning", code


def test_the_closed_lists_are_served_by_the_vocabulary_as_the_schema_states_them():
    ea = SCHEMA["properties"]["measurement"]["properties"]["energy_axis"]["properties"]
    spec = SCHEMA["properties"]["computation"]["properties"]["spectroscopy"]["properties"]
    family = SCHEMA["properties"]["computation"]["properties"]["method"]["properties"]["family"]["enum"]
    assert VOCAB["Measurement"]["measurement.energy_axis.status"]["values"] == ea["status"]["enum"]
    assert VOCAB["Measurement"]["measurement.energy_axis.gauge.core_hole"]["values"] == ea["gauge"]["properties"]["core_hole"]["enum"]
    assert (VOCAB["Measurement"]["measurement.energy_axis.calibration.standard_acquisition"]["values"]
            == ea["calibration"]["properties"]["standard_acquisition"]["enum"])
    assert VOCAB["Computation"]["computation.spectroscopy.broadening.mode"]["values"] == spec["broadening"]["properties"]["mode"]["enum"]
    assert VOCAB["Computation"]["computation.method.family"]["values"] == family
    assert {"BSE", "multiple_scattering", "multiplet"} <= set(family)


# --- each check catches its case ------------------------------------------------------------------------

def test_a_total_offset_added_to_the_axis_is_caught():
    r = copy.deepcopy(OCEAN)
    _axis(r)["gauge"]["total_offset_added_to_axis"] = True
    assert "TOTAL_OFFSET_ADDED_TO_AXIS" in _codes(r)


@pytest.mark.parametrize("rmax,fires", [(11.5, True), (12.0, False), (9.5, True)])
def test_the_screening_grid_extends_more_than_two_bohr_beyond_the_shells(rmax, fires):
    r = copy.deepcopy(OCEAN)
    _axis(r)["gauge"]["screening_grid_rmax_bohr"] = rmax           # shells at 9.5 Bohr
    assert ("SCREENING_GRID_TOO_SMALL" in _codes(r)) is fires


@pytest.mark.parametrize("field", ["standard", "reference_value_eV", "reference_source"])
def test_a_calibration_without_its_parts_is_caught(field):
    r = copy.deepcopy(MEASURED)
    del _axis(r)["calibration"][field]
    assert "CALIBRATION_INCOMPLETE" in _codes(r)


@pytest.mark.parametrize("definition", [None, "not_reported", "  "])
def test_a_feature_without_its_definition_is_caught_and_the_record_is_still_stored(definition):
    r = copy.deepcopy(OCEAN)
    feature = _axis(r)["calibration"]["feature"]
    if definition is None:
        del feature["definition"]
    else:
        feature["definition"] = definition
    res = _result(r)
    assert res["valid"] and "CALIBRATION_INCOMPLETE" in {w["code"] for w in res["warnings"]}


def test_a_tie_without_its_scope_is_caught_and_one_with_it_passes():
    r = copy.deepcopy(OCEAN)
    tie = {"to_record_id": "01JFH8SPECXAS0000000000003", "offset_eV": -3.64, "sigma_eV": None,
           "scope": {"compound": "CuO", "edge": "Cu K"}}
    _axis(r)["ties"] = [tie]
    assert "TIE_WITHOUT_SCOPE" in _codes(r)
    tie["scope"]["feature"] = {"name": "first_peak", "definition": "first resolved peak at least 0.6 x maximum"}
    assert "TIE_WITHOUT_SCOPE" not in _codes(r)


def test_a_computed_energy_without_a_stated_axis_is_caught():
    r = copy.deepcopy(OCEAN)
    del r["measurement"]["energy_axis"]
    assert "ENERGY_AXIS_NOT_STATED" in _codes(r)


@pytest.mark.parametrize("status", ["aligned", "as_recorded"])
def test_an_axis_stated_as_not_calibrated_is_an_answer_and_draws_no_warning(status):
    r = copy.deepcopy(OCEAN)
    _axis(r)["status"] = status
    del _axis(r)["calibration"]
    _axis(r)["notes"] = "the source does not say how the axis was placed"
    assert not set(NEW_CODES) & _codes(r)


# --- look-alikes that must pass ----------------------------------------------------------------------------

def test_a_measured_absolute_energy_without_the_block_draws_no_warning():
    r = copy.deepcopy(MEASURED)
    del r["measurement"]["energy_axis"]
    assert not set(NEW_CODES) & _codes(r)


def test_a_computed_shift_against_a_reference_compound_needs_no_calibration():
    r = copy.deepcopy(OCEAN)
    _axis(r)["status"] = "aligned"
    del _axis(r)["calibration"]
    r["descriptors"]["outputs"][0]["descriptors"] = [{
        "name": "edge_shift.Cu_K", "kind": "differential", "source": "auto", "value": 4.4, "unit": "eV",
        "definition": "edge_position of this record minus that of the Cu metal anchor, both in this gauge",
        "relative_to": {"record_id": "01JFH8SPECXAS0000000000001", "operation": "difference"},
        "uncertainty": {"sigma": 0.1, "unit": "eV", "basis": "broadening_sweep"}}]
    assert _result(r)["valid"] and not set(NEW_CODES) & _codes(r)


def test_a_computed_peak_position_outside_energy_draws_no_warning():
    r = copy.deepcopy(OCEAN)
    del r["measurement"]["energy_axis"]
    r["descriptors"]["outputs"][0]["descriptors"] = [{
        "name": "peak_position.Cu_O_stretch", "kind": "absolute", "source": "auto", "value": 590.0, "unit": "cm-1",
        "definition": "computed vibrational frequency"}]
    assert "ENERGY_AXIS_NOT_STATED" not in _codes(r)


def test_the_bse_family_needs_its_core_hole_treatment_or_not_reported():
    r = copy.deepcopy(OCEAN)
    del r["computation"]["method"]["core_hole_treatment"]
    assert "COMPUTATION_METHOD_INCOMPLETE" in _codes(r)
    r["computation"]["method"]["core_hole_treatment"] = "not_reported"
    assert _result(r)["valid"]


def test_the_block_needs_a_status_and_a_tie_needs_its_target_and_offset():
    r = copy.deepcopy(OCEAN)
    del _axis(r)["status"]
    assert not _result(r)["valid"]
    r = copy.deepcopy(OCEAN)
    _axis(r)["ties"] = [{"offset_eV": 1.0}]
    assert not _result(r)["valid"]


@pytest.mark.parametrize("field", ["anchor_record_id", "core_offset_eV", "functional"])
def test_a_bse_gauge_without_its_anchor_offset_or_functional_is_caught(field):
    r = copy.deepcopy(OCEAN)
    del _axis(r)["gauge"][field]
    assert "GAUGE_INCOMPLETE" in _codes(r)


def test_a_gauge_is_not_asked_of_a_spectrum_as_recorded_or_of_another_family():
    r = copy.deepcopy(OCEAN)
    del _axis(r)["gauge"]["anchor_record_id"]
    _axis(r)["status"] = "as_recorded"
    assert "GAUGE_INCOMPLETE" not in _codes(r)
    r = copy.deepcopy(OCEAN)
    del _axis(r)["gauge"]
    r["computation"]["method"]["family"] = "DFT"
    assert "GAUGE_INCOMPLETE" not in _codes(r)


def test_a_computed_calibration_needs_its_shift_and_a_measured_one_does_not():
    r = copy.deepcopy(OCEAN)
    del _axis(r)["calibration"]["shift_applied_eV"]
    assert "CALIBRATION_INCOMPLETE" in _codes(r)
    r = copy.deepcopy(MEASURED)
    del _axis(r)["calibration"]["shift_applied_eV"]
    assert "CALIBRATION_INCOMPLETE" not in _codes(r)


def test_a_computed_energy_outside_x_ray_spectra_draws_no_warning():
    r = copy.deepcopy(OCEAN)
    del r["measurement"]["energy_axis"], r["computation"]["spectroscopy"]
    r["system"]["technique"] = "DFT"
    r["descriptors"]["outputs"][0]["descriptors"] = [{
        "name": "peak_position.d_band", "kind": "absolute", "source": "auto", "value": -2.1, "unit": "eV",
        "definition": "maximum of the projected d density of states, relative to the Fermi level",
        "uncertainty": {"sigma": 0.05, "unit": "eV", "basis": "method"}}]
    assert "ENERGY_AXIS_NOT_STATED" not in _codes(r)


def test_the_detection_of_a_measured_spectrum_is_its_own_and_the_standard_keeps_its_mode():
    ea = _axis(MEASURED)
    assert ea["detection"]["mode"] == "HERFD" and ea["calibration"]["standard_detection_mode"] == "transmission"
    assert "analyzer" not in ea["calibration"] and ea["calibration"]["standard_acquisition"] == "same_scan"
