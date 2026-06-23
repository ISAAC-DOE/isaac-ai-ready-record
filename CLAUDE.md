# ISAAC AI-Ready Record — Portal (developer notes)

This repo holds two things: the **data standard** (`schema/`, `examples/`, `wiki/` —
see `README.md`, the normative v1.0 spec) and the **portal application** under
`portal/` that serves and edits records. This file documents the portal app and its
security model. The README is the frozen public standard; keep app/security notes here.

## Portal layout
- `portal/app.py` — Streamlit UI (records browser, form, Admin Review, API Keys, nano ISAAC).
- `portal/api.py` — Flask API (gunicorn sidecar) for programmatic access via Bearer tokens.
- `portal/database.py` — Postgres access (shared `isaac-psql` CNPG cluster).
- `portal/ontology.py` — vocabulary + wiki sync/push, plus the edge-identity gate.
- `portal/agent.py` — nano ISAAC (an LLM that emits read-only SQL over the records).

Deployed by `isaac-k8/isaac/` via Flux, behind Authentik forward-auth at
`https://isaac.slac.stanford.edu/portal`. Image tags are semver (never `:latest`);
pushing to `main` triggers a GitHub Actions build and Flux image automation bumps the
manifest.

## Security model (added 2026-06, Dean's audit)
Defense in depth — each layer is independent.

### Identity is trusted only from the edge
The pod must not trust `X-authentik-username` on its own; anything reaching the pod
directly (in-cluster peer, port-forward, SSRF) could set it. `ontology.trusted_identity()`
trusts the Authentik headers only when the request also carries the `X-Isaac-Edge`
shared secret (`hmac.compare_digest` vs `EDGE_AUTH_SECRET`) that the ingress injects and
overwrites. Admin status is derived from the username against `ISAAC_ADMINS` (no
spoofable group header). The secret lives only in k8s, never in git — see
`isaac-k8/docs/adr-0001-edge-auth-secret.md`.
- FAIL-OPEN FALLBACK (local dev only): if `EDGE_AUTH_SECRET` is unset the gate fails
  open (logs a WARNING). In prod this no longer happens — the deployment's secretKeyRef
  is `optional: false`, so a missing Secret fails the pod instead of silently degrading.

### Free-form / AI SQL runs least-privilege (nano ISAAC, /records/query)
`database.execute_readonly_query()`: single statement only; SELECT/WITH only; mutation
keywords and system/file functions (`pg_*`, `lo_*`, `dblink`, `information_schema`, …)
rejected; wrapped in a READ ONLY transaction with a 5s timeout. It connects via
`get_readonly_db_connection()` as `PGUSER_RO` (the `isaac_readonly` role — non-superuser,
SELECT only on `records`/`templates`/`vocabulary_cache`). nano ISAAC additionally runs
with `agent_mode=True`, which restricts reads to the `records` table by name. nano ISAAC
is open to all users; these layers are what make that safe.
- FALLBACK (now loud, local dev only): if `PGUSER_RO` is unset, `get_readonly_db_connection()`
  logs a WARNING and falls back to the privileged main connection. In prod this can't
  happen — `optional: false` fails the pod when the Secret is missing.

### Other controls
- API keys expire (90 days) and are minted only for the edge-trusted caller (`app.py`).
- Privileged Admin Review actions re-check admin server-side at click time
  (`_require_admin_action()`), not just at page load.
- `GITHUB_TOKEN` is never inlined into the wiki remote URL; git auth goes via
  `http.extraHeader` (GIT_CONFIG_* env), and error strings are scrubbed (`_scrub_secrets`).

## Open security follow-ups
Tracked in `../security-open-issues.md`: per-record read IDOR, confining the
`AUTHENTIK_API_TOKEN` to the API sidecar, pod hardening, and revoking the latent
PUBLIC CONNECT to other databases.
