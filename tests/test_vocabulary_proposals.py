"""Agents can propose a missing vocabulary term through the API, and every unknown-term error says how.

The September records carried 124 notes admitting a substituted term: the vocabulary lacked the
right one, and the only way to propose a term was a form in the portal. Offline: the database and
the auth layer are monkeypatched.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))

import api  # noqa: E402
import validation  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    api.app.config["TESTING"] = True
    monkeypatch.setattr(api, "_get_auth_info", lambda: {"method": "bearer_token", "user": "agent-7", "groups": ["researcher"]})
    store = []

    def create_proposal(**kw):
        store.append(dict(kw, id=len(store) + 1, status="pending"))
        return len(store)

    monkeypatch.setattr(api.database, "create_proposal", create_proposal)
    monkeypatch.setattr(api.database, "list_proposals",
                        lambda status=None, proposed_by=None: [p for p in store if p.get("proposed_by") == proposed_by])
    c = api.app.test_client()
    c.store = store
    return c


GOOD = {"section": "System", "category": "system.technique", "term": "operando_XRD_microdiffraction",
        "description": "Microfocused X-ray diffraction recorded while the catalyst operates, used to map phases."}


def test_a_proposal_is_created_for_review(client):
    res = client.post("/portal/api/vocabulary/proposals", json=GOOD)
    assert res.status_code == 201 and res.get_json()["status"] == "pending"
    assert client.store[0]["proposed_by"] == "agent-7" and client.store[0]["proposal_type"] == "add_term"


def test_the_same_pending_proposal_is_not_filed_twice(client):
    client.post("/portal/api/vocabulary/proposals", json=GOOD)
    res = client.post("/portal/api/vocabulary/proposals", json=GOOD)
    assert res.status_code == 200 and len(client.store) == 1


def test_bad_proposals_are_refused_with_the_reason(client):
    cases = [(dict(GOOD, category="system.not_a_category"), 400, "unknown_category"),
             (dict(GOOD, term="two words"), 400, "invalid_term"),
             (dict(GOOD, description="short"), 400, "description_required"),
             (dict(GOOD, term="XAS"), 409, "term_exists")]
    for body, code, reason in cases:
        res = client.post("/portal/api/vocabulary/proposals", json=body)
        assert res.status_code == code and res.get_json()["reason"] == reason, reason
    assert client.store == []


def test_the_caller_sees_their_own_proposals(client):
    client.post("/portal/api/vocabulary/proposals", json=GOOD)
    body = client.get("/portal/api/vocabulary/proposals").get_json()
    assert [p["term"] for p in body["proposals"]] == [GOOD["term"]]


def test_an_unknown_term_error_says_how_to_propose_it():
    r = json.loads((REPO / "tests" / "adversarial" / "R00_control_valid.json").read_text())
    r = copy.deepcopy(r)
    r["system"]["technique"] = "scanning_electrochemical_cell_microscopy"
    msgs = [e.get("message", "") for e in validation.validate_record_full(r)["errors"]]
    assert any("POST /portal/api/vocabulary/proposals" in m and "system.technique" in m for m in msgs), msgs
