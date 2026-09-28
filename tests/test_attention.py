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
