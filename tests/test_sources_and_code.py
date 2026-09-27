"""
Where the numbers came from, which database entry, and one home for the code.

On 2026-09-27: 13 records built from a paper's public data were typed as facility
measurements (the DOI sat in system.configuration and in notes); no database record named
the entry it came from; and the code of 130 calculations sat in system.instrument, because
the templates put it there, while computation.method.code (which the discovery engine
reads) was empty on every record. The rules are generic: what says "calculation", what a
method declaration must contain and which processing steps mean "extracted from a paper"
are vocabulary data, and no rule names a code or a method.
"""

import copy
import json
import re
import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(PORTAL))

import validation  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
NEB = json.loads((REPO / "examples" / "dft_neb_evidence_record.json").read_text())
CO2RR = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())
XANES = json.loads((REPO / "examples" / "operando_xanes_co2rr_record.json").read_text())
CATHUB = json.loads((REPO / "examples" / "cathub_co2rr_cu100_cooh_record.json").read_text())
VOCAB = json.loads((REPO / "data" / "vocabulary.json").read_text())


def _codes(record):
    return [e.get("code") for e in validation.validate_record_full(record)["errors"]]


def _from_public_data(**changes):
    """A spectrum converted from a paper's public data repository, typed as a facility measurement."""
    r = copy.deepcopy(XANES)
    r["system"]["configuration"] = {"source_doi": "10.1038/s41929-022-00880-6", "source_panel": "Fig. 3a"}
    r["measurement"]["processing"] = {"notes": "Converted from the public data of DOI: 10.1038/s41929-022-00880-6."}
    r.update(changes)
    return r


# --- numbers taken from a publication are literature ---------------------------------------------

def test_a_publication_mentioned_without_its_relation_is_rejected():
    errs = [e for e in validation.validate_record_full(_from_public_data())["errors"]
            if e["code"] == "CITATION_RELATION_UNDECLARED"]
    assert errs and "10.1038/s41929-022-00880-6" in errs[0]["message"]


def test_numbers_taken_from_a_paper_are_not_a_facility_measurement():
    r = _from_public_data()
    r["assets"] = [{"asset_id": "paper", "content_role": "documentation", "sha256": "0" * 64,
                    "uri": "https://doi.org/10.1038/s41929-022-00880-6",
                    "citation": {"doi": "10.1038/s41929-022-00880-6", "relation": "source"}}]
    errs = [e for e in validation.validate_record_full(r)["errors"] if e["code"] == "LITERATURE_SOURCE_UNDECLARED"]
    assert errs and "source_type is 'literature', not 'facility'" in errs[0]["message"]
    r["source_type"] = "literature"
    r["attribution"]["produced_by"] = {"group": "Paper authors' group", "organization": "University of Toronto"}
    assert not {"LITERATURE_SOURCE_UNDECLARED", "CITATION_RELATION_UNDECLARED",
                "LITERATURE_CITATION_MISSING"} & set(_codes(r))


def test_a_calculation_citing_the_experiment_it_models_stays_the_uploader_s_calculation():
    assert NEB["source_type"] == "computation"
    assert validation.validate_record_full(NEB)["valid"]
    r = copy.deepcopy(NEB)
    for a in r["assets"]:
        if a.get("citation"):
            del a["citation"]["relation"]
    assert "CITATION_RELATION_UNDECLARED" in _codes(r)


def test_a_calculation_that_names_its_paper_as_source_must_be_literature():
    r = copy.deepcopy(NEB)
    for a in r["assets"]:
        if a.get("citation"):
            a["citation"]["relation"] = "source"
    assert "LITERATURE_SOURCE_UNDECLARED" in _codes(r)


def test_a_lab_may_cite_the_paper_that_reports_its_own_data():
    r = copy.deepcopy(CO2RR)
    r["assets"].append({"asset_id": "our_paper", "content_role": "documentation", "sha256": "0" * 64,
                        "uri": "https://doi.org/10.1000/our-paper",
                        "citation": {"doi": "10.1000/our-paper", "relation": "reports_this_work"}})
    assert validation.validate_record_full(r)["valid"]


def test_a_digitized_figure_or_a_figure_reference_is_literature():
    r = copy.deepcopy(CO2RR)
    r["measurement"]["processing"]["steps"].append("digitized_from_published_figure")
    assert "LITERATURE_SOURCE_UNDECLARED" in _codes(r)
    r = copy.deepcopy(XANES)
    r["descriptors"]["outputs"][0]["source_figure_refs"] = ["Fig. 2b"]
    assert "LITERATURE_SOURCE_UNDECLARED" in _codes(r)


def test_a_literature_record_must_cite_its_source_not_only_a_reference():
    r = copy.deepcopy(NEB)
    r["source_type"] = "literature"
    assert "LITERATURE_CITATION_MISSING" in _codes(r)  # its only citation is a 'reference'


def test_a_material_identifier_doi_needs_no_citation():
    r = copy.deepcopy(CO2RR)
    r["sample"]["material"]["identifiers"] = [{"scheme": "doi", "value": "10.1000/material-entry"}]
    assert "CITATION_RELATION_UNDECLARED" not in _codes(r)


# --- a database record names its entry -------------------------------------------------------------

def test_a_database_record_names_its_entry():
    assert validation.validate_record_full(CATHUB)["valid"]
    r = copy.deepcopy(CATHUB)
    r["assets"] = [a for a in r["assets"] if "database_entry" not in a]
    errs = [e for e in validation.validate_record_full(r)["errors"] if e["code"] == "DATABASE_ENTRY_MISSING"]
    assert errs and "entry_id" in errs[0]["message"]


# --- one home for the code ---------------------------------------------------------------------------

def test_the_code_lives_only_in_the_method_declaration():
    assert NEB["computation"]["method"]["code"] and "instrument" not in NEB["system"]
    r = copy.deepcopy(NEB)
    r["system"]["instrument"] = {"instrument_name": NEB["computation"]["method"]["code"]}
    assert "CODE_OUTSIDE_METHOD" in _codes(r)
    r = copy.deepcopy(NEB)
    r["system"]["configuration"] = {"dft_code": NEB["computation"]["method"]["code"]}
    assert "CODE_OUTSIDE_METHOD" in _codes(r)
    r["system"]["configuration"] = {"code_version": "6.4.1"}
    assert "CODE_OUTSIDE_METHOD" in _codes(r)


def test_any_code_is_accepted_including_chained_and_unreported():
    for code in ("not_reported", "VASP + OCEAN", "An in-house solver"):
        r = copy.deepcopy(NEB)
        r["computation"]["method"]["code"] = code
        assert validation.validate_record_full(r)["valid"], code


def test_a_calculation_declares_its_code_or_says_it_was_not_reported():
    r = copy.deepcopy(NEB)
    del r["computation"]["method"]["code"]
    errs = [e for e in validation.validate_record_full(r)["errors"] if e["code"] == "COMPUTATION_METHOD_INCOMPLETE"]
    assert errs and "code" in errs[0]["message"]
    r["computation"]["method"]["code"] = "not_reported"
    assert validation.validate_record_full(r)["valid"]


def test_a_measurement_keeps_its_instrument():
    assert CO2RR["system"].get("instrument") and "CODE_OUTSIDE_METHOD" not in _codes(CO2RR)


# --- the rules are generic: the knowledge is vocabulary data ------------------------------------------

def test_the_validator_names_no_simulation_code():
    src = (PORTAL / "validation.py").read_text()
    for name in ("VASP", "Quantum ESPRESSO", "GPAW", "CP2K", "JDFTx", "LAMMPS", "COMSOL", "OCEAN", "FEFF",
                 "ORCA", "Gaussian", "CASTEP", "SIESTA", "ABINIT"):
        assert not re.search(r"\b" + re.escape(name) + r"\b", src), name


def test_a_method_requirement_is_extended_by_vocabulary_alone(monkeypatch):
    r = copy.deepcopy(NEB)
    r["computation"]["method"]["family"] = "classical_MD"
    assert "COMPUTATION_METHOD_INCOMPLETE" not in _codes(r)
    monkeypatch.setitem(validation.METHOD_REQUIRED_BY_FAMILY, "classical_MD", ["pseudopotential_or_force_field"])
    assert "COMPUTATION_METHOD_INCOMPLETE" in _codes(r)


def test_every_sample_form_and_environment_says_calculation_or_measurement():
    forms = VOCAB["Sample"]["sample.sample_form"]["values"]
    envs = json.loads((REPO / "schema" / "isaac_record_v1.json").read_text())[
        "properties"]["context"]["properties"]["environment"]["enum"]
    for field, values in (("sample.sample_form", forms), ("context.environment", envs)):
        for value in values:
            assert f"{field}={value}" in validation.DOMAIN_SIGNALS, (field, value)


def test_oak_ridge_is_a_canonical_organization_and_its_facilities_resolve_to_it():
    orgs = VOCAB["System"]["system.organizations"]["values"]
    assert orgs["Oak Ridge National Laboratory"] == "https://ror.org/01qz5mb56"
    aliases = VOCAB["System"]["system.organization_aliases"]["map"]
    assert aliases["SNS"] == "Oak Ridge National Laboratory"
    assert aliases["ALS"] == "Lawrence Berkeley National Laboratory"
