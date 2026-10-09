"""The record form renders and turns a lab's catalysis entry into a record, driven as a person would.

Runs the real Streamlit form (streamlit.testing) with the database stubbed. On 2026-09-30 the form crashed on
every load and no test saw it; on 2026-10-09 a typical CO2 reduction entry from its fields was rejected.
"""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

PORTAL = Path(__file__).resolve().parent.parent / "portal"

APP = f'''
import sys, types
sys.path.insert(0, {str(PORTAL)!r})
db = types.ModuleType("database")
db.test_db_connection = lambda: False
db.list_templates = lambda: []
sys.modules["database"] = db
import form
form.render_form()
'''


def _pick(at, kind, label):
    return next(el for el in getattr(at, kind) if el.label == label)


def test_the_form_loads_with_its_rules():
    at = AppTest.from_string(APP, default_timeout=60).run()
    assert not at.exception
    assert any("Before you enter a record" in i.value for i in at.info)


def test_a_co2rr_lab_entry_becomes_a_record_with_its_conditions_and_values():
    at = AppTest.from_string(APP, default_timeout=60).run()
    for kind, label, value in [
            ("selectbox", "Record Type *", "evidence"), ("selectbox", "Record Domain *", "performance"),
            ("selectbox", "Source Type *", "laboratory"), ("selectbox", "Domain", "experimental"),
            ("selectbox", "Technique *", "chronoamperometry"), ("selectbox", "Environment", "operando"),
            ("selectbox", "Temperature is", "stated"), ("selectbox", "Reaction", "CO2RR"),
            ("selectbox", "Drive", "electrochemical"), ("selectbox", "Catalysis", "heterogeneous"),
            ("selectbox", "Cell Type", "flow_cell"), ("selectbox", "Control mode", "potentiostatic"),
            ("selectbox", "Potential Scale", "RHE"), ("selectbox", "pH is", "nominal"),
            ("selectbox", "Sample Form", "film")]:
        _pick(at, kind, label).select(value)
    _pick(at, "radio", "Temperature unit").set_value("°C")
    for label, value in [("Temperature", 25.0), ("Potential (V, as reported)", -1.0),
                         ("Electrolyte concentration (M)", 1.0), ("pH", 6.8)]:
        _pick(at, "number_input", label).set_value(value)
    for label, value in [("Produced by: group *", "Example PI group"),
                         ("Material Name", "Cu nanoparticles on carbon paper"), ("Electrolyte at the working electrode", "KHCO3"),
                         ("Analysis or software that produced these values (optional)", "GC analysis script v2")]:
        _pick(at, "text_input", label).input(value)
    at.session_state["descriptor_rows"] = {
        "edited_rows": {0: {"name": "faradaic_efficiency.C2H4", "value": "0.428", "unit": "fraction", "uncertainty": ""}},
        "added_rows": [{"name": "faradaic_efficiency.H2", "value": "0.105", "unit": "fraction", "uncertainty": "0.008"}],
        "deleted_rows": []}
    _pick(at, "button", "Preview JSON").click()
    at.run()
    assert not at.exception and not at.error
    record = json.loads(at.json[-1].value)
    assert record["context"]["temperature_K"] == 298.15
    ec = record["context"]["electrochemistry"]
    assert ec["control_mode"] == "potentiostatic" and ec["potential_vs_RHE"]["value_V"] == -1.0
    assert ec["electrolyte"] == {"name": "KHCO3", "concentration_M": 1.0}
    values = record["descriptors"]["outputs"][0]["descriptors"]
    assert [d["uncertainty"].get("basis") for d in values] == ["not_reported", "reported"]
