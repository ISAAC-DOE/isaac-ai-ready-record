"""A record is one result: one sample or one model, one measurement or one calculation, one set
of conditions, the values it produced, one source (Record-Granularity wiki).

The eight one-result checks are WARNINGS: they read words, names and strings, and the stored
corpus is too narrow to prove such a check safe. Each is tested both ways here: it flags the
collection, the citation, the second source, the catalyst in a name, the text where a number
belongs, the sentence, the ex situ performance and the admitted substitution; and it never blocks
a legitimate record. LOOKALIKES is the guard: legitimate records from other subfields that look
like the patterns. A check may be promoted to an error only when none of them raises it.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))

import validation  # noqa: E402

BASE = json.loads((REPO / "tests" / "adversarial" / "R00_control_valid.json").read_text())
XANES = json.loads((REPO / "examples" / "ex_situ_xanes_cuo2_record.json").read_text())
ONE_RESULT_CODES = {"SAMPLE_NOT_ONE_MATERIAL", "SAMPLE_NAME_CITES_A_PAPER", "MULTIPLE_SOURCES",
                    "QUALIFIER_NOT_A_PRODUCT", "NUMBER_AS_TEXT", "SENTENCE_AS_VALUE", "PERFORMANCE_EX_SITU",
                    "VOCABULARY_SUBSTITUTION"}


def _errors(record):
    return {e.get("code") for e in validation.validate_record_full(record)["errors"]}


def _warnings(record):
    return {w.get("code") for w in validation.validate_record_full(record).get("warnings") or []}


def _descriptors(r):
    return r["descriptors"]["outputs"][0]["descriptors"]


def _with(fn):
    r = copy.deepcopy(BASE)
    fn(r)
    return r


def _name(text):
    return lambda r: r["sample"]["material"].update(name=text)


def _note(text, where="sample"):
    def f(r):
        if where == "sample":
            r["sample"]["material"]["notes"] = text
        else:
            _descriptors(r)[0]["definition"] = text
    return f


def _label(name, value):
    return lambda r: _descriptors(r).append({"name": name, "kind": "categorical", "source": "imported", "value": value,
                                             "uncertainty": {"sigma": None, "basis": "not_reported"}})


def _value(value, kind="absolute"):
    def f(r):
        _descriptors(r)[0].update(value=value, kind=kind)
        _descriptors(r)[0].pop("unit", None)
    return f


def _qualifier(name):
    return lambda r: _descriptors(r)[0].update(name=name, value=0.42, unit="fraction")


def _cite(doi, relation):
    return {"asset_id": doi, "content_role": "documentation", "uri": f"https://doi.org/{doi}", "sha256": "0" * 64,
            "citation": {"doi": doi, "relation": relation}}


def _also_cite(doi, relation="source"):
    return lambda r: r["assets"].append(_cite(doi, relation))


def test_the_base_record_is_one_result():
    r = copy.deepcopy(BASE)
    assert validation.validate_record_full(r)["valid"] and not _warnings(r) & ONE_RESULT_CODES


# --- each check flags its pattern, and a flag never blocks ------------------------------------------

FLAGGED = [
    ("SAMPLE_NOT_ONE_MATERIAL", _name("Ni-based DRM catalysts, literature survey")),
    ("SAMPLE_NOT_ONE_MATERIAL", _name("MoS2 HER site-type comparison, review")),
    ("SAMPLE_NOT_ONE_MATERIAL", _name("Supported Pd catalysts, varied crystallite size")),
    ("SAMPLE_NOT_ONE_MATERIAL", _name("NO3RR across operating conditions")),
    ("SAMPLE_NOT_ONE_MATERIAL", _name("12 catalysts from Table 1")),
    ("SAMPLE_NAME_CITES_A_PAPER", _name("Pt/C (Xue 2020)")),
    ("SAMPLE_NAME_CITES_A_PAPER", _name("pure Ni (Yang et al., 2020)")),
    ("MULTIPLE_SOURCES", _also_cite("10.9999/probe.0002")),
    ("QUALIFIER_NOT_A_PRODUCT", _qualifier("mass_specific_rate.NH3_catalyst_b")),
    ("NUMBER_AS_TEXT", _value("4.06, 8.03, 9.24")),
    ("NUMBER_AS_TEXT", _value("sixfold")),
    ("NUMBER_AS_TEXT", _value("0.41 to 7.48", kind="categorical")),
    ("SENTENCE_AS_VALUE", _label("activity_origin", "CO activation creates oxygen vacancies that raise the rate")),
    ("PERFORMANCE_EX_SITU", lambda r: r["context"].update(environment="ex_situ")),
    ("VOCABULARY_SUBSTITUTION", _note("VOCABULARY SUBSTITUTION: measurement is 'literature_survey'. Submitted as 'EIS'.")),
    ("VOCABULARY_SUBSTITUTION", _note("Recorded as GC, the closest available technique.")),
]


@pytest.mark.parametrize("code,fn", FLAGGED, ids=[f"{c}-{i}" for i, (c, _) in enumerate(FLAGGED)])
def test_each_check_flags_its_pattern_as_a_warning(code, fn):
    r = _with(fn)
    assert code in _warnings(r)
    assert code not in _errors(r), f"{code} must not block: it is a warning until promoted"


def test_the_checks_leave_the_examples_alone():
    for path in sorted((REPO / "examples").glob("*.json")):
        assert not _warnings(json.loads(path.read_text())) & ONE_RESULT_CODES, path.name


def test_a_paper_its_dataset_and_its_references_are_one_source():
    r = _with(lambda r: r["assets"].extend([_cite("10.5281/zenodo.1234567", "reports_this_work"),
                                            _cite("10.9999/probe.0003", "reference")]))
    assert "MULTIPLE_SOURCES" not in _warnings(r)


def test_formula_and_vocabulary_qualifiers_are_not_flagged():
    for name in ("mass_specific_rate.NH3", "selectivity.C3H6", "conversion.CO2", "selectivity.C2plus",
                 "selectivity.n_C3H7OH"):
        r = _with(_qualifier(name) if not name.startswith("mass_specific_rate") else lambda r: None)
        assert "QUALIFIER_NOT_A_PRODUCT" not in _warnings(r), name


def test_an_ex_situ_characterization_is_not_flagged():
    assert "PERFORMANCE_EX_SITU" not in _warnings(XANES)


# --- a producer names someone (an error) ---------------------------------------------------------------

def test_a_placeholder_producer_names_no_one():
    for group in ("the authors", "Authors", "the paper's authors", "authors of the paper", "unknown", "not_reported"):
        r = _with(lambda r: r["attribution"].update(produced_by={"group": group}))
        hit = [e for e in validation.validate_record_full(r)["errors"] if e.get("code") == "PRODUCED_BY_MISSING"]
        assert hit and "names no one" in hit[0]["message"], group


def test_a_named_group_or_organization_is_a_producer():
    for pb in ({"group": "Lilong Jiang group"}, {"group": "the authors", "organization": "SLAC"},
               {"group": "Authors Lab consortium"}):
        r = _with(lambda r: r["attribution"].update(produced_by=pb))
        assert "PRODUCED_BY_MISSING" not in _errors(r), pb


# --- LOOKALIKES: legitimate records that look like the patterns. None may ever be rejected. -------------

LOOKALIKES = [
    ("one sample with a size distribution", _name("Cu nanoparticles on carbon (size range of 5-10 nm)")),
    ("islands on one surface", _name("Au islands distributed across the Cu surface")),
    ("a single crystal named by its use", _name("Pt(111) single crystal for kinetic studies")),
    ("a commercial reference catalyst", _name("Literature-standard Pt/C (TKK TEC10E50E)")),
    ("one foam with a pore-size distribution", _name("Ni foam with various pore sizes")),
    ("one gradient film", _name("TiO2 film of varied thickness (wedge sample)")),
    ("a synthesis-practice label", _name("NiFe LDH, best-practice hydrothermal synthesis")),
    ("a benchmark catalyst measured in the work", _name("Commercial Pt/C benchmark catalyst (20 wt%)")),
    ("made in this lab by a published recipe", _name("Pt3Ni/C prepared following Stamenkovic et al. 2007")),
    ("a batch label", _name("Cu foil (Batch 2023)")),
    ("a run label", _name("IrOx film (Run 2024)")),
    ("the paper and its SI, both cited as source", _also_cite("10.9999/probe.0001.s001")),
    ("the paper and its erratum", _also_cite("10.9999/probe.0099")),
    ("an isotope-labelled product", _qualifier("selectivity.13CO")),
    ("a Fischer-Tropsch C2-C4 lump", _qualifier("selectivity.C2_C4")),
    ("deuterium", _qualifier("selectivity.HD")),
    ("a product not detected (a table's n.d.)", _value("n.d.")),
    ("below the detection limit", _value("<0.5")),
    ("trace", _value("trace")),
    ("a hexagonal facet label", _label("surface_facet", "(10-10)")),
    ("a facet written with commas", _label("surface_facet", "(1,1,1)")),
    ("a space group", _label("space_group", "Fm-3m")),
    ("a stacking label", _label("stacking", "2H/3R polytypes")),
    ("a mixed-phase label", _label("crystal_phase", "mixed rutile and anatase")),
    ("a structure label", _label("crystal_structure", "face-centered cubic solid solution")),
    ("a morphology label", _label("morphology", "hollow spheres with porous shell")),
    ("an adsorption-site label", _label("adsorption.site", "bridge site between two Cu atoms")),
    ("a category label", _label("adsorption.site", "fcc-hollow")),
    ("an element list", _label("elements_detected", "Au; Cu; C; O")),
    ("a curator reading a figure", _note("No exact value is given in the text; the rate was read from Fig. 3b.")),
    ("a round-robin sample", _note("The catalyst was submitted as 'Cu-7' to the round-robin test.")),
    ("a scientific proxy", _note("NH3 formation rate; the N2 consumption was used as a proxy for conversion.",
                                 "definition")),
    ("no suitable reference electrode", _note("No suitable reference electrode was available; potentials are vs "
                                              "a Pt quasi-reference.")),
    ("an ordinary note", _note("The closest analogue in the source is the H2-reduced catalyst, submitted as its own "
                               "record.")),
    ("a group literally called Authors", lambda r: r["attribution"].update(produced_by={"group": "Authors Lab"})),
    ("a Rietveld refinement declared as a method", lambda r: r.update(computation={"method": {
        "code": "GSAS-II"}})),
]


@pytest.mark.parametrize("why,fn", LOOKALIKES, ids=[w for w, _ in LOOKALIKES])
def test_legitimate_lookalikes_are_never_rejected(why, fn):
    r = _with(fn)
    res = validation.validate_record_full(r)
    assert res["valid"], f"{why}: {[e.get('code') for e in res['errors']]}"
