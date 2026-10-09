"""
Stored records under the current rules.

The rules tighten over time and a stored record is never rejected retroactively. Its owner,
or the owner's agent, finds what to bring up to date through GET /records/attention and the
portal's Saved Records page, without anyone writing to them. These tests pin the report and
the endpoint's access rules; the database is stubbed.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(PORTAL))

import api         # noqa: E402
import validation  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CO2RR = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())


def _stale():
    r = copy.deepcopy(CO2RR)
    r["record_id"] = "01JSTALE000000000000000001"
    del r["attribution"]["produced_by"]
    return r


def test_the_report_lists_only_failing_records_with_every_error():
    rep = validation.current_contract_report([CO2RR, _stale()])
    assert rep["checked"] == 2 and rep["needing_update"] == 1
    row = rep["records"][0]
    assert row["record_id"] == "01JSTALE000000000000000001"
    assert any(e["code"] == "PRODUCED_BY_MISSING" and e["message"] for e in row["errors"])
    assert rep["by_code"]["PRODUCED_BY_MISSING"] == 1


@pytest.fixture
def client(monkeypatch):
    api.app.config["TESTING"] = True
    seen = {}

    def _records(identity):
        seen["identity"] = identity
        return [CO2RR, _stale()]

    monkeypatch.setattr(api.database, "records_editable_by", _records)
    c = api.app.test_client()
    c.seen = seen
    return c


def _as(monkeypatch, user, admin=False):
    monkeypatch.setattr(api, "_get_auth_info",
                        lambda: {"method": "bearer_token", "user": user, "groups": ["admin" if admin else "researcher"]})
    monkeypatch.setattr(api, "_caller_is_admin", lambda: admin)


def test_an_owner_sees_their_records_that_need_updating(client, monkeypatch):
    _as(monkeypatch, "alice")
    resp = client.get("/portal/api/records/attention")
    assert resp.status_code == 200
    body = resp.get_json()
    assert client.seen["identity"] == "alice"
    assert body["needing_update"] == 1 and body["records"][0]["record_id"] == "01JSTALE000000000000000001"
    assert "PUT /records/<id>" in body["how_to_fix"]


def test_the_list_can_be_narrowed_to_one_rule(client, monkeypatch):
    _as(monkeypatch, "alice")
    assert client.get("/portal/api/records/attention?code=PRODUCED_BY_MISSING").get_json()["matching"] == 1
    assert client.get("/portal/api/records/attention?code=SIGN_CONVENTION").get_json()["matching"] == 0


def test_only_an_admin_may_look_at_another_owner(client, monkeypatch):
    _as(monkeypatch, "mallory")
    assert client.get("/portal/api/records/attention?owner=alice").status_code == 403
    _as(monkeypatch, "root", admin=True)
    assert client.get("/portal/api/records/attention?owner=alice").status_code == 200
    assert client.seen["identity"] == "alice"


def test_an_unknown_parameter_is_rejected(client, monkeypatch):
    _as(monkeypatch, "alice")
    assert client.get("/portal/api/records/attention?mine=1").status_code == 400


# --- warnings on stored records (2026-10-01) -----------------------------------------------
# A rebuilt pipeline's agent read "none in /attention" after 674 uploads; two warnings added that
# evening then reached it only if it re-validated every record. Records that pass now report
# their warnings with a tier, counted apart from the errors; the list stays errors-only unless
# asked. Three blind reviews set the shape and the wording against inventing values.

R00 = json.loads((REPO / "tests" / "adversarial" / "R00_control_valid.json").read_text())


def _warned(suffix, journal=None, notes=None):
    r = copy.deepcopy(R00)
    r["record_id"] = R00["record_id"][:-2] + suffix
    if journal:
        r["assets"][0]["citation"]["journal"] = journal
    if notes:
        r["sample"]["material"]["notes"] = notes
    return r


REVIEWED = _warned("R1", journal="Chemical Reviews")
HELD_TODAY = _warned("H1", notes="SECOND-HAND (review table).")


def _stale_with_a_hold_warning():
    r = _stale()
    r["sample"]["material"]["notes"] = "SECOND-HAND (review table)."
    return r


def test_warnings_are_counted_on_records_that_pass_with_their_tier():
    rep = validation.current_contract_report([CO2RR, _stale_with_a_hold_warning(), REVIEWED, HELD_TODAY])
    assert rep["needing_update"] == 1 and [r["record_id"] for r in rep["records"]] == ["01JSTALE000000000000000001"]
    assert any(w["code"] == "SECOND_HAND_SOURCE" and w["tier"] == "hold" for w in rep["records"][0]["warnings"])
    by = rep["warnings_by_code"]
    assert by["SECOND_HAND_SOURCE"] == {"records": 1, "tier": "hold", "examples": [HELD_TODAY["record_id"]]}
    assert by["SOURCE_LOOKS_LIKE_A_REVIEW"] == {"records": 1, "tier": "warning", "examples": [REVIEWED["record_id"]]}
    assert by["NO_DATA_OWNER"]["records"] == 2 and sorted(by["NO_DATA_OWNER"]["examples"]) == sorted(
        [REVIEWED["record_id"], HELD_TODAY["record_id"]])
    assert next(iter(by)) == "SECOND_HAND_SOURCE"
    assert rep["stored_with_hold_warning"] == 1


@pytest.fixture
def warned_client(monkeypatch):
    api.app.config["TESTING"] = True
    monkeypatch.setattr(api.database, "records_editable_by",
                        lambda identity: [CO2RR, _stale_with_a_hold_warning(), REVIEWED, HELD_TODAY])
    return api.app.test_client()


def test_the_default_list_stays_errors_only_and_the_counts_point_to_the_warnings(warned_client, monkeypatch):
    _as(monkeypatch, "alice")
    body = warned_client.get("/portal/api/records/attention").get_json()
    assert body["tier"] == "error" and body["matching"] == 1
    assert body["records"][0]["record_id"] == "01JSTALE000000000000000001"
    assert body["stored_with_hold_warning"] == 1 and body["warnings_by_code"]["SOURCE_LOOKS_LIKE_A_REVIEW"]["records"] == 1
    assert "a warning that stays is an accepted outcome" in body["how_to_fix"]
    assert "Never supply a usual value" in body["how_to_fix"]
    assert "PUT /records/<id>" in body["how_to_fix"]


def test_a_tier_or_a_warning_code_lists_the_records_that_pass_with_it(warned_client, monkeypatch):
    _as(monkeypatch, "alice")
    def ids(query):
        body = warned_client.get("/portal/api/records/attention" + query).get_json()
        return body["tier"], [r["record_id"] for r in body["records"]]
    assert ids("?tier=hold") == ("hold", [HELD_TODAY["record_id"]])
    assert ids("?code=SECOND_HAND_SOURCE") == ("hold", [HELD_TODAY["record_id"]])
    assert ids("?code=SOURCE_LOOKS_LIKE_A_REVIEW") == ("warning", [REVIEWED["record_id"]])
    assert sorted(ids("?tier=warning")[1]) == sorted([REVIEWED["record_id"], HELD_TODAY["record_id"]])
    assert ids("?code=PRODUCED_BY_MISSING") == ("error", ["01JSTALE000000000000000001"])
    assert ids("?code=SIGN_CONVENTION") == ("error", [])
    assert warned_client.get("/portal/api/records/attention?tier=urgent").status_code == 400
