"""Flask-level authorization tests for the Discovery workbench.

These are the regression gate for the write-IDOR: before the fix, any
authenticated user could evaluate predictions / edit hypotheses / delete runs on
ANY project by id. Every discovery MUTATION must now prove the caller may WRITE
the target project (admin OR owner OR write-share); read-only shares and
unrelated users get 403.

Runs offline: the auth layer and the discovery DB access are monkeypatched, so
no Postgres/Authentik is needed — this exercises the *decorator wiring* through
real Flask routing (which is exactly what was untested and let the hole ship).
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "portal"))
import api          # noqa: E402
import discovery    # noqa: E402


class _FakeCur:
    def execute(self, *a, **k): pass
    def fetchone(self): return {"project_id": "P1"}
    def close(self): pass


class _FakeConn:
    def cursor(self): return _FakeCur()
    def close(self): pass


@pytest.fixture
def client():
    api.app.config["TESTING"] = True
    return api.app.test_client()


def _as(monkeypatch, *, user, admin, can_write=False, can_read=False):
    """Simulate an authenticated caller; stub the discovery-DB authz lookups."""
    groups = ["admin"] if admin else ["researcher"]
    monkeypatch.setattr(api, "_get_auth_info",
                        lambda: {"method": "bearer_token", "user": user, "groups": groups})
    monkeypatch.setattr(api, "_caller_is_admin", lambda: admin)
    monkeypatch.setattr(api, "_disc_identity", lambda: user)
    # any child id resolves to project "P1"
    monkeypatch.setattr(api, "_disc_project_of", lambda kind, cid: "P1")
    monkeypatch.setattr(api.database, "get_discovery_db_connection", lambda: _FakeConn())
    monkeypatch.setattr(discovery, "_can_write", lambda cur, pid, ident: can_write)
    monkeypatch.setattr(discovery, "_can_read", lambda cur, pid, ident: can_read)


def _track(monkeypatch, fnname):
    """Replace a discovery mutation with a call tracker; returns the calls list."""
    calls = []
    monkeypatch.setattr(discovery, fnname, lambda *a, **k: (calls.append((a, k)), True)[1])
    return calls


# --- the confirmed IDOR: PUT /predictions/<id>/evaluate ---------------------

def test_evaluate_blocks_readonly_collaborator(client, monkeypatch):
    calls = _track(monkeypatch, "evaluate_prediction")
    _as(monkeypatch, user="attacker", admin=False, can_write=False)  # read-share or unrelated
    r = client.put("/portal/api/predictions/PRED1/evaluate", json={"verdict": "supports"})
    assert r.status_code == 403, r.get_data(as_text=True)
    assert calls == [], "mutation must NOT run when the caller is unauthorized"


def test_evaluate_allows_owner(client, monkeypatch):
    calls = _track(monkeypatch, "evaluate_prediction")
    _as(monkeypatch, user="owner", admin=False, can_write=True)
    r = client.put("/portal/api/predictions/PRED1/evaluate", json={"verdict": "supports"})
    assert r.status_code == 200
    assert len(calls) == 1


def test_evaluate_allows_admin_bypass(client, monkeypatch):
    calls = _track(monkeypatch, "evaluate_prediction")
    _as(monkeypatch, user="root", admin=True, can_write=False)  # admin bypasses the share check
    r = client.put("/portal/api/predictions/PRED1/evaluate", json={"verdict": "supports"})
    assert r.status_code == 200
    assert len(calls) == 1


# --- representative project-scoped mutation + child mutations ----------------

def test_add_event_blocks_non_owner(client, monkeypatch):
    calls = _track(monkeypatch, "add_event")
    _as(monkeypatch, user="attacker", admin=False, can_write=False)
    r = client.post("/portal/api/projects/P1/events",
                    json={"event_type": "note", "summary": "x"})
    assert r.status_code == 403
    assert calls == []


def test_delete_run_blocks_non_owner(client, monkeypatch):
    calls = _track(monkeypatch, "delete_compute_run")
    _as(monkeypatch, user="attacker", admin=False, can_write=False)
    r = client.delete("/portal/api/runs/RUN1")
    assert r.status_code == 403
    assert calls == []


def test_update_hypothesis_blocks_non_owner(client, monkeypatch):
    calls = _track(monkeypatch, "update_hypothesis")
    _as(monkeypatch, user="attacker", admin=False, can_write=False)
    r = client.put("/portal/api/hypotheses/HYP1", json={"status": "supported"})
    assert r.status_code == 403
    assert calls == []


# --- read-IDOR: rigor findings / async are owner/collaborator-only ----------

def test_list_rigor_findings_blocks_non_reader(client, monkeypatch):
    _as(monkeypatch, user="stranger", admin=False, can_read=False)
    r = client.get("/portal/api/projects/P1/rigor/findings")
    assert r.status_code == 403


def test_list_rigor_findings_allows_reader(client, monkeypatch):
    monkeypatch.setattr(discovery, "list_rigor_findings", lambda *a, **k: [])
    _as(monkeypatch, user="collab", admin=False, can_read=True)
    r = client.get("/portal/api/projects/P1/rigor/findings")
    assert r.status_code == 200


# --- completeness: no discovery mutation route may ship UNGATED --------------

# Discovery routes intentionally NOT behind the per-project write gate:
#   list/create own projects (owner is the caller), reads that self-enforce
#   _can_read inside the discovery fn, and OWNER-ONLY actions (share/unshare/
#   delete) which are gated separately by _is_owner.
_EXEMPT_ENDPOINTS = {
    "discovery_create_project", "discovery_list_projects", "discovery_get_project",
    "discovery_briefing", "discovery_context", "discovery_evidence",
    "discovery_share_project", "discovery_unshare_project", "discovery_delete_project",
    # Internal MLflow auth-proxy: NOT behind @_require_auth — gated by a
    # constant-time proxy secret (_proxy_secret_ok); a read-only access CHECK,
    # not a project mutation.
    "discovery_access_check_batch",
}
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def test_every_discovery_mutation_route_is_gated():
    """A new discovery mutation endpoint that forgets @_require_disc_write fails
    here — this is the guard that would have caught the original IDOR."""
    ungated = []
    for rule in api.app.url_map.iter_rules():
        if "/portal/api/" not in str(rule):
            continue
        view = api.app.view_functions[rule.endpoint]
        name = getattr(view, "__name__", rule.endpoint)
        if not name.startswith("discovery_"):
            continue
        methods = (rule.methods or set()) & _MUTATING
        if not methods or name in _EXEMPT_ENDPOINTS:
            continue
        if getattr(view, "_disc_authz_mode", None) != "write":
            ungated.append((name, sorted(methods), str(rule)))
    assert not ungated, "ungated discovery mutation routes: " + repr(ungated)
