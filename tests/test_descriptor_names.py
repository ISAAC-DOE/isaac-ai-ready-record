"""
Descriptor names carry the quantity; conditions, reactions and techniques live in structured slots.

The 2026-09-27 repository audit counted 813 descriptor names for 5,852 values (10% canonical):
uploaders and their agents wrote the reaction (orr_), the technique (xanes., dft_), the
condition (_at_10mA_cm2, _0p9V, _600c) and the unit (mV vs V) into the name, so the same
quantity got a new name in every upload and canonical queries returned nothing. These tests
pin the rules that stop that at ingestion, the teaching messages, and the cases that must
NOT fire (product tokens, element qualifiers, organization namespaces).
"""

import copy
import json
import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(PORTAL))

import validation  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
ORR = json.loads((REPO / "examples" / "echem_performance_record.json").read_text())
CO2RR = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())


def _with(base, **descriptor):
    """The base record with one extra descriptor appended to its first output block."""
    r = copy.deepcopy(base)
    d = {"kind": "absolute", "source": "manual", "uncertainty": {"sigma": None, "basis": "not_reported"}}
    d.update(descriptor)
    r["descriptors"]["outputs"][0]["descriptors"].append(d)
    return r


def _errors(record, code=None):
    errs = validation.validate_record_full(record)["errors"]
    return [e for e in errs if code is None or e.get("code") == code]


# --- the read-out point belongs in `at` ------------------------------------------------------

def test_overpotential_at_a_current_density_is_expressible_on_a_sweep():
    r = _with(ORR, name="overpotential", value=0.36, unit="V", at={"current_density_mA_cm2": -3.0})
    assert _errors(r) == []


def test_the_orr_template_teaches_read_out_points():
    ats = {d["name"]: d.get("at") for d in ORR["descriptors"]["outputs"][0]["descriptors"]}
    assert ats["mass_activity"] == {"potential_V_RHE": 0.9}
    assert ats["onset_potential"] == {"current_density_mA_cm2": -0.1}


def test_a_read_out_key_on_a_single_operating_point_is_rejected():
    r = _with(CO2RR, name="overpotential", value=0.5, unit="V", at={"potential_V_RHE": -1.1})
    errs = _errors(r, "AT_READOUT_WITHOUT_SWEEP")
    assert errs and "potential_setpoint_V" in errs[0]["message"]


def test_a_positive_read_out_current_under_a_cathodic_reaction_violates_the_sign_convention():
    r = _with(ORR, name="overpotential", value=0.36, unit="V", at={"current_density_mA_cm2": 3.0})
    assert _errors(r, "SIGN_CONVENTION")


# --- conditions, reactions and techniques never go into the name ------------------------------

def test_a_condition_in_the_name_is_rejected_with_a_structured_alternative():
    r = _with(ORR, name="overpotential_at_10mA_cm2", value=0.36, unit="V")
    errs = _errors(r, "CONDITION_IN_DESCRIPTOR_NAME")
    assert errs and "'overpotential' with at.current_density_mA_cm2" in errs[0]["message"]


def test_potential_temperature_and_time_conditions_are_caught():
    for name in ("mass_activity_0p9V", "h_loading.x_at_minus0p38V", "coke_content_600c", "cu0_fraction_15min"):
        r = _with(ORR, name=name, value=1.0, unit="dimensionless")
        assert _errors(r, "CONDITION_IN_DESCRIPTOR_NAME"), name


def test_a_reaction_prefix_is_rejected_and_the_canonical_class_suggested():
    r = _with(ORR, name="orr_half_wave_potential", value=0.84, unit="V_RHE")
    errs = _errors(r, "PREFIX_IN_DESCRIPTOR_NAME")
    assert errs and "Use 'half_wave_potential'" in errs[0]["message"]


def test_a_technique_namespace_is_rejected_and_the_canonical_class_suggested():
    r = _with(ORR, name="xanes.edge_position", value=8980.4, unit="eV")
    errs = _errors(r, "PREFIX_IN_DESCRIPTOR_NAME")
    assert errs and "Use 'edge_position'" in errs[0]["message"]


def test_a_method_prefix_on_a_computed_quantity_points_to_class_plus_qualifier():
    r = _with(ORR, name="dft_o2_adsorption_energy", value=-0.8, unit="eV")
    errs = _errors(r, "PREFIX_IN_DESCRIPTOR_NAME")
    assert errs and "adsorption_energy.o2" in errs[0]["message"]


def test_an_uppercase_class_is_rejected_and_the_qualifier_form_taught():
    r = _with(ORR, name="Cu_thickness", value=250.0, unit="angstrom")
    errs = _errors(r, "DESCRIPTOR_CLASS_NOT_LOWERCASE")
    assert errs and "layer_thickness.Cu" in errs[0]["message"]


def test_a_deprecated_class_spelling_names_its_replacement():
    r = _with(ORR, name="activation_energy", value=0.7, unit="eV")
    errs = _errors(r, "DESCRIPTOR_CLASS_ALIAS")
    assert errs and "activation_barrier" in errs[0]["message"]


def test_a_repeated_name_in_one_block_is_rejected():
    r = _with(ORR, name="half_wave_potential", value=0.85, unit="V_RHE")
    assert _errors(r, "DUPLICATE_DESCRIPTOR_NAME")


# --- one class, one unit ------------------------------------------------------------------------

def test_a_class_in_the_wrong_unit_is_rejected():
    r = _with(ORR, name="overpotential", value=360.0, unit="mV", at={"current_density_mA_cm2": -3.0})
    errs = _errors(r, "CLASS_UNIT_MISMATCH")
    assert errs and "['V']" in errs[0]["message"]


def test_a_known_respelling_is_reported_once_as_an_alias_not_twice():
    r = copy.deepcopy(ORR)
    tafel = next(d for d in r["descriptors"]["outputs"][0]["descriptors"] if d["name"] == "tafel_slope")
    tafel["unit"] = "mV_per_dec"
    errs = _errors(r)
    assert any("'mV/dec'" in e["message"] for e in errs)
    assert not _errors(r, "CLASS_UNIT_MISMATCH")


def test_a_categorical_value_is_not_unit_checked():
    r = _with(ORR, name="oxidation_state.Pt", value="Pt(0)-like", kind="categorical",
              uncertainty={"confidence": 0.8})
    assert not _errors(r, "CLASS_UNIT_MISMATCH")


# --- what must NOT fire ----------------------------------------------------------------------------

def test_product_tokens_element_qualifiers_and_organization_namespaces_are_clean():
    names = [("faradaic_efficiency.C2H4", 0.3, "fraction"), ("faradaic_efficiency.n_C3H7OH", 0.02, "fraction"),
             ("partial_current_density.C2plus", -12.0, "mA/cm2"), ("binding_energy.Cu_2p3_2", 932.6, "eV"),
             ("oxidation_state.Cu", 1.0, "dimensionless"), ("lattice_parameter.Pd_fcc", 3.89, "angstrom"),
             ("lbnl.xps.elements_detected", "C; Au", None), ("edge_position.Cu_K", 8979.0, "eV")]
    for name, value, unit in names:
        r = _with(CO2RR, name=name, value=value, unit=unit)
        naming = [e for e in _errors(r) if e.get("code") in (
            "CONDITION_IN_DESCRIPTOR_NAME", "PREFIX_IN_DESCRIPTOR_NAME", "DESCRIPTOR_CLASS_NOT_LOWERCASE",
            "DESCRIPTOR_CLASS_ALIAS", "CLASS_UNIT_MISMATCH")]
        assert not naming, (name, naming)


def test_every_canonical_example_is_clean_under_the_naming_rules():
    for path in sorted((REPO / "examples").glob("*.json")):
        assert validation._descriptor_name_errors(json.loads(path.read_text())) == [], path.name


# --- the vocabulary that drives these rules is self-consistent ---------------------------------

def test_every_class_unit_is_a_canonical_unit():
    stray = {c: u for c, us in validation.CLASS_UNITS.items() for u in us
             if u not in validation.CANONICAL_UNIT_SET}
    assert not stray, stray


def test_no_prefix_token_can_reject_a_canonical_class():
    clash = [(t, c) for t in validation.NAME_PREFIX_TOKENS for c in validation.CANONICAL_CLASSES
             if c == t or c.startswith(t + "_")]
    assert not clash, clash
