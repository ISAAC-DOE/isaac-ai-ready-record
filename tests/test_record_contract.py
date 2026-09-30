"""The record contract: what an ISAAC record is, as one file served to agents and rendered on the wiki.

An agent that builds records through the API may never open the wiki. GET /portal/api/contract
gives it the definition, the procedure for deciding what goes in one record, what is never a
record (with the codes that flag each case and their tier today), how to submit, and worked
examples; every validation response points to it. The wiki page is generated from the same file,
and CI fails if the two differ.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))
sys.path.insert(0, str(REPO / "tools"))

import api  # noqa: E402
import validation  # noqa: E402
import generate_record_contract as gen  # noqa: E402
from generate_validation_docs import REGISTRY  # noqa: E402

CONTRACT = json.loads((REPO / "data" / "record_contract.json").read_text())
ONE_RESULT_CODES = {"SAMPLE_NOT_ONE_MATERIAL", "SAMPLE_NAME_CITES_A_PAPER", "MULTIPLE_SOURCES",
                    "QUALIFIER_NOT_A_PRODUCT", "NUMBER_AS_TEXT", "SENTENCE_AS_VALUE", "PERFORMANCE_EX_SITU",
                    "VOCABULARY_SUBSTITUTION"}


@pytest.fixture
def client():
    api.app.config["TESTING"] = True
    return api.app.test_client()


def test_the_contract_names_only_real_codes_and_real_examples():
    assert gen.problems() == []


def test_every_worked_example_is_a_clean_record():
    files = sorted({f for ex in CONTRACT["examples"] for f in ex["files"]})
    for f in files:
        res = validation.validate_record_full(json.loads((REPO / f).read_text()))
        assert res["valid"], (f, res["errors"][:2])
        assert not {w["code"] for w in res.get("warnings") or []} & ONE_RESULT_CODES, f


def test_the_paper_examples_are_linked_records_from_one_source():
    a, b, calc = (json.loads((REPO / "examples" / n).read_text()) for n in (
        "literature_paper_catalyst_a_record.json", "literature_paper_catalyst_b_record.json",
        "literature_paper_calculation_record.json"))
    assert {l["target"] for l in a["links"]} == {b["record_id"]} and {l["target"] for l in b["links"]} == {a["record_id"]}
    assert {l["target"] for l in calc["links"]} == {a["record_id"]}
    sources = {c["citation"]["doi"] for r in (a, b, calc) for c in r["assets"] if c["citation"]["relation"] == "source"}
    assert len(sources) == 1
    assert a["system"]["domain"] == "experimental" and calc["system"]["domain"] == "computational"


def test_the_review_is_cited_as_a_reference_and_the_original_paper_as_the_source():
    r = json.loads((REPO / "examples" / "literature_original_paper_rate_record.json").read_text())
    relations = sorted(a["citation"]["relation"] for a in r["assets"])
    assert relations == ["reference", "source"]


def test_the_contract_is_served_without_sign_in_with_tiers_and_links(client):
    res = client.get("/portal/api/contract")
    assert res.status_code == 200
    body = res.get_json()
    assert body["definition"] == CONTRACT["definition"] and body["procedure"] == CONTRACT["procedure"]
    tiers = {f["code"]: f["tier"] for row in body["never_a_record"] for f in row["flagged_as"]}
    assert tiers["DOMAIN_INCONSISTENT"] == REGISTRY["DOMAIN_INCONSISTENT"][0] == "error"
    assert tiers["SAMPLE_NOT_ONE_MATERIAL"] == "warning"
    assert all(u.startswith("https://github.com/ISAAC-DOE/") for ex in body["examples"] for u in ex["urls"])
    assert body["wiki"].endswith("/wiki/Record-Granularity")


def test_every_validation_response_points_to_the_contract(client, monkeypatch):
    monkeypatch.setattr(api, "_get_auth_info", lambda: {"method": "bearer_token", "user": "t", "groups": ["researcher"]})
    record = json.loads((REPO / "tests" / "adversarial" / "R02_survey_as_sample.json").read_text())
    body = client.post("/portal/api/validate", json=record).get_json()
    assert body["contract"]["url"] == "/portal/api/contract" and body["contract"]["version"] == CONTRACT["version"]
    assert "SAMPLE_NOT_ONE_MATERIAL" in {w["code"] for w in body.get("warnings") or []}
    bad = dict(record, record_domain="not_a_domain")
    res = client.post("/portal/api/records", json=bad)
    assert res.status_code == 400 and res.get_json()["contract"]["url"] == "/portal/api/contract"


def test_the_rendered_section_is_what_the_wiki_shows():
    text = gen.render()
    assert text.startswith(gen.BEGIN) and text.endswith(gen.END)
    for step in CONTRACT["procedure"]:
        assert step in text
    assert "`DOMAIN_INCONSISTENT` (error)" in text and "`SAMPLE_NOT_ONE_MATERIAL` (warning)" in text
