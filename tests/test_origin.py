"""
Measurement or calculation, where it came from, and who produced it.

On 2026-09-27, 98 calculations taken from papers (DFT, classical MD, microkinetic models)
were stored as experiments: the technique said DFT, while the domain, the environment, the
sample form and the provenance all said measurement, so they answered queries for measured
data. No record stated who produced its result. These tests pin the rules: the fields that
say "calculation" and "measurement" must agree, a calculation declares its method, every
evidence record names its producer, and a literature record carries its source. A
calculation from a paper, a calculation the uploader ran and a measurement are each
expressible and distinguishable.
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
NEB = json.loads((REPO / "examples" / "dft_neb_evidence_record.json").read_text())
CO2RR = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())


def _errors(record, code=None):
    return [e for e in validation.validate_record_full(record)["errors"] if code is None or e.get("code") == code]


def _literature_calculation():
    """A DFT result taken from a paper: the calculation envelope, the paper's group, its DOI."""
    r = copy.deepcopy(NEB)
    r["source_type"] = "literature"
    r["attribution"] = {"produced_by": {"group": "Paper authors' group", "organization": "Utrecht University"}}
    for a in r["assets"]:
        if a.get("citation"):
            a["citation"]["relation"] = "source"  # the calculation is taken from this paper
    return r


def _calculation_typed_as_experiment():
    """The 2026-09-27 pattern: technique DFT, everything else says measurement."""
    r = _literature_calculation()
    r["system"]["domain"] = "experimental"
    r["context"]["environment"] = "ex_situ"
    r["sample"]["sample_form"] = "film"
    r["sample"]["material"]["provenance"] = "synthesized"
    return r


# --- each origin is expressible and distinguishable -------------------------------------------

def test_a_calculation_the_uploader_ran_is_valid():
    assert NEB["source_type"] == "computation" and NEB["attribution"]["produced_by"]["group"]
    assert validation.validate_record_full(NEB)["valid"]


def test_a_calculation_from_a_paper_is_valid_and_stays_a_calculation():
    r = _literature_calculation()
    res = validation.validate_record_full(r)
    assert res["valid"], res["errors"][:3]
    assert r["system"]["domain"] == "computational" and r["source_type"] == "literature"


def test_a_measurement_is_valid():
    assert validation.validate_record_full(CO2RR)["valid"]


# --- the fields that say calculation or measurement agree ---------------------------------------

def test_a_calculation_typed_as_an_experiment_is_rejected_and_every_field_is_named():
    errs = _errors(_calculation_typed_as_experiment(), "DOMAIN_INCONSISTENT")
    assert errs
    msg = errs[0]["message"]
    for field in ("system.technique=DFT", "system.domain=experimental", "context.environment=ex_situ",
                  "sample.sample_form=film", "sample.material.provenance=synthesized"):
        assert field in msg, field
    assert "keep source_type 'literature'" in msg


def test_a_model_sample_on_a_measurement_is_rejected():
    r = copy.deepcopy(CO2RR)
    r["sample"]["sample_form"] = "slab_model"
    assert _errors(r, "DOMAIN_INCONSISTENT")


def test_an_in_silico_environment_on_a_measurement_is_rejected():
    r = copy.deepcopy(CO2RR)
    r["context"]["environment"] = "in_silico"
    assert _errors(r, "DOMAIN_INCONSISTENT")


def test_a_simulated_spectrum_may_name_the_spectroscopy():
    sim = json.loads((REPO / "examples" / "simulation_xas_record.json").read_text())
    sim["system"]["technique"] = "XAS"
    assert not _errors(sim, "DOMAIN_INCONSISTENT")


def test_every_evidence_record_says_whether_it_is_measured_or_computed():
    r = copy.deepcopy(CO2RR)
    del r["system"]
    assert _errors(r, "SYSTEM_DOMAIN_MISSING")


def test_an_intent_is_not_held_to_the_evidence_rules():
    intent = json.loads((REPO / "examples" / "intent_sample_request.json").read_text())
    codes = {e.get("code") for e in _errors(intent)}
    assert not codes & {"SYSTEM_DOMAIN_MISSING", "DOMAIN_INCONSISTENT", "PRODUCED_BY_MISSING"}


# --- a calculation declares its method -----------------------------------------------------------

def test_a_literature_calculation_without_its_method_is_rejected():
    r = _literature_calculation()
    del r["computation"]
    errs = _errors(r, "COMPUTATION_METHOD_MISSING")
    assert errs and "copy the method the paper states" in errs[0]["message"]


def test_a_functional_the_source_does_not_state_is_declared_not_reported():
    r = _literature_calculation()
    del r["computation"]["method"]["functional_name"]
    assert _errors(r, "COMPUTATION_METHOD_INCOMPLETE")
    r["computation"]["method"]["functional_name"] = "not_reported"
    assert not _errors(r, "COMPUTATION_METHOD_INCOMPLETE")


# --- who produced it, and from which source -------------------------------------------------------

def test_the_producer_is_required_and_the_message_fits_the_source():
    r = _literature_calculation()
    del r["attribution"]
    errs = _errors(r, "PRODUCED_BY_MISSING")
    assert errs and "the group of the paper's authors" in errs[0]["message"]
    r = copy.deepcopy(NEB)
    del r["attribution"]
    errs = _errors(r, "PRODUCED_BY_MISSING")
    assert errs and "your own group if you ran it" in errs[0]["message"]


def test_a_placeholder_organization_names_no_producer():
    r = copy.deepcopy(CO2RR)
    r["attribution"]["produced_by"] = {"organization": "not_specified_in_source"}
    assert _errors(r, "PRODUCED_BY_MISSING")
    r["attribution"]["produced_by"] = {"organization": "Lawrence Berkeley National Laboratory"}
    assert not _errors(r, "PRODUCED_BY_MISSING")


def test_a_literature_record_carries_its_source():
    r = _literature_calculation()
    r["assets"] = [a for a in r["assets"] if "doi.org" not in a.get("uri", "")]
    assert _errors(r, "LITERATURE_CITATION_MISSING")
    r["assets"].append({"asset_id": "paper", "content_role": "documentation", "uri": "https://example.org/paper",
                        "sha256": "0" * 64, "citation": {"title": "A paper without a DOI", "year": 1987}})
    assert not _errors(r, "LITERATURE_CITATION_MISSING")


# --- vocabulary ------------------------------------------------------------------------------------

def test_the_model_forms_cover_molecular_and_continuum_models():
    served = ontology.merge_vocabulary_into_schema(validation.ISAAC_SCHEMA)
    forms = set(served["properties"]["sample"]["properties"]["sample_form"]["enum"])
    assert validation.MODEL_SAMPLE_FORMS <= forms


def test_not_reported_names_no_organization_or_functional():
    assert "not_reported" in validation._ORG_PLACEHOLDERS
