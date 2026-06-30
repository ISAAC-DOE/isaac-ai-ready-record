# Read-only query access — DB grants (`isaac_readonly`)

`POST /portal/api/records/query` runs as the least-privilege **`isaac_readonly`** role.
Any authenticated user may run read-only `SELECT`/`WITH` over **non-sensitive** tables;
**sensitive** tables are admin-only.

There are TWO layers and they must stay in sync:
1. **In-code belt** — `_AGENT_FORBIDDEN_TABLES` in `portal/database.py` (blocks non-admins by
   table name early, with a clear error).
2. **The real gate** — what `isaac_readonly` is actually `GRANT`ed in PostgreSQL. Even if the
   in-code belt is opened, a table is only readable if the role has `SELECT` on it (fail-closed).

## Policy
| Table | Class | Researcher read? |
|---|---|---|
| `records` | scientific data | ✅ yes |
| `record_history` | version history of public records | ✅ yes |
| `vocabulary_cache` | the controlled ontology (reference) | ✅ yes |
| `templates` | record-form scaffolding | ✅ yes |
| `api_requests` | usage log — usernames, endpoints, **client IPs** | ❌ admin only |
| `portal_access_log` | login activity — usernames, timestamps | ❌ admin only |
| `vocabulary_sync_log` | operational sync log | ❌ admin only |
| `vocabulary_proposals` | proposer/reviewer identities + moderation | ❌ admin only |
| `record_acl` | who-can-edit-what (access-control graph) | ❌ admin only |

## Grants to apply (run as the records-DB owner/superuser)
```sql
-- Non-sensitive: the read-only role may read these (lights up researcher access)
GRANT SELECT ON records, record_history, templates, vocabulary_cache TO isaac_readonly;

-- Sensitive: the read-only role must NOT read these (fail-closed at the DB)
REVOKE ALL ON api_requests, portal_access_log, vocabulary_sync_log,
              vocabulary_proposals, record_acl FROM isaac_readonly;
```

Notes:
- Until the `GRANT` above is applied, a researcher query on a newly-opened table returns a DB
  permission error (safe — closed by default), even though the in-code belt allows it.
- Both admins and researchers hit this endpoint via `isaac_readonly`; the admin's only extra
  privilege here is that the in-code belt is skipped. Sensitive tables stay closed via the
  `REVOKE` regardless — so raw SQL over sensitive operational tables should go through a
  dedicated admin path, not this endpoint.
- **When you add a new records-DB table, classify it here and in `_AGENT_FORBIDDEN_TABLES`.**
  The test `test_denylist_is_exactly_the_sensitive_set` fails until you do.
