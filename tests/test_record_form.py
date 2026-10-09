"""The portal's record form can enter a catalysis result from a lab without writing JSON (2026-10-09).

Before this, a typical entry (a CO2 reduction run with its reaction, cell and potential scale chosen) was
rejected: the form had no control mode, no potential, current, electrolyte or pH field, one value only, no
'generated_by' or uncertainty unless typed, a free-text value source and link basis that the schema rejects,
and a checksum of zeros on every literature source.
"""
import datetime
import json
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))
try:
    import streamlit  # noqa: F401  the real one where it is installed, so the UI tests can still import it
except ImportError:
    sys.modules["streamlit"] = types.ModuleType("streamlit")  # the builder needs no UI

import form  # noqa: E402
import validation  # noqa: E402

FIELDS = ("acquired_end_date acquired_end_time acquired_start_date acquired_start_time asset_id asset_media_type "
          "asset_role asset_uri asset_sha256 channel_name channel_role channel_unit channel_values composition_json "
          "configuration_json context_additional_json created_date created_time desc_kind desc_name desc_source "
          "desc_uncertainty desc_unit desc_value echem_cell_type echem_potential_scale echem_reaction environment "
          "geometry_json ind_var_name ind_var_unit ind_var_values instrument_id instrument_name instrument_type "
          "link_basis link_notes link_rel link_target material_formula material_name material_provenance "
          "output_generated_by output_label processing_json produced_by_group produced_by_org qc_details_json qc_status "
          "reaction_catalysis reaction_drive record_domain record_id record_type sample_form series_id source_doi "
          "source_type system_domain system_technique tags temperature_k").split()


def _entry(**changes):
    kw = {k: None for k in FIELDS}
    kw.update(extra_vocab={}, record_id="01K9FORMTEST00000000000000", record_type="evidence",
              source_type="laboratory", produced_by_group="Example PI group", produced_by_org="Example University",
              created_date=datetime.date(2026, 10, 9), created_time=datetime.time(12, 0))
    kw.update(changes)
    return form.build_record(**kw)


CO2RR_LAB = dict(
    record_domain="performance", material_name="Cu nanoparticles on carbon paper", material_formula="Cu",
    material_provenance="synthesized", sample_form="film", system_domain="experimental",
    system_technique="chronoamperometry", environment="operando", temperature_k=298.15, temperature_basis="stated",
    echem_reaction="CO2RR", reaction_drive="electrochemical", reaction_catalysis="heterogeneous",
    echem_cell_type="flow_cell", control_mode="potentiostatic", potential_V="-1.0", echem_potential_scale="RHE",
    electrolyte_name="KHCO3", electrolyte_concentration_M="1.0", pH="6.8", pH_basis="nominal",
    descriptor_rows=[
        {"name": "faradaic_efficiency.H2", "value": "0.105", "unit": "fraction", "uncertainty": "0.008"},
        {"name": "faradaic_efficiency.CO", "value": "0.152", "unit": "fraction", "uncertainty": "0.005"},
        {"name": "faradaic_efficiency.C2H4", "value": "0.428", "unit": "fraction", "uncertainty": ""},
        {"name": "steady_state_current_density", "value": "-150.5", "unit": "mA/cm2", "uncertainty": "2.1"},
    ])


def _errors(record):
    result = validation.validate_record_full(record)
    return result["outcome"], [e.get("code") or e.get("message") for e in result.get("errors") or []]


def test_a_co2rr_lab_entry_from_the_fields_publishes():
    record = _entry(**CO2RR_LAB)
    assert _errors(record) == ("publish", [])
    ec = record["context"]["electrochemistry"]
    assert ec["control_mode"] == "potentiostatic" and ec["potential_setpoint_V"] == -1.0
    assert ec["potential_vs_RHE"] == {"value_V": -1.0, "rhe_basis": "reported_as_RHE"}
    assert ec["electrolyte"] == {"name": "KHCO3", "concentration_M": 1.0} and ec["pH"] == 6.8
    values = record["descriptors"]["outputs"][0]["descriptors"]
    assert len(values) == 4 and all(d["source"] == "manual" for d in values)
    assert values[2]["uncertainty"] == {"basis": "not_reported"}
    assert values[0]["uncertainty"] == {"sigma": 0.008, "unit": "fraction", "basis": "reported"}


def test_a_potential_on_another_scale_is_kept_as_given_and_never_converted():
    record = _entry(**dict(CO2RR_LAB, echem_potential_scale="Ag/AgCl", potential_V="-1.6", reference_electrode="Ag/AgCl"))
    ec = record["context"]["electrochemistry"]
    assert ec["potential_setpoint_V"] == -1.6 and ec["reference_electrode"] == {"type": "Ag/AgCl"}
    assert "potential_vs_RHE" not in ec
    assert _errors(record)[0] == "publish"


def test_a_galvanostatic_run_takes_its_current():
    record = _entry(**dict(CO2RR_LAB, control_mode="galvanostatic", potential_V="-1.1", current_mA_cm2="-200"))
    ec = record["context"]["electrochemistry"]
    assert ec["current_setpoint_mA_cm2"] == -200 and "potential_setpoint_V" not in ec
    assert ec["potential_vs_RHE"]["value_V"] == -1.1
    assert _errors(record)[0] == "publish"


def test_a_thermal_catalysis_entry_takes_pressure_and_feed():
    record = _entry(record_domain="performance", material_name="Pt/Al2O3", sample_form="powder",
                    system_domain="experimental", system_technique="catalytic_reactor_test", environment="in_situ",
                    temperature_k=473.15, temperature_basis="stated", echem_reaction="CO_oxidation",
                    reaction_drive="thermal", reaction_catalysis="heterogeneous", pressure_bar=1.0,
                    feed_phase="gas", feed_composition="1% CO, 1% O2 in He", flow_rate="50", flow_rate_unit="mL/min",
                    descriptor_rows=[{"name": "conversion.CO", "value": "0.42", "unit": "fraction", "uncertainty": ""}])
    ctx = record["context"]
    assert ctx["thermodynamics"]["pressure_Pa"] == 100000.0
    assert ctx["transport"]["feed"] == {"phase": "gas", "composition": "1% CO, 1% O2 in He",
                                        "flow_rate": 50.0, "flow_rate_unit": "mL/min"}
    outcome, errors = _errors(record)
    assert not errors, errors


def test_a_spectrum_of_a_reference_material_needs_no_reaction():
    record = _entry(record_domain="characterization", material_name="Copper(II) oxide reference", material_formula="CuO",
                    material_provenance="commercial", sample_form="pellet", system_domain="experimental",
                    system_technique="XAS", environment="ex_situ", temperature_basis="room_temperature",
                    descriptor_rows=[{"name": "edge_energy.Cu_K", "value": "8986.5", "unit": "eV", "uncertainty": ""}])
    assert record["context"]["temperature_K"] == 298.15
    outcome, errors = _errors(record)
    assert not errors, errors


def test_a_literature_source_takes_the_literature_placeholder_and_its_relation():
    record = _entry(**dict(CO2RR_LAB, source_type="literature", source_doi="https://doi.org/10.1002/advs.202520469"))
    paper = next(a for a in record["assets"] if a["asset_id"] == "source_paper")
    assert paper["sha256"] == "not_available_literature_source" and paper["citation"]["relation"] == "source"
    codes = {w["code"] for w in validation.validate_record_full(record).get("warnings") or []}
    assert "CHECKSUM_NOT_SHA256" not in codes


def test_an_asset_without_a_checksum_says_it_is_not_available():
    record = _entry(**dict(CO2RR_LAB, asset_id="raw", asset_role="raw_data", asset_uri="s3://lab/run_042.zip"))
    raw = next(a for a in record["assets"] if a["asset_id"] == "raw")
    assert raw["sha256"] == "not_available"


def test_a_galvanostatic_potential_on_another_scale_is_kept_as_a_result_on_its_scale():
    record = _entry(**dict(CO2RR_LAB, control_mode="galvanostatic", potential_V="-1.9", current_mA_cm2="-200",
                           echem_potential_scale="Ag/AgCl", reference_electrode="Ag/AgCl"))
    ec = record["context"]["electrochemistry"]
    assert "potential_setpoint_V" not in ec and "potential_vs_RHE" not in ec
    row = record["descriptors"]["outputs"][0]["descriptors"][-1]
    assert row["name"] == "steady_state_potential" and row["value"] == -1.9 and "Ag/AgCl" in row["definition"]
    assert _errors(record)[0] == "publish"


def test_a_current_typed_for_a_potentiostatic_run_is_refused_with_its_fix():
    kw = dict(CO2RR_LAB, current_mA_cm2="-150")
    record = _entry(**kw)
    assert "current_setpoint_mA_cm2" not in record["context"]["electrochemistry"]
    problems = form.entry_problems(kw)
    assert len(problems) == 1 and "measured current density in Results" in problems[0]


def test_an_uncertainty_typed_with_plus_minus_is_read_and_text_is_refused():
    rows = [{"name": "faradaic_efficiency.CO", "value": "0.15", "unit": "fraction", "uncertainty": "±0.01"},
            {"name": "faradaic_efficiency.H2", "value": "0.10", "unit": "fraction", "uncertainty": "+/- 0.02"}]
    record = _entry(**dict(CO2RR_LAB, descriptor_rows=rows))
    assert [d["uncertainty"]["sigma"] for d in record["descriptors"]["outputs"][0]["descriptors"]] == [0.01, 0.02]
    bad = [{"name": "faradaic_efficiency.CO", "value": "0.15", "unit": "fraction", "uncertainty": "about 1%"}]
    assert any("not a number" in p for p in form.entry_problems({"descriptor_rows": bad}))


def test_a_feed_without_its_phase_is_asked_for_and_never_defaulted():
    kw = dict(feed_composition="5% H2 in Ar")
    assert form._feed(kw) == {"composition": "5% H2 in Ar"}
    assert any("feed phase" in p for p in form.entry_problems(kw))


def test_ir_compensation_area_loading_and_celsius():
    record = _entry(**dict(CO2RR_LAB, ir_method="automatic", ir_percent="85", ir_corrected="yes",
                           electrode_type="GDE", geometric_area_cm2="1.0", catalyst_loading_mg_cm2="1.5",
                           temperature_k="25", temperature_unit="°C"))
    ec = record["context"]["electrochemistry"]
    assert ec["ir_compensation"] == {"method": "automatic", "percent": 85.0, "applied_to_reported_potential": True}
    assert ec["potential_vs_RHE"]["ir_corrected"] == "yes"
    sample = record["sample"]
    assert sample["electrode_type"] == "GDE" and sample["geometry"]["geometric_area_cm2"] == 1.0
    assert sample["composition"]["catalyst_loading_mg_cm2"] == 1.5
    assert record["context"]["temperature_K"] == 298.15
    assert _errors(record) == ("publish", [])


def test_the_single_value_fields_still_work():
    record = _entry(**dict(CO2RR_LAB, descriptor_rows=None, desc_name="faradaic_efficiency.CO", desc_value="0.15",
                           desc_unit="fraction", desc_kind="absolute", desc_source="DFT calculation"))
    (value,) = record["descriptors"]["outputs"][0]["descriptors"]
    assert value["source"] == "manual" and value["uncertainty"] == {"basis": "not_reported"}
