"""A record is one result: one sample or one model, one measurement or one calculation, one set
of conditions, the values it produced, one source (Record-Granularity wiki).

Each check is tested both ways: it catches the collection, the citation, the second source, the
catalyst in a name, the sentence, the numbers as text, the ex situ performance, the admitted
substitution and the placeholder producer; and it leaves alone the records that look similar and
are right (a benchmark catalyst measured in the work itself, a paper and its dataset, a category
label, an ex situ characterization, formula qualifiers, a real group name).
"""
import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))

import validation  # noqa: E402

BASE = json.loads((REPO / "tests" / "adversarial" / "R00_control_valid.json").read_text())
CO2RR = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())
XANES = json.loads((REPO / "examples" / "ex_situ_xanes_cuo2_record.json").read_text())


def _codes(record):
    return {e.get("code") for e in validation.validate_record_full(record)["errors"]}


def _descriptor(r):
    return r["descriptors"]["outputs"][0]["descriptors"][0]


def test_the_base_record_is_one_result():
    assert validation.validate_record_full(BASE)["valid"]


# --- the sample is one material ------------------------------------------------------------------

def test_a_survey_a_review_or_varied_samples_is_not_one_material():
    for name in ("Ni-based DRM catalysts, literature survey", "MoS2 HER site-type comparison, review",
                 "Supported Pd catalysts, varied crystallite size", "NO3RR across operating conditions",
                 "HER benchmarking practice, iR compensation", "12 catalysts from Table 1"):
        r = copy.deepcopy(BASE)
        r["sample"]["material"]["name"] = name
        assert "SAMPLE_NOT_ONE_MATERIAL" in _codes(r), name


def test_a_benchmark_catalyst_measured_in_the_work_is_one_material():
    r = copy.deepcopy(BASE)
    r["sample"]["material"]["name"] = "Commercial Pt/C benchmark catalyst (20 wt%)"
    assert "SAMPLE_NOT_ONE_MATERIAL" not in _codes(r)


def test_a_sample_name_that_cites_a_paper_is_rejected():
    for name in ("Pt/C (Xue 2020)", "pure Ni (Yang et al., 2020)", "Ru/C as in Fang et al. 2024"):
        r = copy.deepcopy(BASE)
        r["sample"]["material"]["name"] = name
        assert "SAMPLE_NAME_CITES_A_PAPER" in _codes(r), name


# --- one source ------------------------------------------------------------------------------------

def _cite(doi, relation):
    return {"asset_id": doi, "content_role": "documentation", "uri": f"https://doi.org/{doi}", "sha256": "0" * 64,
            "citation": {"doi": doi, "relation": relation}}


def test_two_source_papers_are_two_records():
    r = copy.deepcopy(BASE)
    r["assets"].append(_cite("10.9999/probe.0002", "source"))
    assert "MULTIPLE_SOURCES" in _codes(r)


def test_a_paper_its_dataset_and_its_references_are_one_source():
    r = copy.deepcopy(BASE)
    r["assets"] += [_cite("10.5281/zenodo.1234567", "reports_this_work"), _cite("10.9999/probe.0003", "reference")]
    assert "MULTIPLE_SOURCES" not in _codes(r)


def test_the_same_source_cited_twice_is_one_source():
    r = copy.deepcopy(BASE)
    r["assets"].append(_cite("10.9999/PROBE.0001", "source"))
    assert "MULTIPLE_SOURCES" not in _codes(r)


# --- a qualifier names a species -------------------------------------------------------------------

def test_a_catalyst_written_into_a_qualifier_is_rejected():
    r = copy.deepcopy(BASE)
    _descriptor(r)["name"] = "mass_specific_rate.NH3_catalyst_b"
    assert "QUALIFIER_NOT_A_PRODUCT" in _codes(r)


def test_formula_and_vocabulary_qualifiers_pass():
    for name in ("mass_specific_rate.NH3", "selectivity.C3H6", "conversion.CO2", "selectivity.C2plus",
                 "selectivity.n_C3H7OH"):
        r = copy.deepcopy(BASE)
        _descriptor(r)["name"] = name
        if not name.startswith("mass_specific_rate"):
            _descriptor(r).update(value=0.42, unit="fraction")
        assert "QUALIFIER_NOT_A_PRODUCT" not in _codes(r), name


# --- a value is a number or a label ----------------------------------------------------------------

def test_a_sentence_is_not_a_value():
    r = copy.deepcopy(BASE)
    r["descriptors"]["outputs"][0]["descriptors"].append({
        "name": "activity_origin", "kind": "categorical", "source": "imported",
        "value": "CO activation creates oxygen vacancies that raise the rate",
        "uncertainty": {"sigma": None, "basis": "not_reported"}})
    assert "SENTENCE_AS_VALUE" in _codes(r)


def test_a_short_category_label_is_a_value():
    for label in ("fcc-hollow", "Au; Cu; C; O", "rutile", "mixed Cu/Cu2O phase"):
        r = copy.deepcopy(BASE)
        r["descriptors"]["outputs"][0]["descriptors"].append({
            "name": "adsorption.site", "kind": "categorical", "source": "imported", "value": label,
            "uncertainty": {"sigma": None, "basis": "not_reported"}})
        codes = _codes(r)
        assert "SENTENCE_AS_VALUE" not in codes and "NUMBER_AS_TEXT" not in codes, label


def test_numbers_as_text_are_rejected():
    for text, kind in (("4.06, 8.03, 9.24", "absolute"), ("30-34", "absolute"), ("0.41 to 7.48", "categorical"),
                       ("2.5 10-2", "absolute"), ("36%, 47%, and 43%", "categorical"), ("sixfold", "absolute")):
        r = copy.deepcopy(BASE)
        _descriptor(r).update(value=text, kind=kind)
        _descriptor(r).pop("unit", None)
        assert "NUMBER_AS_TEXT" in _codes(r), text


# --- performance is measured while the reaction runs -------------------------------------------------

def test_performance_ex_situ_is_rejected():
    r = copy.deepcopy(BASE)
    r["context"]["environment"] = "ex_situ"
    assert "PERFORMANCE_EX_SITU" in _codes(r)


def test_an_ex_situ_characterization_is_fine():
    assert "PERFORMANCE_EX_SITU" not in _codes(XANES)
    assert validation.validate_record_full(XANES)["valid"]


# --- a substituted term is a wrong term -------------------------------------------------------------

def test_an_admitted_substitution_is_rejected_wherever_it_is_written():
    notes = ("VOCABULARY SUBSTITUTION: measurement is 'literature_survey'. Submitted as 'EIS'.",
             "Recorded as GC, the closest available technique.",
             "SECM is not in the ISAAC vocabulary, so linear_sweep_voltammetry was used as a proxy.")
    for text in notes:
        r = copy.deepcopy(BASE)
        r["sample"]["material"]["notes"] = text
        assert "VOCABULARY_SUBSTITUTION" in _codes(r), text


def test_ordinary_notes_are_not_substitutions():
    r = copy.deepcopy(BASE)
    r["sample"]["material"]["notes"] = ("The closest analogue in the source is the H2-reduced catalyst, measured "
                                        "under the same conditions and submitted as its own record.")
    assert "VOCABULARY_SUBSTITUTION" not in _codes(r)


# --- a producer names someone -----------------------------------------------------------------------

def test_a_placeholder_producer_names_no_one():
    for group in ("the authors", "Authors", "the paper's authors", "authors of the paper", "unknown", "not_reported"):
        r = copy.deepcopy(BASE)
        r["attribution"]["produced_by"] = {"group": group}
        codes = validation.validate_record_full(r)["errors"]
        hit = [e for e in codes if e.get("code") == "PRODUCED_BY_MISSING"]
        assert hit and "names no one" in hit[0]["message"], group


def test_a_named_group_or_organization_is_a_producer():
    for pb in ({"group": "Lilong Jiang group"}, {"group": "the authors", "organization": "SLAC"},
               {"group": "Authors Lab consortium"}):
        r = copy.deepcopy(BASE)
        r["attribution"]["produced_by"] = pb
        assert "PRODUCED_BY_MISSING" not in _codes(r), pb


def test_the_rules_leave_the_examples_alone():
    for path in sorted((REPO / "examples").glob("*.json")):
        codes = _codes(json.loads(path.read_text()))
        assert not codes & {"SAMPLE_NOT_ONE_MATERIAL", "SAMPLE_NAME_CITES_A_PAPER", "MULTIPLE_SOURCES",
                            "QUALIFIER_NOT_A_PRODUCT", "NUMBER_AS_TEXT", "SENTENCE_AS_VALUE",
                            "PERFORMANCE_EX_SITU", "VOCABULARY_SUBSTITUTION"}, path.name
