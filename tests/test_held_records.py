"""A record with a hold warning is held, not published, until it is fixed.

On 2026-09-29 one pipeline uploaded 147 records. Each passed every hard rule, and each came back
with a warning that named its problem: performance marked ex situ, a substituted vocabulary term,
a value copied from a review, a condition in the sample name, a rival hypothesis in a note. All
147 were published, because an agent reads a success response as success. A warning in a 201
changes nothing for a pipeline. So a record that carries a hold warning (validation.HOLD_CODES)
is stored privately and the upload answers 409: no one else, no search and no discovery agent
sees it, and a corrected version publishes it. Three blind reviews (Codex, Grok, Gemini) asked
for an error status, no self-acknowledgment, one id space and a backlog brake; all are here.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "portal"))
sys.path.insert(0, str(REPO / "tools"))

import api  # noqa: E402
import database  # noqa: E402
import validation  # noqa: E402
from generate_validation_docs import REGISTRY  # noqa: E402

BASE = json.loads((REPO / "tests" / "adversarial" / "R00_control_valid.json").read_text())


def _record(**sample_material):
    r = copy.deepcopy(BASE)
    r["sample"]["material"].update(sample_material)
    return r


HELD = _record(notes="SECOND-HAND (review table).")


# --- the validator says what a submission would do ------------------------------------------

def test_outcome_is_publish_hold_or_reject():
    clean = validation.validate_record_full(_record())
    assert clean["valid"] and clean["outcome"] == "publish" and "hold" not in clean
    held = validation.validate_record_full(HELD)
    assert held["valid"] and held["outcome"] == "hold" and held["hold"] == ["SECOND_HAND_SOURCE"]
    bad = copy.deepcopy(BASE); bad["record_domain"] = "not_a_domain"
    assert validation.validate_record_full(bad)["outcome"] == "reject"


def test_the_registry_marks_exactly_the_hold_codes():
    assert {c for c, (tier, _) in REGISTRY.items() if tier == "hold"} == set(validation.HOLD_CODES)


def test_warnings_without_a_clean_remedy_do_not_hold():
    """A value below a detection limit has no number form yet, and a refinement may sit on a
    measurement legitimately: those stay warnings."""
    assert "NUMBER_AS_TEXT" not in validation.HOLD_CODES
    assert "COMPUTATION_ON_MEASUREMENT" not in validation.HOLD_CODES
    assert {"NO_LINKS", "NO_DATA_OWNER", "MISSING_PH"}.isdisjoint(validation.HOLD_CODES)


# --- the API ---------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    who = {"user": "alice", "admin": False}
    monkeypatch.setattr(api, "_get_auth_info",
                        lambda: {"method": "bearer_token", "user": who["user"],
                                 "groups": ["admin"] if who["admin"] else ["researcher"]})
    monkeypatch.setattr(api, "_caller_is_admin", lambda: who["admin"])
    api.app.config["TESTING"] = True
    return api.app.test_client(), who


def _held_error(record):
    res = validation.validate_record_full(record)
    return database.RecordHeldError(record["record_id"], res["hold"], res["warnings"])


def test_a_held_upload_answers_409_with_the_fixes(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(database, "save_record", lambda *a, **k: (_ for _ in ()).throw(_held_error(HELD)))
    res = c.post("/portal/api/records", json=HELD)
    body = res.get_json()
    assert res.status_code == 409 and body["reason"] == "held"
    assert body["published"] is False and body["retryable"] is False and body["record_id"] == HELD["record_id"]
    assert body["hold"] == ["SECOND_HAND_SOURCE"] and {w["code"] for w in body["warnings"]} == {"SECOND_HAND_SOURCE"}
    assert "Do not retry this payload unchanged" in body["message"] and "PUT /portal/api/records/" in body["message"]
    assert "—" not in body["message"]


def test_the_backlog_brake_answers_409(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(database, "save_record",
                        lambda *a, **k: (_ for _ in ()).throw(database.HeldBacklogError("alice", 20)))
    body = c.post("/portal/api/records", json=HELD).get_json()
    assert body["reason"] == "held_backlog" and body["held"] == 20 and body["limit"] == database.HELD_BACKLOG_LIMIT


def test_a_fixed_record_is_published_by_put(client, monkeypatch):
    c, _ = client
    calls = []
    monkeypatch.setattr(database, "get_record", lambda rid: None)
    monkeypatch.setattr(database, "get_held_record", lambda rid: {"owner": "alice", "data": HELD, "hold_codes": ["SECOND_HAND_SOURCE"]})
    monkeypatch.setattr(database, "save_record", lambda data, **k: calls.append((data, k)) or data["record_id"])
    fixed = _record()
    res = c.put(f"/portal/api/records/{HELD['record_id']}", json=fixed)
    assert res.status_code == 201 and res.get_json()["published"] is True
    data, kwargs = calls[0]
    assert data["record_id"] == HELD["record_id"] and kwargs == {"uploaded_by": "alice", "mode": "insert"}


def test_someone_elses_held_record_does_not_exist_for_you(client, monkeypatch):
    c, who = client
    who["user"] = "bob"
    monkeypatch.setattr(database, "get_record", lambda rid: None)
    monkeypatch.setattr(database, "get_held_record", lambda rid: {"owner": "alice", "data": HELD, "hold_codes": []})
    assert c.get(f"/portal/api/records/{HELD['record_id']}").status_code == 404
    assert c.put(f"/portal/api/records/{HELD['record_id']}", json=HELD).status_code == 404
    assert c.delete(f"/portal/api/records/{HELD['record_id']}").status_code == 403


def test_the_owner_reads_and_discards_a_held_record(client, monkeypatch):
    c, _ = client
    gone = []
    monkeypatch.setattr(database, "get_record", lambda rid: None)
    monkeypatch.setattr(database, "get_held_record", lambda rid: {"owner": "alice", "data": HELD, "hold_codes": ["SECOND_HAND_SOURCE"]})
    monkeypatch.setattr(database, "delete_held", lambda rid: gone.append(rid) or True)
    res = c.get(f"/portal/api/records/{HELD['record_id']}")
    assert res.status_code == 200 and res.headers["X-ISAAC-Record-Status"] == "held"
    assert res.headers["X-ISAAC-Hold"] == "SECOND_HAND_SOURCE" and res.get_json()["record_id"] == HELD["record_id"]
    assert c.delete(f"/portal/api/records/{HELD['record_id']}").status_code == 200 and gone == [HELD["record_id"]]


def test_deleting_a_published_record_still_needs_an_admin(client, monkeypatch):
    c, who = client
    monkeypatch.setattr(database, "get_record", lambda rid: HELD)
    monkeypatch.setattr(database, "get_held_record", lambda rid: None)
    assert c.delete(f"/portal/api/records/{HELD['record_id']}").status_code == 403
    who["admin"] = True
    monkeypatch.setattr(database, "delete_record", lambda rid, actor=None: True)
    assert c.delete(f"/portal/api/records/{HELD['record_id']}").status_code == 200


def test_an_edit_that_brings_in_a_hold_warning_is_refused(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(database, "get_record", lambda rid: dict(_record(), attribution={"uploaded_by": "alice"}))
    monkeypatch.setattr(database, "acl_editor_usernames", lambda rid: set())
    res = validation.validate_record_full(HELD)
    monkeypatch.setattr(database, "update_record_versioned", lambda *a, **k: (_ for _ in ()).throw(
        database.EditHeldError(HELD["record_id"], res["hold"], res["warnings"])))
    r = c.put(f"/portal/api/records/{HELD['record_id']}", json=HELD)
    body = r.get_json()
    assert r.status_code == 409 and body["reason"] == "edit_held" and "published version is unchanged" in body["message"]


def test_the_held_list_is_your_own(client, monkeypatch):
    c, who = client
    monkeypatch.setattr(database, "list_held", lambda owner, limit, offset: (
        [{"record_id": HELD["record_id"], "owner": owner, "hold": ["SECOND_HAND_SOURCE"]}], 1))
    body = c.get("/portal/api/records/held").get_json()
    assert body["owner"] == "alice" and body["held"] == 1 and body["limit"] == database.HELD_BACKLOG_LIMIT
    assert c.get("/portal/api/records/held?owner=bob").status_code == 403
    who["admin"] = True
    assert c.get("/portal/api/records/held?owner=all").status_code == 200


def test_validate_previews_the_outcome(client):
    c, _ = client
    body = c.post("/portal/api/validate", json=HELD).get_json()
    assert body["valid"] and body["outcome"] == "hold" and body["hold"] == ["SECOND_HAND_SOURCE"]


# --- the database layer, offline ------------------------------------------------------------

class _Cur:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def execute(self, sql, params=None):
        self.conn.log.append(" ".join(sql.split()))
        self.rows = self.conn.answer(" ".join(sql.split()), params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class _Conn:
    def __init__(self, answer):
        self.answer, self.log, self.commits, self.rollbacks = answer, [], 0, 0

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _db(monkeypatch, held_owner=None, held_count=0, published=False):
    def answer(sql, params):
        if sql.startswith("SELECT owner FROM records_held"):
            return [{"owner": held_owner}] if held_owner is not None else []
        if sql.startswith("SELECT 1 FROM records WHERE"):
            return [{"?column?": 1}] if published else []
        if sql.startswith("SELECT COUNT(*) AS n FROM records_held"):
            return [{"n": held_count}]
        if sql.startswith("INSERT INTO records ("):
            return [{"record_id": params[0]}]
        return []
    conn = _Conn(answer)
    monkeypatch.setattr(database, "get_db_connection", lambda: conn)
    monkeypatch.setattr(database, "_index_after_commit", lambda c, rid: None)
    return conn


def test_a_held_record_goes_to_the_private_table(monkeypatch):
    conn = _db(monkeypatch)
    with pytest.raises(database.RecordHeldError) as e:
        database.save_record(copy.deepcopy(HELD), uploaded_by="alice", mode="insert")
    assert e.value.hold == ["SECOND_HAND_SOURCE"]
    assert any(s.startswith("INSERT INTO records_held") for s in conn.log)
    assert not any(s.startswith("INSERT INTO records (") for s in conn.log) and conn.commits == 1


def test_the_twenty_first_held_record_is_refused_and_not_stored(monkeypatch):
    conn = _db(monkeypatch, held_count=database.HELD_BACKLOG_LIMIT)
    with pytest.raises(database.HeldBacklogError):
        database.save_record(copy.deepcopy(HELD), uploaded_by="alice", mode="insert")
    assert not any(s.startswith("INSERT INTO records_held") for s in conn.log) and conn.rollbacks == 1


def test_a_clean_version_of_a_held_record_publishes_and_clears_the_draft(monkeypatch):
    conn = _db(monkeypatch, held_owner="alice")
    fixed = _record(); fixed["record_id"] = HELD["record_id"]
    assert database.save_record(fixed, uploaded_by="alice", mode="insert") == HELD["record_id"]
    assert any(s.startswith("DELETE FROM records_held") for s in conn.log)
    assert any(s.startswith("INSERT INTO records (") for s in conn.log)


def test_another_owners_held_id_is_taken(monkeypatch):
    _db(monkeypatch, held_owner="bob")
    with pytest.raises(database.RecordExistsError):
        database.save_record(_record(), uploaded_by="alice", mode="insert")


def test_a_published_id_cannot_be_held_over(monkeypatch):
    _db(monkeypatch, published=True)
    with pytest.raises(database.RecordExistsError):
        database.save_record(copy.deepcopy(HELD), uploaded_by="alice", mode="insert")


def test_admin_migration_bypass_publishes_directly(monkeypatch):
    conn = _db(monkeypatch)
    database.save_record(copy.deepcopy(HELD), uploaded_by="admin", mode="insert", skip_validation=True)
    assert not any(s.startswith("INSERT INTO records_held") for s in conn.log)
