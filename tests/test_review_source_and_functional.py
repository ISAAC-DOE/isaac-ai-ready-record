"""A review cited as the source, and a DFT result without its functional (warnings, 2026-10-01).

A rebuilt literature pipeline passed every rule with 674 records, and 13 still cited a review as the
source of a value the review quoted, while all 118 calculations gave their functional as
'not_reported'. Three blind reviews set the patterns: full names of review-only venues and title
phrases that name a review, leaving out "Progress in ..." journals, a bare "perspective" and
"reviewed", which publish or describe research.
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


def _codes(record):
    return {w["code"] for w in validation._source_and_method_warnings(record)}


def _cited(journal="", title=""):
    return {"record_type": "evidence", "assets": [{"asset_id": "s", "uri": "https://doi.org/10.1000/x", "sha256": "x",
                                                   "content_role": "documentation",
                                                   "citation": {"doi": "10.1000/x", "relation": "source",
                                                                "journal": journal, "title": title}}]}


@pytest.mark.parametrize("journal", ["Chemical Reviews", "Chem. Rev.", "Chemical Society Reviews", "Chem. Soc. Rev.",
                                     "Nature Reviews Chemistry", "Nat. Rev. Mater.", "Annual Review of Physical Chemistry",
                                     "Current Opinion in Chemical Engineering", "Accounts of Chemical Research",
                                     "Acc. Chem. Res.", "Reviews of Modern Physics", "Physics Reports"])
def test_a_review_venue_warns(journal):
    assert "SOURCE_LOOKS_LIKE_A_REVIEW" in _codes(_cited(journal=journal, title="Some title")), journal


@pytest.mark.parametrize("title", ["A review of copper catalysts for CO2 reduction", "Minireview: single-atom sites",
                                   "Recent advances in the selective hydrodeoxygenation of lignin-derived oxygenates",
                                   "Catalytic Effects of Electrodes and Electrolytes: Progress and Perspective",
                                   "Catalytic Effects of Electrodes and Electrolytes in Metal-Sulfur Batteries: Progress and Prospective",
                                   "A roadmap for green ammonia", "A meta-analysis of perovskite stability",
                                   "Confinement effects in methanol to olefins: A computational review"])
def test_a_review_title_warns(title):
    assert "SOURCE_LOOKS_LIKE_A_REVIEW" in _codes(_cited(journal="Some Journal", title=title)), title


@pytest.mark.parametrize("journal,title", [
    ("Progress in Photovoltaics", "Efficient perovskite cells with a passivated interface"),
    ("Physical Chemistry Chemical Physics", "A quantitative multiscale perspective on primary olefin formation from methanol"),
    ("Journal of Catalysis", "Catalytic mechanisms reviewed under operando conditions"),
    ("Advanced Materials", "A state-of-the-art Ni-Fe anode for seawater electrolysis"),
    ("Nature Communications", "Single-site trinuclear copper oxygen clusters in mordenite"),
    ("ACS Catalysis", "Preview of the rate law for CO oxidation on Pt"),
])
def test_research_articles_do_not_warn(journal, title):
    assert "SOURCE_LOOKS_LIKE_A_REVIEW" not in _codes(_cited(journal=journal, title=title)), title


def test_a_reference_review_is_not_the_source():
    r = _cited(journal="Chemical Reviews", title="A review")
    r["assets"][0]["citation"]["relation"] = "reference"
    assert not _codes(r)


def _method(**m):
    return {"record_type": "evidence", "computation": {"method": m}}


def test_dft_without_its_functional_warns_and_names_the_code_when_missing():
    w = validation._source_and_method_warnings(_method(family="DFT", functional_name="not_reported", code="not_reported"))
    assert [x["code"] for x in w] == ["FUNCTIONAL_NOT_REPORTED"] and "nor the code" in w[0]["message"]
    assert "keep 'not_reported'" in w[0]["message"] and "PBE" not in w[0]["message"]
    w = validation._source_and_method_warnings(_method(family="DFT_U", code="VASP"))
    assert [x["code"] for x in w] == ["FUNCTIONAL_NOT_REPORTED"] and "nor the code" not in w[0]["message"]


@pytest.mark.parametrize("method", [{"family": "DFT", "functional_name": "RPBE", "code": "VASP"},
                                    {"family": "machine_learning", "code": "FairChem"},
                                    {"family": "microkinetic", "code": "CatMAP"},
                                    {"family": "semi_empirical", "functional_name": "not_reported"}])
def test_a_named_functional_or_another_method_does_not_warn(method):
    assert "FUNCTIONAL_NOT_REPORTED" not in _codes(_method(**method)), method


def test_the_warnings_never_change_validity():
    r = copy.deepcopy(BASE)
    r["assets"].append(_cited(journal="Chemical Reviews", title="A review")["assets"][0])
    res = validation.validate_record_full(r)
    assert "SOURCE_LOOKS_LIKE_A_REVIEW" in {w["code"] for w in res.get("warnings") or []}
    assert "SOURCE_LOOKS_LIKE_A_REVIEW" not in validation.HOLD_CODES
