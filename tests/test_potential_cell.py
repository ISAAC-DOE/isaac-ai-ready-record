"""
A potential on the axis belongs to THIS experiment; the cell is described.

On 2026-09-27 ten literature records carried -1.11 V_RHE as their measured potential. Their
source ran galvanostatically at 200 mA/cm2 in a gas-diffusion cell, reported only the
current, and stated -1.11 V_RHE for its DFT calculations; the curator put that number on the
experimental axis. The fix is honesty, not a better number: the record carries the current,
says the potential was not reported, and describes the cell completely (above all when it is
a two-electrode device). These tests pin the rules and the cases that must stay clean.
"""

import copy
import json
import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(PORTAL))

import validation  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CO2RR = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())
ORR = json.loads((REPO / "examples" / "echem_performance_record.json").read_text())
MEA = json.loads((REPO / "examples" / "electrolyzer_durability_record.json").read_text())


def _codes(record, key="errors"):
    return [e.get("code") for e in validation.validate_record_full(record).get(key) or []]


def _galvanostatic_gde(potential_definition=None):
    """A literature CO2RR record run at a fixed current in a gas-diffusion cell."""
    r = copy.deepcopy(CO2RR)
    r["source_type"] = "literature"
    r["assets"] = [{"asset_id": "source_paper", "content_role": "documentation",
                    "uri": "https://doi.org/10.0000/example", "sha256": "0" * 64,
                    "citation": {"title": "Example source", "year": 2026, "doi": "10.0000/example"}}]
    ec = r["context"]["electrochemistry"]
    ec["cell_type"] = "gde_cell"
    ec["control_mode"] = "galvanostatic"
    ec["current_setpoint_mA_cm2"] = -200.0
    ec.pop("potential_setpoint_V", None)
    ec["potential_vs_RHE"] = {"value_V": None, "rhe_basis": "not_reported"}
    r["measurement"]["series"] = []
    if potential_definition is not None:
        r["descriptors"]["outputs"][0]["descriptors"].append({
            "name": "steady_state_potential", "kind": "absolute", "source": "imported", "value": -1.11,
            "unit": "V_RHE", "uncertainty": {"sigma": None, "basis": "not_reported"},
            "definition": potential_definition})
    return r


# --- only this experiment's potential goes on the axis --------------------------------------

def test_a_current_only_record_that_says_so_is_valid():
    r = _galvanostatic_gde()
    res = validation.validate_record_full(r)
    assert res["valid"], res["errors"][:3]


def test_a_potential_stated_for_dft_calculations_is_rejected():
    r = _galvanostatic_gde("Potential stated by the authors for the DFT calculations, "
                           "converted from -1.8 V vs Ag/AgCl.")
    errs = [e for e in validation.validate_record_full(r)["errors"]
            if e["code"] == "POTENTIAL_FROM_OTHER_CONTEXT"]
    assert errs and "rhe_basis: 'not_reported'" in errs[0]["message"]


def test_the_source_s_own_conversion_to_rhe_is_not_mistaken_for_a_borrowed_value():
    r = _galvanostatic_gde("Cathode potential; converted from Ag/AgCl to RHE by the authors (E_RHE = E + 0.197 + "
                           "0.059 pH).")
    assert "POTENTIAL_FROM_OTHER_CONTEXT" not in _codes(r)


def test_a_measured_potential_with_an_ordinary_definition_is_clean():
    r = _galvanostatic_gde("Mean iR-corrected cathode potential over the last hour of the run.")
    r["context"]["electrochemistry"]["potential_vs_RHE"] = {"value_V": None, "rhe_basis": "not_reported"}
    assert "POTENTIAL_FROM_OTHER_CONTEXT" not in _codes(r)


def test_a_computational_record_may_state_its_model_potential():
    r = _galvanostatic_gde("Potential applied in the DFT calculations (computational hydrogen electrode).")
    r["system"]["domain"] = "computational"
    assert "POTENTIAL_FROM_OTHER_CONTEXT" not in _codes(r)


def test_silence_about_the_potential_is_rejected():
    r = _galvanostatic_gde()
    del r["context"]["electrochemistry"]["potential_vs_RHE"]
    errs = [e for e in validation.validate_record_full(r)["errors"]
            if e["code"] == "GALVANOSTATIC_NO_POTENTIAL"]
    assert errs and "Never fill it with a value from another context" in errs[0]["message"]


# --- the cell is described ----------------------------------------------------------------------

def test_a_performance_record_names_its_cell():
    r = copy.deepcopy(CO2RR)
    del r["context"]["electrochemistry"]["cell_type"]
    assert "CELL_TYPE_MISSING" in _codes(r)


def test_three_electrode_is_wiring_not_a_cell_body():
    r = copy.deepcopy(ORR)
    r["context"]["electrochemistry"]["cell_type"] = "three_electrode"
    assert "DEPRECATED_CELL_TYPE" in _codes(r, "warnings")
    assert validation.validate_record_full(r)["valid"]


def test_a_fed_cell_declares_its_feed():
    r = _galvanostatic_gde()
    del r["context"]["transport"]["feed"]
    assert "FEED_UNDECLARED" in _codes(r)


def test_the_mea_template_is_a_complete_device_description():
    res = validation.validate_record_full(MEA)
    assert res["valid"], res["errors"][:3]


def test_an_mea_without_membrane_or_area_is_incomplete():
    r = copy.deepcopy(MEA)
    del r["context"]["electrochemistry"]["membrane"]
    del r["sample"]["composition"]["membrane"]
    del r["sample"]["composition"]["active_area_cm2"]
    errs = [e for e in validation.validate_record_full(r)["errors"]
            if e["code"] == "FULL_CELL_DESCRIPTION_INCOMPLETE"]
    assert errs and "membrane" in errs[0]["message"] and "active area" in errs[0]["message"]


def test_a_two_electrode_device_has_no_half_cell_potential():
    r = copy.deepcopy(MEA)
    r["context"]["electrochemistry"]["potential_vs_RHE"] = {"value_V": -1.2, "rhe_basis": "reported_as_RHE"}
    errs = [e for e in validation.validate_record_full(r)["errors"]
            if e["code"] == "HALF_CELL_POTENTIAL_IN_FULL_CELL"]
    assert errs and "cell_voltage" in errs[0]["message"]


def test_a_full_cell_with_an_integrated_reference_may_report_a_half_cell_potential():
    r = copy.deepcopy(MEA)
    ec = r["context"]["electrochemistry"]
    ec["potential_vs_RHE"] = {"value_V": -1.2, "rhe_basis": "reported_as_RHE"}
    ec["reference_electrode"] = {"type": "other", "model": "integrated_RHE_wire"}
    assert "HALF_CELL_POTENTIAL_IN_FULL_CELL" not in _codes(r)
