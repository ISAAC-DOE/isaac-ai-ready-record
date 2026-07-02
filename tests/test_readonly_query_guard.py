"""Security boundary for the read-only SQL endpoint (database.execute_readonly_query).

Now that ANY authenticated researcher can call /records/query (scoped via agent_mode),
these pin that the guard rejects everything dangerous BEFORE it ever touches the DB.
All cases here raise ValueError in the pre-connection guard, so the suite runs offline.
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "portal"))
from database import execute_readonly_query  # noqa: E402


# Non-admin (agent_mode=True): SENSITIVE tables are denied (PII / access-control / moderation).
@pytest.mark.parametrize("sql", [
    "SELECT * FROM api_requests",
    "SELECT username FROM portal_access_log",
    "SELECT * FROM record_acl",
    "SELECT * FROM vocabulary_sync_log",
    "SELECT * FROM vocabulary_proposals",
    "SELECT actor FROM record_history",  # audit log: editor identity + archived/deleted snapshots
    "WITH x AS (SELECT 1) SELECT * FROM api_requests",
    "SELECT r.record_id FROM records r JOIN record_acl a ON true",
])
def test_agent_mode_blocks_sensitive_tables(sql):
    with pytest.raises(ValueError):
        execute_readonly_query(sql, agent_mode=True)


# Non-admin: NON-sensitive reference/scientific tables ARE allowed (pass the in-code belt;
# they only fail later reaching the DB in this env — never with the scope ValueError).
@pytest.mark.parametrize("sql", [
    "SELECT term FROM vocabulary_cache LIMIT 1",
    "SELECT * FROM templates LIMIT 1",
])
def test_agent_mode_allows_non_sensitive_tables(sql):
    with pytest.raises(Exception) as ei:
        execute_readonly_query(sql, agent_mode=True)
    assert "restricted to admins" not in str(ei.value)


def test_denylist_is_exactly_the_sensitive_set():
    # Pins the exact admin-only set (a change here is a conscious security decision).
    from database import _AGENT_FORBIDDEN_TABLES
    assert set(_AGENT_FORBIDDEN_TABLES) == {
        "api_requests", "portal_access_log", "vocabulary_sync_log",
        "vocabulary_proposals", "record_acl", "record_history"}


def test_every_records_table_is_classified():
    """Defense-in-depth completeness: EVERY table init_tables creates in the records DB
    must be explicitly classified — sensitive (admin-only, _AGENT_FORBIDDEN_TABLES) or
    public (_AGENT_PUBLIC_TABLES). A NEW table added without classifying it FAILS here,
    so it can't silently ship readable-by-default. (The isaac_readonly GRANT is the real
    gate; this keeps the in-code belt honest.) Introspects the init_tables DDL from
    source so it stays offline. Discovery-DB tables (a separate DB, not reachable via
    /records/query) are intentionally out of scope."""
    import re
    import database
    src = Path(database.__file__).read_text()
    body = src[src.index("def init_tables"):src.index("def init_discovery_tables")]
    created = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", body))
    assert created, "sanity: expected to find CREATE TABLE statements in init_tables"
    classified = set(database._AGENT_FORBIDDEN_TABLES) | set(database._AGENT_PUBLIC_TABLES)
    unclassified = created - classified
    assert not unclassified, (
        f"records-DB table(s) not classified: {sorted(unclassified)} — add each to "
        f"_AGENT_FORBIDDEN_TABLES (admin-only) or _AGENT_PUBLIC_TABLES (public).")
    # the declared public set must be real tables, not stale entries
    assert set(database._AGENT_PUBLIC_TABLES) <= created


# These must be rejected in EITHER mode (admin or researcher) — universal guards.
@pytest.mark.parametrize("sql", [
    "UPDATE records SET data='{}' WHERE record_id='x'",
    "DELETE FROM records",
    "DROP TABLE records",
    "INSERT INTO records VALUES (1)",
    "TRUNCATE records",
    "SELECT 1; DROP TABLE records",            # stacked statements
    "SELECT * FROM pg_roles",                  # system catalog
    "SELECT * FROM information_schema.tables",
    "SELECT pg_read_file('/etc/passwd')",      # file primitive
    "SELECT lo_export(1, '/tmp/x')",
    "SELECT current_setting('is_superuser')",
    "GRANT ALL ON records TO public",
])
@pytest.mark.parametrize("agent", [True, False])
def test_universal_guards_reject(sql, agent):
    with pytest.raises(ValueError):
        execute_readonly_query(sql, agent_mode=agent)


def test_plain_select_passes_guard_until_db():
    # A clean records SELECT is NOT rejected by the guard; it only fails later trying to
    # connect (no DB in this env) — i.e. the guard does not over-block legitimate queries.
    with pytest.raises(Exception) as ei:
        execute_readonly_query("SELECT record_id FROM records LIMIT 1", agent_mode=True)
    assert not isinstance(ei.value, ValueError) or "records" not in str(ei.value).lower()


def test_row_cap_uses_named_cursor_and_fetchmany(monkeypatch):
    """The row cap MUST be enforced by a server-side NAMED cursor + fetchmany(max_rows),
    never fetchall() (which buffered the whole result and let the old string-LIMIT cap be
    bypassed by a `LIMIT` substring in a column name). Mocks the connection so it runs
    offline — this is the DB-path coverage the guard tests otherwise lack."""
    import database as _db
    seen = {"named": None, "fetchmany_n": None, "fetchall": False}

    class _Cur:
        def __init__(self, name=None):
            self.name = name
        def execute(self, *a, **k):
            return None
        def fetchmany(self, n):
            seen["fetchmany_n"] = n
            return [{"ok": 1}] * min(n, 5)
        def fetchall(self):
            seen["fetchall"] = True
            return []
        def close(self):
            return None

    class _Conn:
        autocommit = False
        def cursor(self, name=None, cursor_factory=None):
            if name:
                seen["named"] = name
            return _Cur(name=name)
        def rollback(self):
            return None
        def close(self):
            return None

    monkeypatch.setattr(_db, "get_readonly_db_connection", lambda: _Conn())
    rows = _db.execute_readonly_query("SELECT record_id FROM records", max_rows=3)
    assert seen["named"] == "isaac_ro_query", "must open a server-side NAMED cursor"
    assert seen["fetchmany_n"] == 3, "must fetch exactly max_rows (the hard cap)"
    assert seen["fetchall"] is False, "must NOT use fetchall (unbounded buffer)"
    assert rows == [{"ok": 1}, {"ok": 1}, {"ok": 1}]


def test_max_rows_is_floored_at_one(monkeypatch):
    """max_rows <= 0 must not reach fetchmany as 0/negative (FETCH FORWARD 0/-n)."""
    import database as _db
    captured = {}

    class _Cur:
        def __init__(self, name=None): pass
        def execute(self, *a, **k): return None
        def fetchmany(self, n): captured["n"] = n; return []
        def fetchall(self): return []
        def close(self): return None

    class _Conn:
        autocommit = False
        def cursor(self, name=None, cursor_factory=None): return _Cur(name=name)
        def rollback(self): return None
        def close(self): return None

    monkeypatch.setattr(_db, "get_readonly_db_connection", lambda: _Conn())
    _db.execute_readonly_query("SELECT 1", max_rows=0)
    assert captured["n"] == 1
    _db.execute_readonly_query("SELECT 1", max_rows=-5)
    assert captured["n"] == 1
