"""
One home for the reaction, for every kind of chemistry.

The reaction used to exist only inside context.electrochemistry, so thermal, photo-,
homogeneous and enzymatic catalysis could not state one (198 performance records carried
none on 2026-09-27). It now lives in context.reaction {name, drive, catalysis}; the
electrochemistry field is deprecated but still read.
"""

import copy
import json
import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(PORTAL))

import ontology  # noqa: E402
import validation  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CO2RR = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())


def _codes(record, key="errors"):
    return [e.get("code") for e in validation.validate_record_full(record).get(key) or []]


def _thermal(name="NH3_synthesis", catalysis="heterogeneous"):
    """A thermal-catalysis performance record: no electrochemistry block at all."""
    r = copy.deepcopy(CO2RR)
    r["context"] = {"environment": "operando", "temperature_K": 673.15,
                    "reaction": {"name": name, "drive": "thermal", "catalysis": catalysis,
                                 "equation": "N2 + 3 H2 -> 2 NH3"}}
    r["system"]["technique"] = "GC"
    r["descriptors"]["outputs"][0]["descriptors"] = [
        {"name": "mass_specific_rate", "kind": "absolute", "source": "manual", "value": 1250.0,
         "unit": "umol/g/h", "uncertainty": {"sigma": None, "basis": "not_reported"},
         "at": {"pressure_bar": 50.0}},
        {"name": "apparent_activation_energy", "kind": "absolute", "source": "manual", "value": 85.0,
         "unit": "kJ/mol", "uncertainty": {"sigma": 5.0, "unit": "kJ/mol", "basis": "reported"}}]
    r["measurement"]["series"] = []
    return r


def test_the_co2rr_template_states_its_reaction_in_the_one_home():
    assert CO2RR["context"]["reaction"] == {"name": "CO2RR", "drive": "electrochemical",
                                            "catalysis": "heterogeneous"}
    assert "reaction" not in CO2RR["context"]["electrochemistry"]


def test_thermal_catalysis_is_expressible():
    r = _thermal()
    assert validation.validate_record_full(r)["valid"], validation.validate_record_full(r)["errors"][:3]


def test_homogeneous_catalysis_is_expressible():
    r = _thermal(name="hydroformylation", catalysis="homogeneous")
    r["context"]["reaction"]["equation"] = "RCH=CH2 + CO + H2 -> RCH2CH2CHO"
    assert validation.validate_record_full(r)["valid"]


def test_a_performance_record_without_a_reaction_is_rejected():
    r = _thermal()
    del r["context"]["reaction"]
    assert "MISSING_REACTION" in _codes(r)


def test_a_legacy_only_record_is_told_exactly_how_to_move_it():
    r = copy.deepcopy(CO2RR)
    del r["context"]["reaction"]
    r["context"]["electrochemistry"]["reaction"] = "CO2RR"
    res = validation.validate_record_full(r)
    err = next(e for e in res["errors"] if e.get("code") == "MISSING_REACTION")
    assert "name: 'CO2RR', drive: 'electrochemical', catalysis: 'heterogeneous'" in err["message"]


def test_the_deprecated_field_warns_and_must_agree():
    r = copy.deepcopy(CO2RR)
    r["context"]["electrochemistry"]["reaction"] = "CO2RR"
    assert "REACTION_FIELD_DEPRECATED" in _codes(r, "warnings")
    assert validation.validate_record_full(r)["valid"]
    r["context"]["electrochemistry"]["reaction"] = "CORR"
    assert "REACTION_MISMATCH" in _codes(r)


def test_an_electrochemical_drive_needs_its_cell():
    r = _thermal()
    r["context"]["reaction"]["drive"] = "electrochemical"
    assert "REACTION_DRIVE_INCONSISTENT" in _codes(r)


def test_an_electrochemical_cell_cannot_have_a_thermal_drive():
    r = copy.deepcopy(CO2RR)
    r["context"]["reaction"]["drive"] = "thermal"
    assert "REACTION_DRIVE_INCONSISTENT" in _codes(r)


def test_an_invented_reaction_token_is_rejected_by_the_vocabulary():
    r = _thermal(name="magic_conversion")
    res = validation.validate_record_full(r)
    assert not res["vocabulary_valid"]
    assert any("magic_conversion" in e["message"] for e in res["vocabulary_errors"])


def test_the_sign_convention_reads_the_reaction_from_its_new_home():
    r = copy.deepcopy(CO2RR)
    target = next(d for d in r["descriptors"]["outputs"][0]["descriptors"]
                  if d["name"] == "partial_current_density.C2H4")
    target["value"] = abs(target["value"])
    assert "SIGN_CONVENTION" in _codes(r)


def test_a_record_that_is_not_about_a_reaction_needs_none():
    xrd = json.loads((REPO / "examples" / "ex_situ_xanes_cuo2_record.json").read_text())
    assert "reaction" not in (xrd.get("context") or {})
    assert "MISSING_REACTION" not in _codes(xrd)


def test_the_served_schema_offers_the_reaction_vocabulary():
    served = ontology.merge_vocabulary_into_schema(validation.ISAAC_SCHEMA)
    rx = served["properties"]["context"]["properties"]["reaction"]["properties"]
    assert "NH3_synthesis" in rx["name"]["enum"]
    assert set(rx["drive"]["enum"]) >= {"electrochemical", "thermal", "photochemical"}
    assert set(rx["catalysis"]["enum"]) == {"heterogeneous", "homogeneous", "enzymatic", "uncatalyzed"}


def test_the_list_filter_and_engine_read_both_homes():
    for f in ("database.py", "discovery.py"):
        src = (PORTAL / f).read_text()
        assert "'reaction'->>'name'" in src, f


def test_a_calculation_of_an_electrochemical_reaction_needs_no_cell():
    neb = json.loads((REPO / "examples" / "dft_neb_evidence_record.json").read_text())
    neb["context"]["reaction"] = {"name": "CO2RR", "drive": "electrochemical", "catalysis": "heterogeneous"}
    neb["context"].pop("electrochemistry", None)
    assert "REACTION_DRIVE_INCONSISTENT" not in _codes(neb)
