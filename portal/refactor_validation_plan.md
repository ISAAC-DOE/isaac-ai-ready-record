# Refactor: DB-Driven Schema Validation

## Problem

Validation is split across two independent systems that can disagree:

1. **Static JSON Schema** (`schema/isaac_record_v1.json`) — checked with `jsonschema` (Draft 2020-12). Defines structure, types, required fields, and *some* `enum` constraints.
2. **Live vocabulary** (`vocabulary_cache` table / `vocabulary.json` fallback) — a separate pass that walks the record and checks string values against allowed term lists.

A record can pass the JSON Schema but fail the vocabulary check (or vice versa). Clients have no single schema to validate against locally, and the API response exposes the seam (`schema_valid` vs `vocabulary_valid`). The `merge_vocabulary_into_schema` function we just added is a band-aid — it still can't inject enums for vocabulary paths that the base schema never declared (`context.electrochemistry.*`, `context.transport.*`, etc.), because the base schema uses `additionalProperties: true` for those sub-trees.

## Proposed Design

Eliminate the static JSON Schema file as the source of truth. Instead, store the **full schema definition** — structure, types, required fields, *and* allowed enum values — in PostgreSQL. Validation runs against a single, DB-materialised JSON Schema that is always in sync with the vocabulary.

### New DB Table: `schema_fields`

```sql
CREATE TABLE IF NOT EXISTS schema_fields (
    id            INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    path          TEXT        NOT NULL UNIQUE,   -- dotted path, e.g. "context.electrochemistry.reaction"
    json_type     VARCHAR(30) NOT NULL,          -- "string", "number", "object", "array", …
    required      BOOLEAN     NOT NULL DEFAULT FALSE,
    enum_values   JSONB,                         -- NULL = no constraint; ["a","b"] = enum
    description   TEXT        DEFAULT '',
    section       VARCHAR(100),                  -- UI grouping ("Context", "Sample", …)
    format        VARCHAR(50),                   -- optional JSON Schema "format" (e.g. "date-time")
    pattern       VARCHAR(255),                  -- optional regex pattern (e.g. "^[0-9A-Z]{26}$")
    const_value   JSONB,                         -- optional fixed value (e.g. "1.05" for isaac_record_version)
    array_item_type VARCHAR(30),                 -- for arrays: type of items
    additional_properties BOOLEAN DEFAULT TRUE,  -- for objects
    parent_path   TEXT,                          -- NULL for top-level; "sample.material" for "sample.material.name"
    sort_order    INT         DEFAULT 0,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
```

Each row is one node in the record tree. The `path` column doubles as the primary lookup key. Vocabulary enum values live directly in `enum_values` — there is no separate vocabulary table (or it becomes a view/synonym).

### Schema Materialisation

A new function `materialise_json_schema()` in `database.py` (or a dedicated `schema.py` module) queries `schema_fields` and assembles a compliant JSON Schema dict in memory:

```
SELECT path, json_type, required, enum_values, format, pattern,
       const_value, array_item_type, additional_properties
FROM   schema_fields
ORDER  BY sort_order, path;
```

The function walks the sorted rows, nests them by splitting `path` on `.`, and produces a valid Draft 2020-12 JSON Schema. The result is cached (in-process or Redis) and invalidated whenever `schema_fields.updated_at` changes.

### Validation Flow (after refactor)

```
Client  ──POST /portal/api/validate──▶  API
                                         │
                                         ▼
                                  materialise_json_schema()   ◄── schema_fields table
                                         │
                                         ▼
                                  Draft202012Validator(schema)
                                         │
                                         ▼
                               single list of errors  ──▶  response
```

One pass. One error list. No `schema_valid` / `vocabulary_valid` split.

### Migration Path

| Step | What | Detail |
|------|------|--------|
| 1 | **Seed `schema_fields`** | Write a one-shot migration script that reads `isaac_record_v1.json` + `vocabulary.json` and inserts rows into `schema_fields`. This is the "merge" we currently do at runtime, done once at migration time. |
| 2 | **Implement `materialise_json_schema()`** | Build and unit-test the function that turns `schema_fields` rows → JSON Schema dict. Validate the output against the existing test records in `examples/`. |
| 3 | **New validation helper** | Replace `_validate_record()` + `_validate_vocabulary()` in `api.py` with a single `_validate_against_db_schema()` that calls the materialised schema. |
| 4 | **Update API response** | Simplify the `/validate` and `POST /records` responses: remove `schema_valid` / `vocabulary_valid`; return only `valid` + `errors`. |
| 5 | **Update `GET /portal/api/schema`** | Return the materialised schema directly (already cached). |
| 6 | **Update Streamlit `app.py`** | Remove the local `Draft202012Validator` + `ontology.validate_record_vocabulary` calls in the Record Validator page. Either call the API's `/validate` endpoint or import the new shared helper. |
| 7 | **Vocabulary sync writes to `schema_fields`** | Modify `save_vocabulary_cache()` (or replace it) so that wiki sync updates `enum_values` in `schema_fields` instead of a separate `vocabulary_cache` table. Keep `vocabulary_cache` as a compatibility view or drop it. |
| 8 | **Proposal workflow targets `schema_fields`** | When an approved proposal adds a term, it writes directly to `schema_fields.enum_values`. When it adds a category, it inserts a new row. |
| 9 | **Admin UI for schema editing** | Expose `schema_fields` in the portal admin so admins can add/remove fields, change types, toggle `required`, etc. — the schema evolves without redeploying code. |
| 10 | **Drop static artefacts** | Remove `schema/isaac_record_v1.json` from the repo (or generate it from the DB as a build artefact for documentation). Remove `_validate_vocabulary` and `merge_vocabulary_into_schema` from `ontology.py`. |

### Files Affected

| File | Changes |
|------|---------|
| `portal/database.py` | Add `schema_fields` table creation in `init_tables()`. Add `materialise_json_schema()`, `get_schema_field()`, `upsert_schema_field()`. Optionally modify/deprecate `save_vocabulary_cache`. |
| `portal/api.py` | Replace dual validation with single DB-schema validation. Simplify `/validate` and `POST /records` response shapes. Update `GET /portal/api/schema`. Remove `ISAAC_SCHEMA` / `ISAAC_VALIDATOR` globals. |
| `portal/ontology.py` | Remove `validate_record_vocabulary()` and `merge_vocabulary_into_schema()`. Vocabulary sync writes to `schema_fields` instead of `vocabulary_cache`. |
| `portal/app.py` | Record Validator page calls the shared validation function or API. Remove local JSON Schema loading. |
| `tools/seed_schema_fields.py` | **New** — one-shot migration script: reads `isaac_record_v1.json` + `vocabulary.json` → inserts into `schema_fields`. |
| `schema/isaac_record_v1.json` | Eventually removed or auto-generated. |

### Benefits

- **Single source of truth** — no more divergence between schema and vocabulary.
- **Live schema evolution** — add fields, change enums, toggle required, all without code changes or redeployment.
- **Simpler API contract** — one `valid` boolean, one `errors` list.
- **Client-friendly** — `GET /schema` returns everything a client needs to validate locally.
- **Auditability** — `updated_at` on each field row; combine with `vocabulary_sync_log` for full history.

### Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Materialisation is slow on every request | Cache the assembled schema in-process; invalidate on a `MAX(updated_at)` check (one cheap query). |
| Complex conditional logic (`allOf`/`if`/`then`) hard to express as flat rows | Keep a `schema_rules` table for conditional constraints (e.g. "if `record_type` = `evidence` then `descriptors` is required"), or store them as a JSONB column on a special row. Start simple — the current schema has only one such rule. |
| Migration correctness | The seed script compares its output against existing example records and the current dual-validation results. CI test: materialised schema must produce identical verdicts on all `examples/*.json`. |
| Backwards compatibility | Keep the `/validate` response fields (`schema_valid`, `vocabulary_valid`) during a transition period, both set to the same value. Deprecate in the next API version. |
