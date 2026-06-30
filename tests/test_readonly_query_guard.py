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


# Non-admin (agent_mode=True) must be scoped to `records` — operational tables denied.
@pytest.mark.parametrize("sql", [
    "SELECT * FROM api_requests",
    "SELECT username FROM portal_access_log",
    "SELECT * FROM record_acl",
    "SELECT * FROM record_history",
    "SELECT * FROM vocabulary_proposals",
    "WITH x AS (SELECT 1) SELECT * FROM api_requests",
    "SELECT r.record_id FROM records r JOIN record_acl a ON true",
])
def test_agent_mode_blocks_operational_tables(sql):
    with pytest.raises(ValueError):
        execute_readonly_query(sql, agent_mode=True)


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
