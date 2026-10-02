"""Conditions stated only in prose, producers named as author lists, and links to nothing.

On 2026-10-01 a rebuilt literature pipeline uploaded ten records that passed every rule. Three gave a
turnover frequency's temperature only in the definition ("at 400 C") while context.temperature_K
said not_reported, and all ten named the producer "<first author> et al.", which splits one group
into many for the engine that judges independence. Its author also checked by hand that every link
target exists, because the validator does not. Three blind reviews (Codex, Grok, Gemini) set the
tiers: the prose rule warns, "et al." holds, other author-list shapes warn. Every phrase they
proposed is a test below.
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


def _record(definition="value", unit="1/s", at=None, temperature=None, pressure=None):
    return {"record_type": "evidence",
            "context": {"temperature_K": temperature,
                        "transport": {"feed": {"pressure_bar": pressure}} if pressure is not None else {}},
            "descriptors": {"outputs": [{"descriptors": [{"name": "turnover_frequency", "definition": definition,
                                                          "unit": unit, **({"at": at} if at else {})}]}]}}


def _prose(definition, **kw):
    return {w["code"] for w in validation._prose_condition_warnings(_record(definition, **kw))}


STATED_ONLY_IN_PROSE = [
    "Ammonia-decomposition turnover frequency at 400 C.",
    "Rate constant at 25 °C (Arrhenius fit 300-500 C).",
    "Turnover frequency @ 673 K.",
    "@673 K steady state",
    "Rate, T = 400 °C.",
    "Rate under 20 bar H2.",
    "Partial current at -0.8 V vs RHE.",
    "Selectivity at a temperature of 250 °C.",
    "Stable at 400 C for 100 h.",
    "Conversion at ~350 °C.",
]
NOT_A_CONDITION = [
    "Discharge capacity at 1 C.",
    "Rate capability at 5 C.",
    "XPS peak at 284.8 eV.",
    "d-band center at -1.5 eV.",
    "CV at 10 mV/s.",
    "Ramped at 10 K/min.",
    "Tafel line at 60 mV dec-1.",
    "Measured relative to the value at 0 V.",
    "Normalized to the response at 1 bar.",
    "Activation energy from an Arrhenius fit at 300-500 K.",
    "Electronic energy at 0 K.",
    "Rate after the catalyst was calcined in flowing air at 500 C.",
    "Activity of a film stored at 4 °C.",
    "Partial current at -0.8 V vs Ag/AgCl.",
    "Active above 60 C.",
    "Rate from 300 to 500 C.",
]


@pytest.mark.parametrize("definition", STATED_ONLY_IN_PROSE)
def test_a_condition_only_in_prose_warns(definition):
    assert "CONDITION_ONLY_IN_PROSE" in _prose(definition), definition


@pytest.mark.parametrize("definition", NOT_A_CONDITION)
def test_rates_references_ranges_and_preparation_are_not_conditions(definition):
    assert "CONDITION_ONLY_IN_PROSE" not in _prose(definition), definition


def test_a_condition_the_structured_fields_carry_does_not_warn():
    assert not _prose("Turnover frequency at 400 C.", temperature=673.15)
    assert not _prose("Turnover frequency at 400 C.", at={"temperature_K": 673.15})
    assert not _prose("Rate under 20 bar H2.", pressure=20)


def test_the_descriptors_own_quantity_is_the_measurand():
    assert not _prose("Onset temperature at 450 C.", unit="K")
    assert not _prose("Plateau at 3.7 V vs RHE.", unit="V")


# --- producers ------------------------------------------------------------------------------

def _producer(group):
    r = {"record_type": "evidence", "attribution": {"produced_by": {"group": group}}}
    return {w["code"] for w in validation._producer_name_warnings(r)}


@pytest.mark.parametrize("group", ["Huihuang Fang et al.", "Sebastian Grundner et al", "Fang et. al.",
                                   "Smith and co-workers", "Smith and coworkers", "Smith and colleagues"])
def test_an_author_list_producer_is_held(group):
    assert _producer(group) == {"PRODUCER_AS_AUTHOR_LIST"}, group
    assert "PRODUCER_AS_AUTHOR_LIST" in validation.HOLD_CODES


@pytest.mark.parametrize("group", ["Smith, J.; Lee, K.", "Fang, Huihuang; Tsang, Shik", "Smith and Jones"])
def test_other_author_list_shapes_warn(group):
    assert _producer(group) == {"PRODUCER_NAME_FORMAT"}, group


@pytest.mark.parametrize("group", ["David Sinton and Edward H. Sargent groups", "Alexis T. Bell and Martin Head-Gordon groups",
                                   "Edward H. Sargent group", "JCAP high-throughput experimentation", "SUNCAT",
                                   "Metal Oxides Laboratory", "Johnson Matthey Technology Centre", "Joel W. Ager group",
                                   "Catalysis team, Example Institute", "metal etalon group"])
def test_group_names_pass(group):
    assert not _producer(group), group


def test_the_pipelines_record_is_held_for_its_producer():
    r = copy.deepcopy(BASE)
    r["attribution"].setdefault("produced_by", {})["group"] = "Huihuang Fang et al."
    res = validation.validate_record_full(r)
    assert res["valid"] and res["outcome"] == "hold" and "PRODUCER_AS_AUTHOR_LIST" in res["hold"]


# --- link targets ---------------------------------------------------------------------------

def test_a_link_to_a_record_that_does_not_exist_warns():
    r = {"links": [{"rel": "same_sample_as", "target": "01AAAAAAAAAAAAAAAAAAAAAAAA", "basis": "same_sample_id"},
                   {"rel": "derived_from", "target": "01BBBBBBBBBBBBBBBBBBBBBBBB", "basis": "unspecified"}]}
    out = validation.link_target_warnings(r, {"01AAAAAAAAAAAAAAAAAAAAAAAA"})
    assert [w["path"] for w in out] == ["links/1/target"] and out[0]["code"] == "LINK_TARGET_NOT_FOUND"
    assert not validation.link_target_warnings(r, {"01AAAAAAAAAAAAAAAAAAAAAAAA", "01BBBBBBBBBBBBBBBBBBBBBBBB"})


def test_validate_reports_missing_targets_without_changing_the_outcome(monkeypatch):
    import api
    import database
    monkeypatch.setattr(api, "_get_auth_info", lambda: {"method": "bearer_token", "user": "t", "groups": ["researcher"]})
    seen = []
    monkeypatch.setattr(database, "existing_record_ids", lambda ids, owner=None: seen.append((sorted(ids), owner)) or set())
    api.app.config["TESTING"] = True
    r = copy.deepcopy(BASE)
    r["links"] = [{"rel": "same_sample_as", "target": "01AAAAAAAAAAAAAAAAAAAAAAAA", "basis": "same_sample_id"}]
    body = api.app.test_client().post("/portal/api/validate", json=r).get_json()
    assert body["outcome"] == "publish" and "LINK_TARGET_NOT_FOUND" in {w["code"] for w in body["warnings"]}
    assert seen == [(["01AAAAAAAAAAAAAAAAAAAAAAAA"], "t")]


def test_an_unreachable_repository_skips_the_link_check(monkeypatch):
    import api
    import database
    monkeypatch.setattr(api, "_get_auth_info", lambda: {"method": "bearer_token", "user": "t", "groups": ["researcher"]})
    monkeypatch.setattr(database, "existing_record_ids", lambda ids, owner=None: (_ for _ in ()).throw(RuntimeError("down")))
    api.app.config["TESTING"] = True
    r = copy.deepcopy(BASE)
    r["links"] = [{"rel": "same_sample_as", "target": "01AAAAAAAAAAAAAAAAAAAAAAAA", "basis": "same_sample_id"}]
    res = api.app.test_client().post("/portal/api/validate", json=r)
    assert res.status_code == 200 and "LINK_TARGET_NOT_FOUND" not in {w["code"] for w in res.get_json().get("warnings") or []}
