# Design: Record Editing, Co‑author ACL, and Versioned Provenance

**Status:** proposal (under review). **Branch:** `records-editing-acl-versioning`.
**Principle:** generic for all users; production‑grade; *nothing silent, everything addressable,
every change reversible and attributable*. Mirrors the discovery scorer's epistemics at the data layer.

## Ground truth (verified in code)
- `records(record_id CHAR(26) UNIQUE, record_type, record_domain, data JSONB, created_at)` — **no
  version, no updated_at, no content hash.** Ownership lives in `data.attribution.uploaded_by`.
- `record_history(id, record_id, action, actor, archived_at, data JSONB)` — full prior snapshot on
  every update/delete. **No version#, no change reason, no diff.**
- `record_acl` — **does not exist** (only a name in a maintenance list). ACL = build from scratch.
- `POST /records` is insert‑only (dup id → 409). `PUT /records/<id>` edits; today auth = `uploaded_by==caller OR admin`, and it **force‑preserves owner** (even admin cannot reassign).
- Identity = Authentik username (`_get_auth_info().user`); admin = `ADMIN_GROUPS` membership.
  `uploaded_by` is a username; `attribution.contributors[].orcid` is an ORCID → **username↔ORCID
  resolution required** for contributor‑based edit rights.

## LOCKED post‑review (3 adversarial sub‑agents, 2026‑06‑30 — all GO‑WITH‑CHANGES)
1. **Identity = server‑stamped Authentik username ONLY.** DROP the implicit
   `contributors[].orcid` edit right — that field lives in client‑controlled record data, so it's
   a trivial self‑grant. ORCID stays informational; it confers **no** rights. Authorization =
   admin **or** owner (`uploaded_by==username`) **or** `record_acl(record_id, username, 'editor')`.
   The "seamless authorship=edit" idea is OUT for security; seamless path is ACL‑by‑username (mirrors
   `/projects/<id>/share`).
2. **All edits go through ONE transactional, version‑CAS'd path.** `SELECT … FOR UPDATE` →
   archive (in‑txn, PRE‑edit version) → `UPDATE … WHERE version=%s RETURNING` (0 rows → 409). Honor
   `If‑Match: <version>` (→ 412). Stop calling standalone `archive_record`/`save_record` from PUT;
   stop swallowing archive failures. Close the admin `?allow_update=true` upsert un‑versioned
   back‑door (route through versioned path or make it bump+archive).
3. **content_hash canonicalization is FROZEN + tested** → `portal/record_provenance.py` +
   `tests/test_record_provenance.py` (12 tests green): whitelist scientific blocks, NFC, preserve
   null≠missing & list order, no float rounding, JSONB‑roundtrip‑stable, asset checksums not URIs.
4. **Re‑examine gate uses server‑computed `is_material` (hash diff), never client `change_class`.**
5. **ACL authz pinned:** only owner/admin grant (an editor cannot re‑grant); role ∈ {'editor'};
   `granted_by` server‑stamped; owner is never an ACL row; grantee==owner → 400.
6. **Auth computed ONCE per request:** `_get_auth_info` returns `groups`; attach to
   `request.auth_info`; `can_edit`/admin read from it; delete the standalone re‑validate in
   `_caller_is_admin`.
7. **Discovery pinning = sidecar.** Add `evidence_pins JSONB` to `hyp_predictions`; leave
   `evidence_record_ids TEXT[]` untouched (scorer dedup unaffected). Drift = **pull‑based re‑hash at
   briefing time** (records expose `version`+`content_hash` on GET); emit advisory
   `recommended_actions` only — a record edit must NEVER write verdict/strength/confidence/work_status
   (copy the `failed_compute` "did NOT change any score" framing). Self‑clears on re‑pin. Retraction
   is a sharper, always‑material case.

## A. Edit‑authorization resolver (single chokepoint)
One function `can_edit_record(record, caller_identity) -> (bool, reason)` used by **every** mutating
path (PUT, future PATCH, owner‑reassign reads). Authorization = caller is **admin** OR **owner**
(`uploaded_by==caller`) OR **listed contributor** (caller's ORCID ∈ `contributors[].orcid`, or a
contributor entry whose identity resolves to caller) OR **explicit ACL grant** (`record_acl` row,
`can_edit`). Default deny. Legacy records with `uploaded_by==None` → only admin may edit (never an
unowned free‑for‑all).

Open Q for review: identity model. Options — (1) resolve caller→ORCID from the Authentik user object
and match `contributors[].orcid`; (2) require contributors to also carry a portal `identity`
(username) for the match; (3) ACL only (no implicit contributor rights). Want the most seamless but
**non‑spoofable** option.

## B. `record_acl` (explicit collaborators)
```
record_acl(record_id CHAR(26), grantee_identity TEXT, role TEXT CHECK(role IN ('editor')),
           granted_by TEXT, granted_at TIMESTAMPTZ, PRIMARY KEY(record_id, grantee_identity))
```
Endpoints (mirror existing `/projects/<id>/share`): `POST /records/<id>/collaborators
{identity, role}` (owner/admin only), `DELETE /records/<id>/collaborators/<identity>`,
`GET /records/<id>/collaborators`. Granting is itself authz‑checked (only owner/admin grant). No
self‑grant escalation. ACL rows are audited.

## C. Admin owner‑reassign (the Grushika blocker, generic)
New: `PATCH /records/<id>/owner {uploaded_by, reason}` **admin‑only**, and a bulk
`PATCH /records/owner {record_ids:[...], uploaded_by, reason}`. Sets `attribution.uploaded_by`,
archives prior to history with `action='reassign_owner'` + reason, classified **metadata** (must NOT
invalidate downstream reasoning). This is the ONLY way to move ownership; PUT still force‑preserves.

## D. Versioning & provenance (the core)
1. **Version + hash.** Add `version INT NOT NULL DEFAULT 1` and `content_hash CHAR(64)` to `records`;
   `version` increments each edit; `content_hash` = sha256 of a **canonical projection of scientific
   fields** (descriptors, measurement, sample, context, computation — *excludes* volatile metadata:
   attribution, timestamps, record_id). `record_history` gains `version INT`, `content_hash`,
   `change_note TEXT`, `change_class TEXT`.
2. **Change capture.** PUT/PATCH accept `change_note` (free text) and `change_class`
   (`correction|metadata|retraction|extension`); if omitted, server computes class from the diff.
3. **Material vs cosmetic.** `is_material(old,new)` = did the canonical scientific projection change
   (hash differs)? Metadata‑only edits (attribution, description typo) are **cosmetic** → logged,
   never invalidate reasoning. Owner reassign is cosmetic by construction.
4. **History + diff endpoints.** `GET /records/<id>/history` (versions: actor, when, class, note,
   hash), `GET /records/<id>/diff?from=&to=` (field‑level). Read‑auth = same as get_record.
5. **Citation pinning (discovery side).** When the agent cites a record as evidence, store
   `{record_id, version, content_hash}` (today: bare string). Backward compatible: bare → treated as
   "unpinned / latest".
6. **Drift → re‑examine.** On a **material** edit, find discovery verdicts whose
   `evidence_record_ids` include it; surface in `briefing.recommended_actions`: "evidence R you cited
   (vN) was revised to vM — <note>; <material field diff> — re‑examine the K verdicts resting on it."
   Routes into the existing rigor/re‑score loop. Cosmetic edits do nothing. (Separate, smaller PR;
   discovery DB is isolated — cross‑DB ids are plain strings, no FK.)

## E. Surfacing
- **API Documentation page** (`app.py`): add `PUT /records/<id>`, the collaborators endpoints, the
  owner‑reassign, history/diff — with auth rules + worked examples.
- **Portal**: an "Edit" affordance for owner/contributors on a record; a **paginated records browser**
  (list API already does limit/offset + X‑Total‑Count). Coordinate with Grushika.

## F. Migration / back‑compat (must not break anything)
- New columns are additive with safe defaults; backfill `version=1`, compute `content_hash` for
  existing rows in a one‑time pass. Existing `record_history` rows: `version=NULL` allowed.
- Legacy unowned records keep working (admin‑editable only).
- Bare `evidence_record_ids` keep working (unpinned). No FK between discovery and records DBs.
- The **chokepoint** (`save_record` re‑validates) is preserved; version/hash are stamped inside it.

## G. Security / abuse surface (reviewers: attack this)
- Identity spoofing (can a caller claim someone else's ORCID/username?). Server‑stamped identity only.
- ACL privilege escalation (grant‑to‑self, grant beyond owner, role injection).
- Owner reassign as a hijack primitive (must be admin‑only, audited, reversible).
- Concurrency: two edits racing → version/history consistency (transaction + optimistic
  `If‑Match: <version|hash>`?). Lost‑update prevention.
- Hash canonicalization stability (key ordering, float formatting, unicode) — false drift / missed drift.
- Does any change weaken the existing insert‑only POST or the validation chokepoint?

## H. Rollout
Branch → unit tests (auth matrix, ACL, versioning, hash stability, migration) → adversarial
sub‑agent review per piece → battery green → **explicit go‑ahead + safe window (never near a demo)**
→ `ship.sh`. Production data ops (38‑record reassign, test‑record delete) only after the endpoints
exist, are reviewed/tested, admin rights confirmed.
