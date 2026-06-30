"""
ISAAC AI-Ready Record - Database Connection Module
PostgreSQL connection for vocabulary, templates, and records storage
"""

import os
import json
import re
import logging
from datetime import datetime

# psycopg2 is required to actually talk to Postgres, but importing this module must
# NOT require the driver — the portal's pure-logic layer (e.g. discovery scoring) is
# unit-tested in environments without psycopg2 installed. Defer the hard failure to
# the moment a real connection is requested.
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ModuleNotFoundError:  # pragma: no cover - exercised only in driver-less CI
    psycopg2 = None
    RealDictCursor = None

logger = logging.getLogger("isaac-database")


def _require_psycopg2():
    if psycopg2 is None:
        raise ModuleNotFoundError(
            "psycopg2 is required for database access but is not installed. "
            "Install psycopg2-binary to use the DB-backed code paths.")


def get_db_connection():
    """Create a database connection using environment variables"""
    _require_psycopg2()
    return psycopg2.connect(
        host=os.environ.get('PGHOST', 'localhost'),
        port=os.environ.get('PGPORT', '5432'),
        database=os.environ.get('PGDATABASE', 'app'),
        user=os.environ.get('PGUSER', 'postgres'),
        password=os.environ.get('PGPASSWORD', ''),
        cursor_factory=RealDictCursor
    )


def get_readonly_db_connection():
    """Connection for the untrusted free-form SQL path (nano-ISAAC,
    /records/query).

    Uses the least-privilege ``PGUSER_RO`` login role when configured — that
    role is NOSUPERUSER with SELECT granted only on the data surface, so file
    primitives (pg_read_file, lo_*) and audit/PII tables are unreachable (C2).

    Falls back to the main connection when PGUSER_RO is unset (local dev only).
    In production the deployment's secretKeyRef is optional:false, so a missing
    Secret fails the pod rather than reaching this fallback. The fallback logs
    loudly so the downgrade to the privileged role is never silent."""
    ro_user = os.environ.get('PGUSER_RO')
    if not ro_user:
        logger.warning(
            "PGUSER_RO not set — free-form SQL is running on the PRIVILEGED main "
            "DB role. Expected only in local dev; in prod this means the "
            "isaac-psql-readonly Secret is missing."
        )
        return get_db_connection()
    _require_psycopg2()
    return psycopg2.connect(
        host=os.environ.get('PGHOST', 'localhost'),
        port=os.environ.get('PGPORT', '5432'),
        database=os.environ.get('PGDATABASE', 'app'),
        user=ro_user,
        password=os.environ.get('PGPASSWORD_RO', ''),
        cursor_factory=RealDictCursor
    )


def is_db_configured():
    """Check if database environment variables are configured"""
    return bool(os.environ.get('PGHOST'))


def test_db_connection():
    """Test if database connection is working"""
    if not is_db_configured():
        return False
    try:
        conn = get_db_connection()
        conn.close()
        return True
    except Exception:
        return False


def init_tables():
    """Initialize database tables if they don't exist"""
    if not is_db_configured():
        return False

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # API usage log (api-usage-dashboard, 2026-06-14)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS api_requests (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL DEFAULT now(),
                username TEXT,
                method TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                status SMALLINT,
                duration_ms REAL
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_api_requests_ts ON api_requests (ts)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_api_requests_user ON api_requests (username)')
        # Client IP for security forensics (added 2026-06-18). ADD COLUMN IF NOT
        # EXISTS migrates the already-deployed table in place.
        cur.execute("ALTER TABLE api_requests ADD COLUMN IF NOT EXISTS ip TEXT")
        cur.execute('CREATE INDEX IF NOT EXISTS idx_api_requests_ip ON api_requests (ip)')

        # Create templates table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS templates (
                id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                name VARCHAR(255) UNIQUE NOT NULL,
                data JSONB NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        cur.execute('CREATE INDEX IF NOT EXISTS idx_templates_name ON templates(name)')

        # Create updated_at trigger function
        cur.execute('''
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        ''')

        # Create trigger (drop first to avoid errors)
        cur.execute('DROP TRIGGER IF EXISTS templates_updated_at ON templates')
        cur.execute('''
            CREATE TRIGGER templates_updated_at
                BEFORE UPDATE ON templates
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column()
        ''')

        # Create records table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS records (
                id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                record_id CHAR(26) UNIQUE NOT NULL,
                record_type VARCHAR(50) NOT NULL,
                record_domain VARCHAR(50) NOT NULL,
                data JSONB NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        cur.execute('CREATE INDEX IF NOT EXISTS idx_records_record_id ON records(record_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_records_type ON records(record_type)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_records_domain ON records(record_domain)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_records_created ON records(created_at)')

        # Record history: prior content snapshotted before every update/delete
        # (audit trail + undo; deletes are admin-only and always recoverable).
        cur.execute('''
            CREATE TABLE IF NOT EXISTS record_history (
                id BIGSERIAL PRIMARY KEY,
                record_id CHAR(26) NOT NULL,
                action TEXT NOT NULL,
                actor TEXT,
                archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                data JSONB NOT NULL
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_record_history_rid ON record_history(record_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_records_data_gin ON records USING GIN (data)')

        # --- Record versioning + provenance (2026-06-30) ---------------------
        # Additive, idempotent migrations. `version` constant-default NOT NULL is a
        # metadata-only change on PG11+ (no table rewrite). content_hash backfills
        # separately (nullable). These let downstream reasoning pin & detect drift.
        cur.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS version INT NOT NULL DEFAULT 1")
        cur.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS content_hash CHAR(64)")
        cur.execute("ALTER TABLE record_history ADD COLUMN IF NOT EXISTS version INT")
        cur.execute("ALTER TABLE record_history ADD COLUMN IF NOT EXISTS content_hash CHAR(64)")
        cur.execute("ALTER TABLE record_history ADD COLUMN IF NOT EXISTS change_note TEXT")
        cur.execute("ALTER TABLE record_history ADD COLUMN IF NOT EXISTS change_class TEXT")

        # Explicit co-author edit grants (keyed on Authentik username, never ORCID).
        # role is constrained to 'editor' — there is no higher tier to escalate to.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS record_acl (
                record_id CHAR(26) NOT NULL,
                grantee_identity TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'editor' CHECK (role IN ('editor')),
                granted_by TEXT,
                granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (record_id, grantee_identity)
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_record_acl_rid ON record_acl(record_id)')

        # Create portal access log table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS portal_access_log (
                id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                username VARCHAR(255),
                accessed_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # Cached vocabulary parsed from wiki
        cur.execute('''
            CREATE TABLE IF NOT EXISTS vocabulary_cache (
                id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                section VARCHAR(100) NOT NULL,
                category VARCHAR(255) NOT NULL,
                description TEXT DEFAULT '',
                terms JSONB NOT NULL DEFAULT '[]',
                synced_at TIMESTAMPTZ DEFAULT NOW(),
                wiki_page VARCHAR(100),
                UNIQUE(section, category)
            )
        ''')

        # Sync audit log
        cur.execute('''
            CREATE TABLE IF NOT EXISTS vocabulary_sync_log (
                id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                synced_at TIMESTAMPTZ DEFAULT NOW(),
                synced_by VARCHAR(255) DEFAULT 'system',
                sections_count INT DEFAULT 0,
                categories_count INT DEFAULT 0,
                status VARCHAR(20) DEFAULT 'success',
                error_message TEXT
            )
        ''')

        # User proposals for vocabulary changes
        cur.execute('''
            CREATE TABLE IF NOT EXISTS vocabulary_proposals (
                id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                proposal_type VARCHAR(30) NOT NULL,
                section VARCHAR(100) NOT NULL,
                category VARCHAR(255),
                term VARCHAR(255),
                description TEXT DEFAULT '',
                proposed_by VARCHAR(255) NOT NULL,
                proposed_at TIMESTAMPTZ DEFAULT NOW(),
                status VARCHAR(20) DEFAULT 'pending',
                reviewed_by VARCHAR(255),
                reviewed_at TIMESTAMPTZ,
                review_comment TEXT
            )
        ''')

        conn.commit()
        cur.close()
        conn.close()
        # One-time: stamp content_hash on pre-versioning records (idempotent, no-op after).
        backfill_content_hashes()
        return True
    except Exception as e:
        print(f"Error initializing tables: {e}")
        return False


# =============================================================================
# Discovery feature DB (isaac_discovery)
# =============================================================================
# An isolated database, separate from the records DB above, backing the portal's
# "discovery" tab. It is reached via the DISCOVERY_* env vars and the
# least-privilege discovery_user role (owner of the isaac_discovery DB and its
# public schema; no access to the records DB or any other DB on the cluster).
# This is deliberately a SEPARATE connection from get_db_connection(): the two
# DBs share a host:port (the pgbouncer pooler) but nothing else.

def get_discovery_db_connection():
    """Connection to the isolated isaac_discovery DB (discovery feature).

    Reads the DISCOVERY_* env vars so it is fully independent of the records-DB
    connection (PG*). Same psycopg2 driver and RealDictCursor convention."""
    _require_psycopg2()
    return psycopg2.connect(
        host=os.environ.get('DISCOVERY_PGHOST', 'localhost'),
        port=os.environ.get('DISCOVERY_PGPORT', '5432'),
        database=os.environ.get('DISCOVERY_PGDATABASE', 'isaac_discovery'),
        user=os.environ.get('DISCOVERY_PGUSER', 'discovery_user'),
        password=os.environ.get('DISCOVERY_PGPASSWORD', ''),
        cursor_factory=RealDictCursor
    )


def is_discovery_db_configured():
    """True when the discovery DB env is present (DISCOVERY_PGHOST set).

    When absent (local dev, or before the env/Secret is provisioned) the
    discovery feature stays dormant — init is skipped and the tab can show a
    'not configured' state rather than erroring."""
    return bool(os.environ.get('DISCOVERY_PGHOST'))


def test_discovery_db_connection():
    """Test the discovery DB connection (for the tab's status / a health check)."""
    if not is_discovery_db_configured():
        return False
    try:
        conn = get_discovery_db_connection()
        conn.close()
        return True
    except Exception:
        return False


def init_discovery_tables():
    """Bootstrap the isaac_discovery schema on startup (idempotent, non-fatal).

    discovery_user owns the DB and its public schema, so it can create/alter its
    own objects here. Today this only stamps a bookkeeping table that records the
    schema version and proves DDL works end-to-end; the discovery feature's real
    tables get added here (same CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT
    EXISTS pattern as init_tables() above). Guarded and try/except-wrapped so it
    never blocks pod startup if the DB is briefly unreachable during rollout."""
    if not is_discovery_db_configured():
        return False
    try:
        conn = get_discovery_db_connection()
        cur = conn.cursor()
        # Bookkeeping / migration marker. Single-row table keyed by a constant.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS discovery_meta (
                id BOOLEAN PRIMARY KEY DEFAULT TRUE,
                schema_version INT NOT NULL DEFAULT 1,
                initialized_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT discovery_meta_singleton CHECK (id)
            )
        ''')
        cur.execute('''
            INSERT INTO discovery_meta (id) VALUES (TRUE)
            ON CONFLICT (id) DO NOTHING
        ''')
        # --- discovery feature tables go here ---
        # Hypothesis-driven reasoning workbench (Discovery page). These are NOT
        # ISAAC records and live only here; record_ids referenced below are plain
        # strings into the records DB (no cross-DB FK by design).
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hyp_projects (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                project_id CHAR(26) UNIQUE NOT NULL,
                owner_identity TEXT NOT NULL,
                title TEXT NOT NULL,
                goal TEXT,
                material_system TEXT,
                reaction TEXT,
                status TEXT DEFAULT 'active',
                next_experiment JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_hyp_projects_owner '
                    'ON hyp_projects (owner_identity, updated_at DESC)')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hyp_hypotheses (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                hypothesis_id CHAR(26) UNIQUE NOT NULL,
                project_id CHAR(26) NOT NULL REFERENCES hyp_projects(project_id),
                label TEXT,
                statement TEXT NOT NULL,
                hypothesis_type TEXT,
                mechanism JSONB,
                origin JSONB,
                status TEXT DEFAULT 'proposed',
                confidence REAL,
                confidence_basis TEXT,
                created_by TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_hyp_hypotheses_project '
                    'ON hyp_hypotheses (project_id)')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hyp_predictions (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                prediction_id CHAR(26) UNIQUE NOT NULL,
                hypothesis_id CHAR(26) NOT NULL REFERENCES hyp_hypotheses(hypothesis_id),
                label TEXT,
                descriptor_name TEXT NOT NULL,
                direction TEXT,
                reference_condition TEXT,
                magnitude TEXT,
                output_quantity TEXT,
                falsification_criterion TEXT,
                verdict TEXT,
                strength TEXT,
                evidence_record_ids TEXT[],
                rationale TEXT,
                mlflow_run_url TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_hyp_predictions_hypothesis '
                    'ON hyp_predictions (hypothesis_id)')
        # work_status: the workflow lifecycle of getting to a verdict (distinct
        # from `verdict`, which is the scientific outcome). Drives the Validation
        # board. awaiting_evidence | more_work_pending | compute_submitted |
        # compute_running | evaluated.
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS "
                    "work_status TEXT NOT NULL DEFAULT 'awaiting_evidence'")
        # evidence_pins: {record_id, version, content_hash} snapshot at evaluate-time, so a
        # later MATERIAL edit to a cited record can be flagged (drift) for re-examination.
        # Sidecar to evidence_record_ids (TEXT[]) — the scorer's dedup key is untouched.
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS evidence_pins JSONB")
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hyp_events (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                project_id CHAR(26) NOT NULL REFERENCES hyp_projects(project_id),
                hypothesis_id CHAR(26),
                event_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                detail TEXT,
                evidence_record_ids TEXT[],
                mlflow_run_url TEXT,
                actor_identity TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_hyp_events_project '
                    'ON hyp_events (project_id, created_at DESC)')
        # v2 (stubbed now): in-portal human<->agent chat.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hyp_messages (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                project_id CHAR(26) NOT NULL REFERENCES hyp_projects(project_id),
                role TEXT NOT NULL,
                body TEXT NOT NULL,
                author_identity TEXT,
                consumed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        ''')
        # v1: what each hypothesis predicts for this measurable — the rows the
        # server aggregates into the cross-hypothesis discrimination matrix.
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS "
                    "discriminates JSONB")
        # provenance: HOW this falsifying prediction was generated/inspired
        # (from the hypothesis mechanism, from literature, by discrimination design,
        # from a prior result, ...). {type, summary, reasoning, sources}.
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS origin JSONB")
        # v1: typed relations between hypotheses (graph, not list):
        # supersedes | derived_from | competes_with | co_operating.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hyp_hypothesis_relations (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                project_id CHAR(26) NOT NULL REFERENCES hyp_projects(project_id),
                from_hypothesis_id CHAR(26) NOT NULL REFERENCES hyp_hypotheses(hypothesis_id),
                to_hypothesis_id CHAR(26) NOT NULL REFERENCES hyp_hypotheses(hypothesis_id),
                relation_type TEXT NOT NULL,
                note TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_hyp_relations_project '
                    'ON hyp_hypothesis_relations (project_id)')
        # v1: a prediction has MANY compute runs (failed + resubmit). Backends are
        # data (vasp/uma/catmap/...), not enum-locked. status: queued | running |
        # completed | failed | resubmitted.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hyp_compute_runs (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                run_id CHAR(26) UNIQUE NOT NULL,
                prediction_id CHAR(26) NOT NULL REFERENCES hyp_predictions(prediction_id),
                backend TEXT,
                engine TEXT,
                resource TEXT,
                slurm_job_id TEXT,
                mlflow_run_url TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                params JSONB,
                metrics JSONB,
                note TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_hyp_runs_prediction '
                    'ON hyp_compute_runs (prediction_id)')
        # v1: explicit evidence include/exclude override layer (curation on top of
        # the auto element-matched candidate set): {include:[record_id], exclude:[]}.
        cur.execute("ALTER TABLE hyp_projects ADD COLUMN IF NOT EXISTS "
                    "evidence_overrides JSONB")
        # The DATASET OF INTEREST: the human points the agent at the record set the
        # project is about (so it doesn't have to divine scope from a 1M-record DB).
        # {record_ids:[...], description, set_by, set_at}. Coverage is checked against
        # it; the agent should use all of it (or justify) and may reach beyond.
        cur.execute("ALTER TABLE hyp_projects ADD COLUMN IF NOT EXISTS dataset JSONB")
        # Project sharing: owner grants another portal identity read (or write)
        # access, so it shows in that user's Discovery tab when they log in.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hyp_project_shares (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                project_id CHAR(26) NOT NULL REFERENCES hyp_projects(project_id),
                identity TEXT NOT NULL,
                access TEXT NOT NULL DEFAULT 'read',
                granted_by TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (project_id, identity)
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_hyp_shares_identity '
                    'ON hyp_project_shares (identity)')

        # --- Scientific-rigor additions (manifest method v0.13) -------------
        # (1) Hypothesis individuation: a hypothesis is its EMPIRICAL CONTENT.
        # Refinements that only sharpen a parameter are VERSIONS of the same
        # node (history below); a genuinely new claim that predicts differently
        # is a new node linked by `supersedes`. `version` is the live count.
        cur.execute("ALTER TABLE hyp_hypotheses ADD COLUMN IF NOT EXISTS "
                    "version INT NOT NULL DEFAULT 1")
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hyp_hypothesis_versions (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                hypothesis_id CHAR(26) NOT NULL REFERENCES hyp_hypotheses(hypothesis_id),
                version INT NOT NULL,
                statement TEXT,
                mechanism JSONB,
                confidence REAL,
                change_note TEXT,
                change_type TEXT,
                actor_identity TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (hypothesis_id, version)
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_hyp_versions_hyp '
                    'ON hyp_hypothesis_versions (hypothesis_id, version)')
        # (2) A `supersedes`/relation must be able to declare the DISCRIMINATING
        # OBSERVABLE on which parent and child predict differently (the "extra
        # predictive element" that makes the child a new hypothesis, not a
        # refinement), plus what the change retained vs abandoned and its type.
        cur.execute("ALTER TABLE hyp_hypothesis_relations ADD COLUMN IF NOT EXISTS "
                    "discriminating_observable TEXT")
        cur.execute("ALTER TABLE hyp_hypothesis_relations ADD COLUMN IF NOT EXISTS "
                    "retained_vs_abandoned TEXT")
        cur.execute("ALTER TABLE hyp_hypothesis_relations ADD COLUMN IF NOT EXISTS "
                    "change_type TEXT")
        # (3) Use-novelty / no double-counting: when a prediction is evaluated,
        # declare the independence of the evidence used. {roles:[{evidence,role}],
        # parameters_fit_to:[...], tested_against:[...], model_was_fit:bool}.
        # Stored + surfaced now (gated later).
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS "
                    "evidence_independence JSONB")
        # grounding — the hypothesis's EPISTEMIC STANDING: 'standing_prior' (an
        # established/literature mechanism that exists independently of this dataset) vs
        # 'ad_hoc' (introduced/parameterised FROM this dataset to fit it). Gates the
        # use-novelty accommodation discount: only ad_hoc + fitted-overlap is zeroed; a
        # standing_prior that a trend merely inspired is NOT accommodation. Default ad_hoc.
        cur.execute("ALTER TABLE hyp_hypotheses ADD COLUMN IF NOT EXISTS grounding TEXT")
        # margin ∈ [0,1] — per-verdict CONTRADICTION SHARPNESS: how decisively the
        # observation diverged PAST the prediction's falsification threshold (1 = far
        # past / unambiguous, 0 = right at the line). Refines the coarse strength tier
        # and gates the strong-contradiction falsification cap. Optional/back-compat.
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS margin REAL")
        # cross_system — true if the verdict's evidence is a borrowed ANALOG from a
        # different material / reaction / mechanism class. Such evidence can SUGGEST but
        # never ESTABLISH: capped at weak, excluded from the reliability count (the Cu-Ag
        # lesson — a borrowed analog must not drive a hypothesis to 'reliable').
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS "
                    "cross_system BOOLEAN")
        # reliability of the EVIDENCE itself (trust, distinct from method-compat/strength).
        # SERVER-derived tier from a machine-checkable basis; low tiers move belief but
        # don't count toward reliability. Opt-in: NULL → scored as before.
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS "
                    "reliability_tier TEXT")
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS "
                    "reliability_basis JSONB")
        # observable_key — the IDENTITY of what a verdict TESTS (quantity @ system),
        # distinct from the evidence/calc that produced it. Two decisive verdicts on the
        # SAME observable via DIFFERENT methods (e.g. PBE then RPBE of the same ΔΔE) are
        # ROBUSTNESS, not two INDEPENDENT verdicts: they vary the method, not the test, so
        # the 2nd is attenuated and does NOT count toward reliability. Opt-in: NULL → scored
        # exactly as before (independence judged on evidence identity alone).
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS "
                    "observable_key TEXT")
        # literature — VERIFIED literature evidence behind a verdict. An LLM's recall is a
        # CLAIM, not data, until checked: each entry {doi, claim, supported(bool, the agent/
        # Edison attested the paper actually makes the claim), peer_reviewed(bool)} is stamped
        # server-side with resolved(bool, Crossref) + title. A resolved+supported entry is
        # first-class CITED evidence (tier from maturity: peer→single_source, preprint→
        # anecdotal); a non-resolving DOI is a FABRICATION (flagged, earns nothing).
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS "
                    "literature JSONB")
        # (4) Confidence as a first-class TIME SERIES (one row per change), so the
        # "Belief River" reads real history instead of scraping event prose. Legacy
        # projects are backfilled-on-read from their event log (see discovery.py).
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hyp_confidence_snapshots (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                project_id CHAR(26) NOT NULL REFERENCES hyp_projects(project_id),
                hypothesis_id CHAR(26) NOT NULL REFERENCES hyp_hypotheses(hypothesis_id),
                confidence REAL,
                basis TEXT,
                source TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_hyp_conf_snap_project '
                    'ON hyp_confidence_snapshots (project_id, created_at)')
        # (5) Independent rigor-critic findings: an ADVERSARIAL reviewer (a separate
        # agent, not the one doing the work) reads the project and records where it
        # thinks a claim fails — esp. omitted declarations the deterministic
        # method_compliance check can't see (a fit model used as confirmation with a
        # blank evidence_independence). Addressable + resolvable; later gateable.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hyp_rigor_findings (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                finding_id CHAR(26) UNIQUE NOT NULL,
                project_id CHAR(26) NOT NULL REFERENCES hyp_projects(project_id),
                target_type TEXT,
                target_id TEXT,
                category TEXT,
                severity TEXT NOT NULL DEFAULT 'major',
                summary TEXT NOT NULL,
                detail TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                raised_by TEXT,
                resolution TEXT,
                resolved_by TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolved_at TIMESTAMPTZ
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_hyp_rigor_project '
                    'ON hyp_rigor_findings (project_id, status)')
        # (6) Async work the agent KICKED OFF but couldn't await this turn (an Edison
        # literature query, a submitted calculation) — so the dashboard can show a
        # project has RESUMABLE pending steps that, once finished, are worth coming
        # back for. kind: literature | compute | external. status: pending | ready |
        # done | failed.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hyp_async_tasks (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                task_id CHAR(26) UNIQUE NOT NULL,
                project_id CHAR(26) NOT NULL REFERENCES hyp_projects(project_id),
                kind TEXT NOT NULL DEFAULT 'external',
                external_ref TEXT,
                summary TEXT,
                poll_hint TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                hypothesis_id CHAR(26),
                prediction_id CHAR(26),
                submitted_by TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolved_at TIMESTAMPTZ
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_hyp_async_project '
                    'ON hyp_async_tasks (project_id, status)')

        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error initializing discovery tables: {e}")
        return False


# =============================================================================
# Record Operations
# =============================================================================

def archive_record(record_id: str, data: dict, action: str, actor: str | None = None) -> None:
    """Snapshot a record's prior content before an update or delete (audit/undo)."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO record_history (record_id, action, actor, data) VALUES (%s, %s, %s, %s)",
            (record_id, action, actor, json.dumps(data)))
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        logger.exception("archive_record failed for %s", record_id)


def record_owner(record_id: str) -> str | None:
    """The uploaded_by of a record, or None if missing/unowned (legacy records)."""
    rec = get_record(record_id)
    if not rec:
        return None
    return (rec.get("attribution") or {}).get("uploaded_by")


class RecordExistsError(Exception):
    """Raised when an INSERT hits an existing record_id (no silent overwrite)."""


class RecordNotFoundError(Exception):
    """Raised when an UPDATE targets a record_id that does not exist."""


def save_record(record_data: dict, *, skip_validation: bool = False,
                uploaded_by: str | None = None, mode: str = "upsert") -> str:
    """
    Save an ISAAC record to the database.

    VALIDATION CHOKEPOINT: every record persisted through this function is
    validated by portal/validation.py (schema + vocabulary + semantic).
    This is the single enforcement point shared by ALL ingestion paths
    (REST API, Streamlit validator page, record form, future tools) — a
    record that fails validation cannot reach the database, regardless of
    which door it came through. To change what validation does, edit
    portal/validation.py; every path picks up the change automatically.

    Args:
        record_data: The complete ISAAC record as a dictionary
        skip_validation: Admin/migration escape hatch ONLY. Bypasses
            validation; every use is logged. Never set this from a
            user-facing path.

    Returns:
        The record_id of the saved record

    Raises:
        validation.ValidationError: If the record fails validation
            (carries the full structured per-layer result).
        ValueError: If required fields are missing
        Exception: If database operation fails
    """
    # Server-stamped attribution: the AUTHENTICATED identity, set before
    # validation so every ingestion door (API + both Streamlit paths) writes
    # tamper-proof provenance. Client-supplied uploaded_by is overwritten.
    if uploaded_by:
        record_data.setdefault("attribution", {})["uploaded_by"] = uploaded_by

    if skip_validation:
        logger.warning(
            "save_record VALIDATION BYPASS (skip_validation=True) for record_id=%s",
            record_data.get('record_id'),
        )
    else:
        import validation  # deferred: validation imports ontology at module load
        result = validation.validate_record_full(record_data)
        if not result["valid"]:
            raise validation.ValidationError(result)

    record_id = record_data.get('record_id')
    record_type = record_data.get('record_type')
    record_domain = record_data.get('record_domain')

    if not record_id:
        raise ValueError("record_id is required")
    if not record_type:
        raise ValueError("record_type is required")
    if not record_domain:
        raise ValueError("record_domain is required")

    import record_provenance as _rp  # pure-logic, deferred like validation
    chash = _rp.content_hash(record_data)

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if mode == "insert":
            # Pure INSERT. A record_id collision RAISES (RecordExistsError) — a
            # caller may NOT silently overwrite an existing record by supplying
            # its id. Editing an owned record goes through PUT (update).
            try:
                cur.execute('''
                    INSERT INTO records (record_id, record_type, record_domain, data, content_hash)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING record_id
                ''', (record_id, record_type, record_domain, json.dumps(record_data), chash))
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                raise RecordExistsError(record_id)
        elif mode == "update":
            # NOTE: the user PUT/edit path uses update_record_versioned() (transactional,
            # version-CAS, archived). This branch remains for non-edit internal callers and
            # still bumps version + stamps the hash so no door writes stale provenance.
            cur.execute('''
                UPDATE records SET record_type = %s, record_domain = %s, data = %s,
                       content_hash = %s, version = version + 1
                WHERE record_id = %s
                RETURNING record_id
            ''', (record_type, record_domain, json.dumps(record_data), chash, record_id))
            if cur.fetchone() is None:
                conn.rollback()
                raise RecordNotFoundError(record_id)
            conn.commit()
            return record_id.strip()
        else:  # "upsert" — admin/migration paths only (never a user door)
            cur.execute('''
                INSERT INTO records (record_id, record_type, record_domain, data, content_hash)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (record_id) DO UPDATE SET
                    record_type = EXCLUDED.record_type,
                    record_domain = EXCLUDED.record_domain,
                    data = EXCLUDED.data,
                    content_hash = EXCLUDED.content_hash,
                    version = records.version + 1
                RETURNING record_id
            ''', (record_id, record_type, record_domain, json.dumps(record_data), chash))

        result = cur.fetchone()
        conn.commit()
        return result['record_id'].strip()
    finally:
        cur.close()
        conn.close()


class VersionConflictError(Exception):
    """A concurrent edit was detected at write time (the version moved between our read and
    write) -> HTTP 409."""


class PreconditionFailedError(Exception):
    """An explicit `If-Match: <version>` did not match the current version, or was malformed
    -> HTTP 412. Distinct from a race so clients can tell a stale precondition from a
    genuine concurrent edit."""


def update_record_versioned(record_id: str, new_data: dict, *, actor: str | None,
                            change_note: str | None = None, if_match=None,
                            action: str = "update") -> dict:
    """The ONE transactional edit path for owned records (PUT).

    In a single transaction: validate (chokepoint) -> SELECT ... FOR UPDATE ->
    archive the PRIOR snapshot -> UPDATE ... WHERE version=expected (compare-and-swap).
    Preserves the original owner (an edit never transfers ownership). Stamps version+1 and
    the recomputed content_hash. Returns {record_id, version, content_hash, change_class}.
    Raises RecordNotFoundError, VersionConflictError, validation.ValidationError.
    """
    import record_provenance as _rp
    import validation
    if not record_id:
        raise ValueError("record_id is required")
    new_data = dict(new_data or {})
    new_data["record_id"] = record_id  # force path id; never trust a body-supplied id
    result = validation.validate_record_full(new_data)
    if not result["valid"]:
        raise validation.ValidationError(result)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT data, version, content_hash FROM records WHERE record_id=%s FOR UPDATE",
                    (record_id,))
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            raise RecordNotFoundError(record_id)
        cur_version, prior_data, prior_hash = row["version"], (row["data"] or {}), row["content_hash"]
        if if_match is not None:
            try:
                want = int(str(if_match).strip().strip('"'))
            except (TypeError, ValueError):
                conn.rollback()
                raise PreconditionFailedError(f"malformed If-Match: {if_match!r}")
            if want != int(cur_version):
                conn.rollback()
                raise PreconditionFailedError(f"expected version {want}, current {cur_version}")

        # Ownership is immutable on edit: re-stamp the existing owner over whatever the body says.
        prior_owner = (prior_data.get("attribution") or {}).get("uploaded_by")
        if prior_owner is not None:
            new_data.setdefault("attribution", {})["uploaded_by"] = prior_owner

        new_hash = _rp.content_hash(new_data)
        baseline = prior_hash or _rp.content_hash(prior_data)  # legacy rows may have NULL hash
        change_class = "material" if new_hash != baseline else "metadata"

        cur.execute('''INSERT INTO record_history
                       (record_id, action, actor, data, version, content_hash, change_note, change_class)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (record_id, action, actor, json.dumps(prior_data), cur_version,
                     prior_hash, change_note, change_class))
        cur.execute('''UPDATE records
                       SET data=%s, content_hash=%s, version=version+1,
                           record_type=%s, record_domain=%s
                       WHERE record_id=%s AND version=%s
                       RETURNING version''',
                    (json.dumps(new_data), new_hash, new_data.get("record_type"),
                     new_data.get("record_domain"), record_id, cur_version))
        upd = cur.fetchone()
        if upd is None:  # someone else committed an edit between our SELECT and UPDATE
            conn.rollback()
            raise VersionConflictError("concurrent edit detected")
        conn.commit()
        return {"record_id": record_id, "version": upd["version"],
                "content_hash": new_hash, "change_class": change_class}
    finally:
        cur.close()
        conn.close()


def reassign_owner(record_id: str, new_owner: str, *, actor: str | None, reason: str) -> int:
    """ADMIN-ONLY ownership correction. Archives the prior state
    (action='reassign_owner', class='metadata'), sets attribution.uploaded_by, bumps
    version. content_hash is unchanged (attribution is excluded from the scientific hash),
    so this NEVER triggers downstream re-examination. Returns the new version."""
    import record_provenance as _rp
    if not new_owner or not str(new_owner).strip():
        raise ValueError("new owner identity required")
    if not reason or not str(reason).strip():
        raise ValueError("a reason is required for an ownership reassignment")
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT data, version, content_hash FROM records WHERE record_id=%s FOR UPDATE",
                    (record_id,))
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            raise RecordNotFoundError(record_id)
        data, cur_version, prior_hash = (row["data"] or {}), row["version"], row["content_hash"]
        cur.execute('''INSERT INTO record_history
                       (record_id, action, actor, data, version, content_hash, change_note, change_class)
                       VALUES (%s,'reassign_owner',%s,%s,%s,%s,%s,'metadata')''',
                    (record_id, actor, json.dumps(data), cur_version, prior_hash, reason))
        data.setdefault("attribution", {})["uploaded_by"] = new_owner
        new_hash = _rp.content_hash(data)  # == prior content hash (attribution not hashed)
        cur.execute('''UPDATE records SET data=%s, content_hash=%s, version=version+1
                       WHERE record_id=%s AND version=%s RETURNING version''',
                    (json.dumps(data), new_hash, record_id, cur_version))
        upd = cur.fetchone()
        if upd is None:
            conn.rollback()
            raise VersionConflictError("concurrent edit during reassign")
        conn.commit()
        return upd["version"]
    finally:
        cur.close()
        conn.close()


# --- Co-author ACL (explicit editor grants, keyed on username) -------------

def acl_add_editor(record_id: str, grantee: str, granted_by: str | None) -> bool:
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute('''INSERT INTO record_acl (record_id, grantee_identity, role, granted_by)
                       VALUES (%s,%s,'editor',%s)
                       ON CONFLICT (record_id, grantee_identity)
                       DO UPDATE SET granted_by=EXCLUDED.granted_by, granted_at=NOW()''',
                    (record_id, grantee, granted_by))
        conn.commit(); return True
    finally:
        cur.close(); conn.close()


def acl_remove_editor(record_id: str, grantee: str) -> bool:
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM record_acl WHERE record_id=%s AND grantee_identity=%s",
                    (record_id, grantee))
        removed = cur.rowcount > 0
        conn.commit(); return removed
    finally:
        cur.close(); conn.close()


def acl_list(record_id: str) -> list:
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute('''SELECT grantee_identity, role, granted_by, granted_at
                       FROM record_acl WHERE record_id=%s ORDER BY granted_at''', (record_id,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


def acl_editor_usernames(record_id: str) -> set:
    """The set of usernames holding an editor grant — consumed by the authz resolver."""
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT grantee_identity FROM record_acl WHERE record_id=%s", (record_id,))
        return {r["grantee_identity"] for r in cur.fetchall()}
    finally:
        cur.close(); conn.close()


# --- History / diff --------------------------------------------------------

def record_history(record_id: str) -> list:
    """Version history, oldest first. Ordered by archived_at (legacy rows have NULL
    version, so we never order by version)."""
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute('''SELECT action, actor, archived_at, version, content_hash,
                              change_note, change_class
                       FROM record_history WHERE record_id=%s ORDER BY archived_at''',
                    (record_id,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


def record_snapshot(record_id: str, version: int):
    """The archived record data for a specific prior version, or None."""
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute('''SELECT data FROM record_history WHERE record_id=%s AND version=%s
                       ORDER BY archived_at DESC LIMIT 1''', (record_id, version))
        r = cur.fetchone()
        return r["data"] if r else None
    finally:
        cur.close(); conn.close()


def record_version_hash(record_id: str):
    """Lightweight {version, content_hash} for a record, or None. Read-only — the discovery
    side calls this to PIN cited evidence and later detect drift, without coupling to the
    record's data or the records DB write path."""
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT version, content_hash FROM records WHERE record_id=%s", (record_id,))
        r = cur.fetchone()
        return {"version": r["version"], "content_hash": r["content_hash"]} if r else None
    finally:
        cur.close(); conn.close()


def record_hashes(record_ids) -> dict:
    """Batch {record_id: content_hash} for many records in ONE query — used by the drift
    check so a briefing is one query, not N (the project view auto-refreshes)."""
    ids = [r for r in (record_ids or []) if r]
    if not ids:
        return {}
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT record_id, content_hash FROM records WHERE record_id = ANY(%s)", (ids,))
        return {r["record_id"]: r["content_hash"] for r in cur.fetchall()}
    finally:
        cur.close(); conn.close()


def backfill_content_hashes(max_rows: int = 20000) -> int:
    """One-time, idempotent: stamp content_hash on records created before versioning (rows
    where it is NULL), so drift detection works for the existing corpus. Re-runs are no-ops.
    Exception-safe — never blocks startup."""
    import record_provenance as _rp
    done = 0
    try:
        conn = get_db_connection(); cur = conn.cursor()
        try:
            cur.execute("SELECT record_id, data FROM records WHERE content_hash IS NULL LIMIT %s",
                        (max_rows,))
            for r in cur.fetchall():
                try:
                    h = _rp.content_hash(r["data"] or {})
                    cur.execute("UPDATE records SET content_hash=%s "
                                "WHERE record_id=%s AND content_hash IS NULL", (h, r["record_id"]))
                    done += 1
                except Exception:
                    logger.exception("hash backfill failed for %s", r.get("record_id"))
            conn.commit()
        finally:
            cur.close(); conn.close()
        if done:
            logger.info("content_hash backfill stamped %d record(s)", done)
    except Exception:
        logger.exception("content_hash backfill aborted")
    return done


def get_record(record_id: str) -> dict:
    """
    Retrieve a record by its ID.

    Args:
        record_id: The 26-character ULID record identifier

    Returns:
        The record data as a dictionary, or None if not found
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('SELECT data, created_at FROM records WHERE record_id = %s', (record_id,))
        row = cur.fetchone()

        if not row:
            return None

        return row['data']
    finally:
        cur.close()
        conn.close()


def list_records(limit: int = 100, offset: int = 0, filters: dict | None = None,
                 full: bool = False) -> tuple:
    """
    List records with optional server-side filters.

    filters keys (all optional):
        record_type, record_domain    -> indexed column equality
        reaction                      -> JSONB context.electrochemistry.reaction
        material_contains             -> ILIKE on sample.material.name
        created_after, created_before -> created_at range (ISO 8601)

    Returns (rows, total_count). rows carry summary fields, or the full
    record JSON when full=True (callers should cap limit accordingly).
    """
    filters = filters or {}
    where, params = [], []
    if filters.get('record_type'):
        where.append('record_type = %s'); params.append(filters['record_type'])
    if filters.get('record_domain'):
        where.append('record_domain = %s'); params.append(filters['record_domain'])
    if filters.get('reaction'):
        where.append("data->'context'->'electrochemistry'->>'reaction' = %s")
        params.append(filters['reaction'])
    if filters.get('material_contains'):
        where.append("data->'sample'->'material'->>'name' ILIKE %s")
        params.append(f"%{filters['material_contains']}%")
    if filters.get('created_after'):
        where.append('created_at >= %s'); params.append(filters['created_after'])
    if filters.get('created_before'):
        where.append('created_at <= %s'); params.append(filters['created_before'])
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f'SELECT COUNT(*) AS count FROM records {where_sql}', params)
        total = cur.fetchone()['count']
        select_cols = 'data, created_at' if full else 'record_id, record_type, record_domain, created_at'
        cur.execute(
            f'SELECT {select_cols} FROM records {where_sql} '
            f'ORDER BY created_at DESC LIMIT %s OFFSET %s',
            params + [limit, offset])
        rows = []
        for row in cur.fetchall():
            if full:
                rec = row['data']
                if isinstance(rec, str):
                    rec = json.loads(rec)
                rows.append(rec)
            else:
                rows.append({
                    'record_id': row['record_id'].strip(),
                    'record_type': row['record_type'],
                    'record_domain': row['record_domain'],
                    'created_at': row['created_at'].isoformat() if hasattr(row['created_at'], 'isoformat') else row['created_at'],
                })
        return rows, total
    finally:
        cur.close()
        conn.close()


def log_api_request(username, method, endpoint, status, duration_ms, ip=None):
    """Fire-and-forget API usage logging. MUST never break a request."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO api_requests (username, method, endpoint, status, duration_ms, ip) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (username, method, endpoint, status, duration_ms, ip))
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def get_api_usage_stats(days: int = 30) -> dict:
    """Aggregates for the usage dashboard: daily series, by-user, by-endpoint."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT date_trunc('day', ts) AS day, COUNT(*) AS n "
            "FROM api_requests WHERE ts > now() - (%s || ' days')::interval "
            "GROUP BY 1 ORDER BY 1", (days,))
        daily = [{'day': r['day'].date().isoformat(), 'requests': r['n']} for r in cur.fetchall()]
        cur.execute(
            "SELECT COALESCE(username, 'unauthenticated') AS who, COUNT(*) AS n "
            "FROM api_requests WHERE ts > now() - (%s || ' days')::interval "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 20", (days,))
        by_user = [{'user': r['who'], 'requests': r['n']} for r in cur.fetchall()]
        cur.execute(
            "SELECT method || ' ' || endpoint AS what, COUNT(*) AS n, "
            "ROUND(AVG(duration_ms)::numeric, 1) AS avg_ms "
            "FROM api_requests WHERE ts > now() - (%s || ' days')::interval "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 20", (days,))
        by_endpoint = [{'endpoint': r['what'], 'requests': r['n'],
                        'avg_ms': float(r['avg_ms'] or 0)} for r in cur.fetchall()]
        cur.execute(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT username) AS users, "
            "COUNT(*) FILTER (WHERE status BETWEEN 400 AND 499) AS rejections, "
            "COUNT(*) FILTER (WHERE status >= 500) AS server_errors "
            "FROM api_requests WHERE ts > now() - (%s || ' days')::interval", (days,))
        row = cur.fetchone()
        # Forensics: unauthenticated traffic grouped by source IP (added 2026-06-18).
        cur.execute(
            "SELECT COALESCE(ip, 'unknown') AS ip, COUNT(*) AS n, "
            "MIN(ts) AS first_seen, MAX(ts) AS last_seen "
            "FROM api_requests WHERE username IS NULL "
            "AND ts > now() - (%s || ' days')::interval "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 20", (days,))
        unauth_by_ip = [{'ip': r['ip'], 'requests': r['n'],
                         'first_seen': r['first_seen'].isoformat() if r['first_seen'] else None,
                         'last_seen': r['last_seen'].isoformat() if r['last_seen'] else None}
                        for r in cur.fetchall()]
        return {'days': days, 'total_requests': row['total'], 'distinct_users': row['users'],
                'rejection_count': row['rejections'], 'server_error_count': row['server_errors'],
                'daily': daily, 'by_user': by_user,
                'by_endpoint': by_endpoint, 'unauth_by_ip': unauth_by_ip}
    finally:
        cur.close()
        conn.close()


def find_records_by_material(material_name: str, exclude_id: str, limit: int = 6) -> list:
    """Record IDs sharing a material name (parameterized — used by /suggestions)."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT record_id FROM records "
            "WHERE data->'sample'->'material'->>'name' = %s AND record_id != %s "
            "ORDER BY created_at DESC LIMIT %s",
            (material_name, exclude_id, limit))
        return [row['record_id'].strip() for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def get_records_batch(record_ids: list) -> list:
    """Fetch full records for a list of IDs in one query. Missing IDs are skipped."""
    if not record_ids:
        return []
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('SELECT data FROM records WHERE record_id = ANY(%s)', (record_ids,))
        out = []
        for row in cur.fetchall():
            rec = row['data']
            if isinstance(rec, str):
                rec = json.loads(rec)
            out.append(rec)
        return out
    finally:
        cur.close()
        conn.close()


def delete_record(record_id: str, actor: str | None = None) -> bool:
    """
    Delete a record by its ID. Prior content is archived to record_history
    first (deletes are admin-only and rare, but always recoverable).
    """
    existing = get_record(record_id)
    if existing is not None:
        archive_record(record_id, existing, "delete", actor)
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('DELETE FROM records WHERE record_id = %s RETURNING record_id', (record_id,))
        deleted = cur.fetchone()
        conn.commit()
        return deleted is not None
    finally:
        cur.close()
        conn.close()


def count_records() -> int:
    """Return the total number of records in the database."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('SELECT COUNT(*) as count FROM records')
        row = cur.fetchone()
        return row['count']
    finally:
        cur.close()
        conn.close()


# SENSITIVE records-DB tables: readable ONLY by admins via /records/query (and never by
# nano-ISAAC). Everything else in this DB — `records`, `record_history` (version history of
# public records), `templates` (form scaffolding), `vocabulary_cache` (the controlled
# ontology) — is non-sensitive reference/scientific data, open read-only to ANY authenticated
# user. Sensitivity rationale: these five carry login/usage PII (incl. client IPs), or
# access-control / moderation identities.
#   The TRUE enforcement is the isaac_readonly role's grants (DB level); this is the in-code
#   belt. KEEP THE TWO IN SYNC — when opening a table here, Dean must GRANT SELECT on it to
#   isaac_readonly (and keep REVOKE on the five below). See docs/READONLY_QUERY_GRANTS.md.
_AGENT_FORBIDDEN_TABLES = (
    "api_requests",          # usage log — usernames, endpoints, client IPs
    "portal_access_log",     # login activity — usernames, timestamps
    "vocabulary_sync_log",   # operational sync log
    "vocabulary_proposals",  # proposer/reviewer identities + moderation state
    "record_acl",            # who-can-edit-what (access-control / collaboration graph)
)


def execute_readonly_query(sql: str, max_rows: int = 50, timeout_ms: int = 5000,
                           agent_mode: bool = False) -> list:
    """
    Execute a read-only SQL query against the database.

    Security:
    - Only SELECT and WITH (CTE) statements are allowed
    - Mutation keywords (INSERT, UPDATE, DELETE, DROP, ALTER, etc.) are rejected
    - A single statement only — embedded ';' is rejected
    - System catalogs / file primitives (pg_*, information_schema, lo_*, dblink)
      are rejected so the path cannot read server files or catalog metadata
      (H2; defense-in-depth — the deployed `isaac` role is already NON-superuser)
    - Runs as the least-privilege PGUSER_RO role, inside a READ ONLY transaction,
      with a statement timeout (C2)
    - A LIMIT clause is enforced (appended if missing)
    - agent_mode=True (nano-ISAAC): additionally restricts reads to the `records`
      table — operational/control tables are rejected by name

    Args:
        sql: The SQL query string (must be SELECT or WITH)
        max_rows: Maximum rows to return (default 50)
        timeout_ms: Statement timeout in milliseconds (default 5000)
        agent_mode: If True, restrict reads to the records table (nano-ISAAC)

    Returns:
        List of row dicts from the query result

    Raises:
        ValueError: If the query is not a safe read-only SELECT/WITH
    """
    stripped = sql.strip().rstrip(";")
    upper = stripped.upper()

    if agent_mode:
        low = stripped.lower()
        hit = [tbl for tbl in _AGENT_FORBIDDEN_TABLES if re.search(r'\b' + tbl + r'\b', low)]
        if hit:
            raise ValueError(
                f"This query is scoped to the scientific `records` table; "
                f"`{hit[0]}` is an operational table and is restricted to admins.")

    # Single statement only — reject stacked statements (a ';' that is not the
    # trailing one we already stripped). Defeats "SELECT 1; <anything>".
    if ";" in stripped:
        raise ValueError("Only a single statement is allowed.")

    # Must start with SELECT or WITH
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        raise ValueError("Only SELECT or WITH (CTE) queries are allowed.")

    # Reject mutation keywords anywhere in the query
    forbidden = r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|COPY|EXECUTE|CALL)\b'
    if re.search(forbidden, upper):
        raise ValueError("Query contains forbidden mutation keywords.")

    # Reject system catalogs and server-side file/credential primitives. These
    # are not needed to query the records/vocabulary surface, and blocking them
    # closes the pg_read_file / lo_export / catalog-read exfiltration vectors
    # even on the fallback (superuser) connection. The broad PG_[A-Z_]+ catch-all
    # also blocks bare catalog reads (pg_roles, pg_class, pg_user) that an
    # explicit list misses. (C2/H2)
    forbidden_ident = (
        r'\b(PG_[A-Z_]+|INFORMATION_SCHEMA|LO_IMPORT|LO_EXPORT|LO_GET|LO_PUT|'
        r'DBLINK|CURRENT_SETTING|SET_CONFIG)\b'
    )
    if re.search(forbidden_ident, upper):
        raise ValueError("Query references a forbidden system object or function.")

    # Enforce LIMIT if not present
    if "LIMIT" not in upper:
        stripped += f" LIMIT {max_rows}"

    conn = get_readonly_db_connection()
    cur = conn.cursor()

    try:
        conn.autocommit = False
        # READ ONLY transaction: blocks any write/DDL the checks above missed.
        # Must be the first statement of the transaction (C2).
        cur.execute("SET TRANSACTION READ ONLY")
        # Parameterized timeout (M4): set_config(..., is_local=true) == SET LOCAL,
        # but accepts a bound parameter so no value is f-string-interpolated.
        # is_local=true applies it for this transaction only.
        cur.execute("SELECT set_config('statement_timeout', %s, true)", (str(int(timeout_ms)),))
        try:
            cur.execute(stripped)
            rows = cur.fetchall()
        except Exception as qe:
            conn.rollback()
            # 42501 = insufficient_privilege: the read-only role lacks SELECT on a table that
            # IS allowed by the in-code belt but not yet GRANTed (see READONLY_QUERY_GRANTS.md).
            # Surface a clear message instead of a 500.
            if getattr(qe, "pgcode", None) == "42501":
                raise ValueError(
                    "Read access to that table is not enabled yet. The `records` table is "
                    "available now; other non-sensitive tables (record_history, "
                    "vocabulary_cache, templates) are pending a DB grant — ask an admin.")
            raise
        conn.rollback()
        return [dict(row) for row in rows]
    finally:
        cur.close()
        conn.close()


# =============================================================================
# Template Operations
# =============================================================================

def save_template(name: str, data: dict) -> str:
    """
    Save a form template to the database.

    Args:
        name: Unique template name
        data: Template data (form field values)

    Returns:
        The template name
    """
    if not name or not name.strip():
        raise ValueError("Template name is required")

    name = name.strip()

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('''
            INSERT INTO templates (name, data)
            VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET data = EXCLUDED.data
            RETURNING name
        ''', (name, json.dumps(data)))

        result = cur.fetchone()
        conn.commit()
        return result['name']
    finally:
        cur.close()
        conn.close()


def get_template(name: str) -> dict:
    """
    Retrieve a template by name.

    Args:
        name: Template name

    Returns:
        Template data dict with 'name', 'data', 'created_at', 'updated_at'
        or None if not found
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            'SELECT name, data, created_at, updated_at FROM templates WHERE name = %s',
            (name,)
        )
        row = cur.fetchone()

        if not row:
            return None

        return {
            'name': row['name'],
            'data': row['data'],
            'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None
        }
    finally:
        cur.close()
        conn.close()


def list_templates() -> list:
    """
    List all templates.

    Returns:
        List of template summaries (name, created_at, updated_at)
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('SELECT name, created_at, updated_at FROM templates ORDER BY name')
        rows = cur.fetchall()

        return [{
            'name': row['name'],
            'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None
        } for row in rows]
    finally:
        cur.close()
        conn.close()


def delete_template(name: str) -> bool:
    """
    Delete a template by name.

    Args:
        name: Template name to delete

    Returns:
        True if deleted, False if not found
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('DELETE FROM templates WHERE name = %s RETURNING name', (name,))
        deleted = cur.fetchone()
        conn.commit()
        return deleted is not None
    finally:
        cur.close()
        conn.close()


# =============================================================================
# Dashboard / Access Log Operations
# =============================================================================

def get_dashboard_stats() -> dict:
    """
    Get dashboard statistics: total records, last indexed time, and counts by type.

    Returns:
        Dict with 'total', 'last_indexed', and 'by_type' keys
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('''
            SELECT
                COUNT(*) AS total,
                MAX(created_at) AS last_indexed
            FROM records
        ''')
        row = cur.fetchone()

        cur.execute('''
            SELECT record_type, COUNT(*) AS cnt
            FROM records
            GROUP BY record_type
            ORDER BY cnt DESC
        ''')
        by_type = {r['record_type']: r['cnt'] for r in cur.fetchall()}

        return {
            'total': row['total'],
            'last_indexed': row['last_indexed'],
            'by_type': by_type,
        }
    finally:
        cur.close()
        conn.close()


def log_access(username: str = "anonymous"):
    """Insert a row into the portal_access_log table."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            'INSERT INTO portal_access_log (username) VALUES (%s)',
            (username,)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_access_stats() -> dict:
    """
    Get portal access statistics.

    Returns:
        Dict with 'total_visits' and 'last_access' keys
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('''
            SELECT
                COUNT(*) AS total_visits,
                MAX(accessed_at) AS last_access
            FROM portal_access_log
        ''')
        row = cur.fetchone()
        return {
            'total_visits': row['total_visits'],
            'last_access': row['last_access'],
        }
    finally:
        cur.close()
        conn.close()


# =============================================================================
# Vocabulary Cache Operations
# =============================================================================

def save_vocabulary_cache(vocab: dict, synced_by: str = "system") -> bool:
    """
    Replace all vocabulary cache from parsed wiki data and log the sync.

    Args:
        vocab: dict matching vocabulary.json structure {section: {category: {description, values}}}
        synced_by: username who triggered the sync

    Returns:
        True on success
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('DELETE FROM vocabulary_cache')

        sections_count = 0
        categories_count = 0

        for section, categories in vocab.items():
            sections_count += 1
            # Derive wiki_page from section name
            wiki_page = section.replace(" ", "-") if section != "Record Info" else "Record-Overview"
            for category, data in categories.items():
                categories_count += 1
                cur.execute('''
                    INSERT INTO vocabulary_cache (section, category, description, terms, wiki_page)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (
                    section,
                    category,
                    data.get('description', ''),
                    json.dumps(data.get('values', [])),
                    wiki_page
                ))

        # Log the sync
        cur.execute('''
            INSERT INTO vocabulary_sync_log (synced_by, sections_count, categories_count, status)
            VALUES (%s, %s, %s, 'success')
        ''', (synced_by, sections_count, categories_count))

        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        # Log failed sync
        try:
            cur.execute('''
                INSERT INTO vocabulary_sync_log (synced_by, status, error_message)
                VALUES (%s, 'error', %s)
            ''', (synced_by, str(e)))
            conn.commit()
        except Exception:
            pass
        raise
    finally:
        cur.close()
        conn.close()


def load_vocabulary_cache() -> dict:
    """
    Load vocabulary from the cache table.

    Returns:
        dict matching vocabulary.json structure, or empty dict if no cache
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('SELECT section, category, description, terms FROM vocabulary_cache ORDER BY section, category')
        rows = cur.fetchall()

        if not rows:
            return {}

        vocab = {}
        for row in rows:
            section = row['section']
            category = row['category']
            if section not in vocab:
                vocab[section] = {}
            vocab[section][category] = {
                'description': row['description'] or '',
                'values': row['terms'] if isinstance(row['terms'], list) else json.loads(row['terms'])
            }
        return vocab
    finally:
        cur.close()
        conn.close()


def get_last_sync() -> dict:
    """
    Get the most recent sync log entry.

    Returns:
        dict with sync info or None if never synced
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('''
            SELECT synced_at, synced_by, sections_count, categories_count, status, error_message
            FROM vocabulary_sync_log
            ORDER BY synced_at DESC
            LIMIT 1
        ''')
        row = cur.fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        cur.close()
        conn.close()


# =============================================================================
# Vocabulary Proposal Operations
# =============================================================================

def create_proposal(proposal_type: str, section: str, category: str = None,
                    term: str = None, description: str = "", proposed_by: str = "anonymous") -> int:
    """
    Create a vocabulary change proposal.

    Args:
        proposal_type: 'add_term' or 'add_category'
        section: target section
        category: target category (required for add_term, new name for add_category)
        term: new term (for add_term)
        description: description text
        proposed_by: username

    Returns:
        The proposal ID
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('''
            INSERT INTO vocabulary_proposals (proposal_type, section, category, term, description, proposed_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (proposal_type, section, category, term, description, proposed_by))

        proposal_id = cur.fetchone()['id']
        conn.commit()
        return proposal_id
    finally:
        cur.close()
        conn.close()


def list_proposals(status: str = None, proposed_by: str = None) -> list:
    """
    List vocabulary proposals with optional filters.

    Args:
        status: filter by status ('pending', 'approved', 'rejected') or None for all
        proposed_by: filter by proposer username or None for all

    Returns:
        List of proposal dicts
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        query = 'SELECT * FROM vocabulary_proposals WHERE 1=1'
        params = []

        if status:
            query += ' AND status = %s'
            params.append(status)
        if proposed_by:
            query += ' AND proposed_by = %s'
            params.append(proposed_by)

        query += ' ORDER BY proposed_at DESC'
        cur.execute(query, params)

        rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        cur.close()
        conn.close()


def review_proposal(proposal_id: int, status: str, reviewed_by: str, comment: str = "") -> tuple:
    """
    Approve or reject a proposal.

    Args:
        proposal_id: the proposal to review
        status: 'approved' or 'rejected'
        reviewed_by: admin username
        comment: optional review comment

    Returns:
        (success: bool, message: str)
    """
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('SELECT * FROM vocabulary_proposals WHERE id = %s', (proposal_id,))
        proposal = cur.fetchone()

        if not proposal:
            return False, "Proposal not found."

        if proposal['status'] != 'pending':
            return False, f"Proposal already {proposal['status']}."

        cur.execute('''
            UPDATE vocabulary_proposals
            SET status = %s, reviewed_by = %s, reviewed_at = NOW(), review_comment = %s
            WHERE id = %s
        ''', (status, reviewed_by, comment, proposal_id))

        conn.commit()
        return True, f"Proposal {status}."
    finally:
        cur.close()
        conn.close()


def count_pending_proposals() -> int:
    """Return the count of pending vocabulary proposals."""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("SELECT COUNT(*) as count FROM vocabulary_proposals WHERE status = 'pending'")
        row = cur.fetchone()
        return row['count']
    finally:
        cur.close()
        conn.close()
