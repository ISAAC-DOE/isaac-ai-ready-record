"""
ISAAC AI-Ready Record - Database Connection Module
PostgreSQL connection for vocabulary, templates, and records storage
"""

import os
import json
import re
import functools
import logging
import time
from datetime import datetime


def _run_once(fn):
    """Run an idempotent initializer AT MOST ONCE per process — and only latch on
    success (a falsy return retries next call). Streamlit re-executes app.py on
    every rerun and calls init_tables()/init_discovery_tables() each time; this
    module is imported once (never importlib.reload'd), so a module-level latch
    here collapses the per-rerun DDL/init storm — the ~27 CREATE/ALTER/INDEX
    statements — to a single execution per process. THIS is the Streamlit half of
    the summit-scale connection burst."""
    state = {"done": False}

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if state["done"]:
            return True
        result = fn(*args, **kwargs)
        if result:
            state["done"] = True
        return result
    wrapper._once_state = state  # exposed so tests can prove the guard is applied
    return wrapper

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


# Fixed key for the schema-init advisory lock (any stable bigint unique to this app).
_INIT_TABLES_LOCK = 728_141_001


@_run_once
def init_tables():
    """Initialize database tables if they don't exist"""
    if not is_db_configured():
        return False

    try:
        conn = get_db_connection()
        conn.autocommit = False  # real transaction so the xact lock holds until commit
        cur = conn.cursor()
        # Serialize concurrent schema init across pods/replicas (multiple gunicorn
        # masters + the Streamlit process all run this at boot; a rolling deploy overlaps
        # old and new). A TRANSACTION-level advisory lock (auto-released at commit/
        # rollback) — NOT a session-level one, which would not survive pgbouncer
        # transaction pooling. The second booter blocks here, then runs the idempotent
        # DDL below as a fast no-op. Removes the CREATE/ALTER/trigger boot race.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (_INIT_TABLES_LOCK,))

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
        cur.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS content_hash VARCHAR(80)")
        cur.execute("ALTER TABLE record_history ADD COLUMN IF NOT EXISTS version INT")
        cur.execute("ALTER TABLE record_history ADD COLUMN IF NOT EXISTS content_hash VARCHAR(80)")
        cur.execute("ALTER TABLE record_history ADD COLUMN IF NOT EXISTS change_note TEXT")
        cur.execute("ALTER TABLE record_history ADD COLUMN IF NOT EXISTS change_class TEXT")
        # Widen pre-existing CHAR(64) hash columns to hold the versioned form ('v2:<hex>',
        # 67 chars). Idempotent: a no-op once already VARCHAR(80).
        cur.execute("ALTER TABLE records ALTER COLUMN content_hash TYPE VARCHAR(80)")
        cur.execute("ALTER TABLE record_history ALTER COLUMN content_hash TYPE VARCHAR(80)")

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

        # Held records (2026-10-01): a record that passes every hard rule but carries a hold warning
        # (validation.HOLD_CODES) is stored here, private to its owner, until a corrected version is
        # sent. Nothing that reads `records` sees it: not search, not read-only SQL, not the discovery
        # engine, not the record graph. A record_id lives in one table or the other, never both.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS records_held (
                record_id CHAR(26) PRIMARY KEY,
                owner TEXT,
                record_type VARCHAR(50) NOT NULL,
                record_domain VARCHAR(50) NOT NULL,
                data JSONB NOT NULL,
                hold_codes TEXT[] NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_records_held_owner ON records_held (owner)')

        # Record graph (2026-09-30): the keys a record shares with others (study, sample, lab,
        # organization, setup, method) and the links it declares, indexed so each link can be
        # followed from either end. Derived from records.data by portal/record_graph.py and
        # rebuildable from it at any time, so these tables hold nothing the records do not.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS record_keys (
                record_id CHAR(26) PRIMARY KEY,
                derivation TEXT NOT NULL,
                record_version INT,
                content_hash VARCHAR(80),
                study TEXT[] NOT NULL DEFAULT '{}',
                sample_id TEXT,
                lab TEXT,
                organization TEXT,
                setup TEXT,
                method TEXT,
                indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_record_keys_study ON record_keys USING GIN (study)')
        for col in ("sample_id", "lab", "organization", "setup", "method"):
            cur.execute(f'CREATE INDEX IF NOT EXISTS idx_record_keys_{col} ON record_keys ({col})')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS record_links (
                source_id CHAR(26) NOT NULL,
                target_id TEXT NOT NULL,
                rel TEXT NOT NULL,
                basis TEXT,
                PRIMARY KEY (source_id, target_id, rel)
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_record_links_target ON record_links (target_id)')

        conn.commit()
        cur.close()
        conn.close()
        # One-time: stamp content_hash on pre-versioning records (idempotent, no-op after).
        backfill_content_hashes()
        _grant_readonly(("record_keys", "record_links"))
        # Keys and links of every record not yet indexed or changed since (a no-op when current).
        backfill_record_graph()
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


@_run_once
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
        conn.autocommit = False  # real transaction so the xact lock holds until commit
        cur = conn.cursor()
        # Serialize concurrent discovery-schema init across pods (pgbouncer-safe xact
        # lock; a separate DB from records, hence an independent lock space).
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (_INIT_TABLES_LOCK,))
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
        # The trace contract a project was BORN under. NULL = pre-enforcement (legacy):
        # those projects keep working exactly as before. New projects are stamped with
        # the current version and are held to it, so improving the contract never has to
        # be traded against preserving old demos.
        cur.execute("ALTER TABLE hyp_projects ADD COLUMN IF NOT EXISTS policy_version INT")
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
        # WHO reasoned (client-attested model identity) and WHY (the decision record:
        # what was chosen, what was rejected, and on what grounds). Both nullable —
        # every historical event keeps NULL and stays valid on read forever.
        cur.execute("ALTER TABLE hyp_events ADD COLUMN IF NOT EXISTS actor_model JSONB")
        cur.execute("ALTER TABLE hyp_events ADD COLUMN IF NOT EXISTS decision JSONB")
        # policy 62: the falsification threshold as data, and the observation that met it.
        # threshold = {comparator, value, unit} on the prediction; observed = {value, unit,
        # scale, scale_basis} on the evaluation. Server derives margin from the pair.
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS threshold JSONB")
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS observed JSONB")
        # policy 64: what the verdict rests on (cited_record | derived | computed_run |
        # literature | prior_knowledge). Separates contract-addressable reasoning from the
        # model's own knowledge.
        cur.execute("ALTER TABLE hyp_predictions ADD COLUMN IF NOT EXISTS basis TEXT")
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


# An uploader with this many held records gets no more held until they deal with them; clean records
# still publish. A pipeline that ignores every response stops here.
HELD_BACKLOG_LIMIT = 20


class RecordHeldError(Exception):
    """The record passed every hard rule but carries a hold warning (validation.HOLD_CODES). It is
    stored privately in records_held and is published once a corrected version is sent -> HTTP 409,
    reason 'held'."""

    def __init__(self, record_id, hold, warnings):
        super().__init__(record_id)
        self.record_id, self.hold, self.warnings = record_id, list(hold), list(warnings or [])


class HeldBacklogError(Exception):
    """The uploader already has HELD_BACKLOG_LIMIT held records; this one, which would be held too, was
    not stored -> HTTP 409, reason 'held_backlog'."""

    def __init__(self, owner, held):
        super().__init__(owner)
        self.owner, self.held = owner, held


class EditHeldError(Exception):
    """An edit of a published record would introduce a hold warning the record does not carry now. The
    edit is not applied and the published version stays as it is -> HTTP 409, reason 'edit_held'."""

    def __init__(self, record_id, hold, warnings):
        super().__init__(record_id)
        self.record_id, self.hold, self.warnings = record_id, list(hold), list(warnings or [])


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

    hold, warnings = [], []
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
        hold, warnings = list(result.get("hold") or []), list(result.get("warnings") or [])

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
            # A record carrying a hold warning goes to records_held instead, private to its owner;
            # the same owner sending the same id again replaces the draft, and a clean version
            # publishes it. The id is locked for the transaction, so it lands in one table only.
            owner = (record_data.get("attribution") or {}).get("uploaded_by")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (record_id,))
            cur.execute("SELECT owner FROM records_held WHERE record_id = %s", (record_id,))
            held_row = cur.fetchone()
            if held_row is not None and held_row["owner"] != owner:
                conn.rollback()
                raise RecordExistsError(record_id)
            if hold:
                cur.execute("SELECT 1 FROM records WHERE record_id = %s", (record_id,))
                if cur.fetchone() is not None:
                    conn.rollback()
                    raise RecordExistsError(record_id)
                if held_row is None:
                    cur.execute("SELECT COUNT(*) AS n FROM records_held WHERE owner IS NOT DISTINCT FROM %s",
                                (owner,))
                    held_now = cur.fetchone()["n"]
                    if held_now >= HELD_BACKLOG_LIMIT:
                        conn.rollback()
                        raise HeldBacklogError(owner, held_now)
                cur.execute('''
                    INSERT INTO records_held (record_id, owner, record_type, record_domain, data, hold_codes)
                    VALUES (%s, %s, %s, %s, %s, %s::text[])
                    ON CONFLICT (record_id) DO UPDATE SET
                        record_type = EXCLUDED.record_type, record_domain = EXCLUDED.record_domain,
                        data = EXCLUDED.data, hold_codes = EXCLUDED.hold_codes, updated_at = NOW()
                ''', (record_id, owner, record_type, record_domain, json.dumps(record_data), hold))
                conn.commit()
                raise RecordHeldError(record_id, hold, warnings)
            if held_row is not None:
                cur.execute("DELETE FROM records_held WHERE record_id = %s", (record_id,))
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
            _index_after_commit(conn, record_id.strip())
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
        _index_after_commit(conn, result['record_id'].strip())
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
                            action: str = "update", allow_hold: bool = False) -> dict:
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

        # An edit may not bring in a hold warning the published record does not already carry: the
        # published version stays as it is (EditHeldError -> 409). Fixing a record never trips this.
        if not allow_hold and result.get("hold"):
            added = sorted(set(result["hold"]) - set(validation.validate_record_full(prior_data).get("hold") or []))
            if added:
                conn.rollback()
                raise EditHeldError(record_id, added, result.get("warnings"))

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
        _index_after_commit(conn, record_id)
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
        _index_after_commit(conn, record_id)
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


def records_editable_by(identity: str) -> list:
    """Full records the identity may edit: those it submitted and those it co-authors."""
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute(
            "SELECT data FROM records WHERE data->'attribution'->>'uploaded_by' = %s "
            "OR record_id IN (SELECT record_id FROM record_acl WHERE grantee_identity = %s) "
            "ORDER BY created_at DESC", (identity, identity))
        out = []
        for row in cur.fetchall():
            rec = row["data"]
            out.append(json.loads(rec) if isinstance(rec, str) else rec)
        return out
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
    """Idempotent: (re)stamp content_hash on records that are NULL or computed by an OLDER
    hash-algorithm version, so drift detection works over the whole corpus. Migrates the
    existing corpus to the current version ('v2:...') in one pass; re-runs are no-ops once
    every row is current. Exception-safe — never blocks startup."""
    import record_provenance as _rp
    done = 0
    try:
        conn = get_db_connection(); cur = conn.cursor()
        try:
            stale = f"{_rp._HASH_VERSION}:%"  # rows NOT matching this are NULL/legacy/older-algo
            cur.execute("SELECT record_id FROM records "
                        "WHERE content_hash IS NULL OR content_hash NOT LIKE %s LIMIT %s",
                        (stale, max_rows))
            for rid in [r["record_id"] for r in cur.fetchall()]:
                try:
                    # Re-read the row UNDER LOCK and hash the CURRENT data, not the scan-time
                    # snapshot: during a rolling deploy an old (v1) pod could edit the row
                    # between the scan and the update, and stamping a stale hash would leave
                    # content_hash describing the wrong data. FOR UPDATE serializes against
                    # update_record_versioned's FOR UPDATE; the re-checked WHERE also skips a
                    # row a concurrent writer already migrated.
                    cur.execute("SELECT data FROM records WHERE record_id=%s "
                                "AND (content_hash IS NULL OR content_hash NOT LIKE %s) FOR UPDATE",
                                (rid, stale))
                    locked = cur.fetchone()
                    if locked is None:
                        continue
                    h = _rp.content_hash(locked["data"] or {})
                    cur.execute("UPDATE records SET content_hash=%s WHERE record_id=%s", (h, rid))
                    done += 1
                except Exception:
                    logger.exception("hash backfill failed for %s", rid)
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
        reaction                      -> JSONB context.reaction.name (falls back to the deprecated
                                         context.electrochemistry.reaction)
        material_contains             -> ILIKE on sample.material.name
        created_after, created_before -> created_at range (ISO 8601)
        study, sample_id, lab,        -> derived keys (record_keys); study matches any of the
        organization, setup, method      record's studies

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
        where.append("COALESCE(data->'context'->'reaction'->>'name', data->'context'->'electrochemistry'->>'reaction') = %s")
        params.append(filters['reaction'])
    if filters.get('material_contains'):
        where.append("data->'sample'->'material'->>'name' ILIKE %s")
        params.append(f"%{filters['material_contains']}%")
    if filters.get('created_after'):
        where.append('created_at >= %s'); params.append(filters['created_after'])
    if filters.get('created_before'):
        where.append('created_at <= %s'); params.append(filters['created_before'])
    # Keys derived by portal/record_graph.py; values arrive already normalized (key_from_param).
    if filters.get('study'):
        where.append('record_id IN (SELECT record_id FROM record_keys WHERE study @> ARRAY[%s]::text[])')
        params.append(filters['study'])
    for col in _KEY_COLUMNS:
        value = filters.get(col)
        if not value:
            continue
        if col == 'sample_id' and value.startswith('sample:*/'):
            # a local sample_id asked for as written: every lab's object of that name
            local = value[len('sample:*/'):].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where.append("record_id IN (SELECT record_id FROM record_keys WHERE sample_id LIKE %s)")
            params.append('sample:%/' + local)
            continue
        where.append(f'record_id IN (SELECT record_id FROM record_keys WHERE {col} = %s)')
        params.append(value)
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
        if deleted is not None:
            _index_after_commit(conn, record_id)
        return deleted is not None
    finally:
        cur.close()
        conn.close()


def existing_record_ids(record_ids, owner=None) -> set:
    """The ids among record_ids that are published, or held by this owner (a link may name a draft of
    one's own)."""
    ids = sorted({str(i).strip() for i in record_ids or () if str(i or "").strip()})
    if not ids:
        return set()
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT record_id FROM records WHERE record_id = ANY(%s) UNION "
                    "SELECT record_id FROM records_held WHERE record_id = ANY(%s) AND owner IS NOT DISTINCT FROM %s",
                    (ids, ids, owner))
        return {str(row["record_id"]).strip() for row in cur.fetchall()}
    finally:
        cur.close()
        conn.close()


def get_held_record(record_id: str):
    """{owner, data, hold_codes, created_at, updated_at} of a held record, or None."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT owner, data, hold_codes, created_at, updated_at FROM records_held WHERE record_id = %s",
                    (record_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()


def list_held(owner: str | None, limit: int = 100, offset: int = 0) -> tuple:
    """(rows, total) of the held records of one owner (None: every owner, for admins), newest first."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        where, params = ("WHERE owner = %s", [owner]) if owner is not None else ("", [])
        cur.execute(f"SELECT COUNT(*) AS n FROM records_held {where}", params)
        total = cur.fetchone()["n"]
        cur.execute(f"SELECT record_id, owner, record_domain, hold_codes, created_at, updated_at FROM records_held "
                    f"{where} ORDER BY updated_at DESC, record_id LIMIT %s OFFSET %s", params + [limit, offset])
        rows = []
        for row in cur.fetchall():
            rows.append({"record_id": row["record_id"].strip(), "owner": row["owner"],
                         "record_domain": row["record_domain"], "hold": list(row["hold_codes"] or []),
                         "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
                         "updated_at": row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else row["updated_at"]})
        return rows, total
    finally:
        cur.close()
        conn.close()


def delete_held(record_id: str) -> bool:
    """Discard a held record. It was never public, so nothing else refers to it."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM records_held WHERE record_id = %s RETURNING record_id", (record_id,))
        gone = cur.fetchone() is not None
        conn.commit()
        return gone
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


# =============================================================================
# Record graph: derived keys and the two-way link index (portal/record_graph.py)
# =============================================================================
# record_keys columns a list filter reads by equality (study, an array, is matched apart).
_KEY_COLUMNS = ("sample_id", "lab", "organization", "setup", "method")
_GRAPH_BACKFILL_LOCK = 728_141_002
_GRAPH_REFRESH_SECONDS = 600
_graph_refreshed_at = {"t": None}
_SAMPLE_CLUSTER_CAP = 5000
_STALE_KEYS = ("k.record_id IS NULL OR k.derivation IS DISTINCT FROM %s "
               "OR k.record_version IS DISTINCT FROM r.version "
               "OR k.content_hash IS DISTINCT FROM r.content_hash")


def _grant_readonly(tables) -> None:
    """Let the read-only SQL role (PGUSER_RO) read public tables this code creates. Skipped
    without a read-only role; a failure is logged (the records-DB owner then grants by hand)
    and never blocks startup."""
    role = os.environ.get('PGUSER_RO')
    if not role:
        return
    try:
        from psycopg2 import sql as _sql
        conn = get_db_connection(); cur = conn.cursor()
        try:
            cur.execute(_sql.SQL("GRANT SELECT ON {} TO {}").format(
                _sql.SQL(", ").join(_sql.Identifier(t) for t in tables), _sql.Identifier(role)))
            conn.commit()
        finally:
            cur.close(); conn.close()
    except Exception:
        logger.exception("GRANT SELECT on %s to the read-only role failed", ", ".join(tables))


def _write_graph(cur, record_id: str, data, version, content_hash) -> None:
    """Replace one record's keys and declared links with those derived from `data`. Callers read
    `data` under FOR SHARE in the same transaction, so it is the record as committed and no edit
    can land before the index is written."""
    import record_graph as rg
    data = data if isinstance(data, dict) else {}
    keys = rg.derive_keys(data)
    cur.execute('''
        INSERT INTO record_keys (record_id, derivation, record_version, content_hash, study,
                                 sample_id, lab, organization, setup, method, indexed_at)
        VALUES (%s, %s, %s, %s, %s::text[], %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (record_id) DO UPDATE SET
            derivation = EXCLUDED.derivation, record_version = EXCLUDED.record_version,
            content_hash = EXCLUDED.content_hash, study = EXCLUDED.study,
            sample_id = EXCLUDED.sample_id, lab = EXCLUDED.lab,
            organization = EXCLUDED.organization, setup = EXCLUDED.setup,
            method = EXCLUDED.method, indexed_at = NOW()
    ''', (record_id, rg.derivation_id(), version, content_hash, keys["study"], keys["sample_id"],
          keys["lab"], keys["organization"], keys["setup"], keys["method"]))
    cur.execute("DELETE FROM record_links WHERE source_id = %s", (record_id,))
    edges = rg.link_edges(record_id, data)
    if edges:
        cur.execute("INSERT INTO record_links (source_id, target_id, rel, basis) VALUES "
                    + ", ".join(["(%s, %s, %s, %s)"] * len(edges)) + " ON CONFLICT DO NOTHING",
                    [v for target, rel, basis in edges for v in (record_id, target, rel, basis)])


def _index_after_commit(conn, record_id: str) -> None:
    """Refresh one record's keys and links on the connection whose write just committed.
    Best-effort: a failure is logged and rolled back, never raised, so indexing can never fail
    an upload, and the startup backfill re-indexes whatever this missed. FOR SHARE holds the
    row until the index is written, so an edit landing meanwhile waits and is indexed after."""
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT data, version, content_hash FROM records WHERE record_id = %s FOR SHARE",
                    (record_id,))
        row = cur.fetchone()
        if row is None:
            cur.execute("DELETE FROM record_keys WHERE record_id = %s", (record_id,))
            cur.execute("DELETE FROM record_links WHERE source_id = %s", (record_id,))
        else:
            _write_graph(cur, record_id, row["data"], row["version"], row["content_hash"])
        conn.commit()
    except Exception:
        logger.exception("record graph: indexing %s failed; the startup backfill retries it", record_id)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass


def backfill_record_graph(batch: int = 500) -> int:
    """Index every record whose keys are missing or stale: new, edited since (version or content
    hash moved), or derived under an older rule or vocabulary. Drops the keys and declared links
    of deleted records. Idempotent and exception-safe, so it never blocks startup. Works in
    batches, one transaction each, reading each batch under FOR SHARE (an edit to one of its
    records waits for that batch only); a try-lock taken per transaction keeps it to one
    process at a time, and the others skip. Returns how many records it indexed."""
    import record_graph as rg
    done = 0
    try:
        conn = get_db_connection(); cur = conn.cursor()
        try:
            def locked():
                cur.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (_GRAPH_BACKFILL_LOCK,))
                return cur.fetchone()["ok"]

            if not locked():
                conn.rollback()
                return 0
            cur.execute("DELETE FROM record_keys k WHERE NOT EXISTS "
                        "(SELECT 1 FROM records r WHERE r.record_id = k.record_id)")
            cur.execute("DELETE FROM record_links l WHERE NOT EXISTS "
                        "(SELECT 1 FROM records r WHERE r.record_id = l.source_id)")
            cur.execute("SELECT r.record_id FROM records r LEFT JOIN record_keys k "
                        f"ON k.record_id = r.record_id WHERE {_STALE_KEYS}", (rg.derivation_id(),))
            stale = [row["record_id"].strip() for row in cur.fetchall()]
            conn.commit()
            for i in range(0, len(stale), batch):
                if not locked():
                    conn.rollback()
                    break
                cur.execute("SELECT record_id, data, version, content_hash FROM records "
                            "WHERE record_id = ANY(%s) FOR SHARE", (stale[i:i + batch],))
                for row in cur.fetchall():
                    rid = row["record_id"].strip()
                    cur.execute("SAVEPOINT graph_row")
                    try:
                        _write_graph(cur, rid, row["data"], row["version"], row["content_hash"])
                        cur.execute("RELEASE SAVEPOINT graph_row")
                        done += 1
                    except Exception:
                        cur.execute("ROLLBACK TO SAVEPOINT graph_row")
                        logger.exception("record graph: indexing %s failed", rid)
                conn.commit()
        finally:
            cur.close(); conn.close()
        if done:
            logger.info("record graph: indexed %d record(s)", done)
    except Exception:
        logger.exception("record graph backfill aborted")
    return done


def refresh_record_graph_if_due() -> None:
    """Run the backfill at most every ten minutes per process, before a query that reads the
    index, so a record whose hook failed, or that an older release wrote during a rollout, is
    found without waiting for a restart. A no-op scan when the index is current."""
    now = time.monotonic()
    if _graph_refreshed_at["t"] is not None and now - _graph_refreshed_at["t"] < _GRAPH_REFRESH_SECONDS:
        return
    _graph_refreshed_at["t"] = now
    backfill_record_graph()


def _ensure_indexed(conn, record_id: str) -> None:
    """Index the record now if its keys are missing or stale, so a query about it is answered
    from its current content even before the next backfill."""
    import record_graph as rg
    cur = conn.cursor()
    try:
        cur.execute("SELECT (" + _STALE_KEYS + ") AS stale FROM records r LEFT JOIN record_keys k "
                    "ON k.record_id = r.record_id WHERE r.record_id = %s", (rg.derivation_id(), record_id))
        row = cur.fetchone()
    finally:
        cur.close()
    if row is not None and row["stale"]:
        _index_after_commit(conn, record_id)


class GroupTooLargeError(Exception):
    """A linked group reached _SAMPLE_CLUSTER_CAP records, so its full membership (and its first
    record) is unknown."""


def _linked_group(cur, record_id: str, rel: str, share_sample_id: bool = False,
                  strict: bool = False) -> set:
    """The records joined to this one by `rel` links, whichever record declared each link and
    through any number of other records; with share_sample_id, also every record stating the
    same sample.sample_id (and their links in turn). Stops at _SAMPLE_CLUSTER_CAP records; with
    strict it raises there instead, because a truncated group named from different starting
    records could get different names."""
    seen, frontier = {record_id}, [record_id]
    while frontier:
        if len(seen) >= _SAMPLE_CLUSTER_CAP:
            if strict:
                raise GroupTooLargeError(f"{rel} group of {record_id} exceeds {_SAMPLE_CLUSTER_CAP} records")
            break
        sql = ("SELECT target_id AS id FROM record_links WHERE rel = %s AND source_id = ANY(%s) "
               "UNION SELECT source_id FROM record_links WHERE rel = %s AND target_id = ANY(%s)")
        params = [rel, frontier, rel, frontier]
        if share_sample_id:
            sql += (" UNION SELECT k2.record_id FROM record_keys k1 JOIN record_keys k2 "
                    "ON k2.sample_id = k1.sample_id WHERE k1.record_id = ANY(%s) AND k1.sample_id IS NOT NULL")
            params.append(frontier)
        cur.execute(sql, params)
        found = {str(row["id"]).strip() for row in cur.fetchall()} - seen
        seen |= found
        frontier = sorted(found)
    return seen


def _sample_members(cur, record_id: str) -> set:
    """The records of the same physical sample: a shared sample.sample_id, or a same_sample_as
    link declared by either record, followed transitively."""
    return _linked_group(cur, record_id, "same_sample_as", share_sample_id=True)


def _sample_namespace(key: str) -> str:
    """Who issued a derived sample key: the scope of a local id ('ror:<id>', 'org:<name>',
    'group:<name>'), or 'global' for an id unique by its form. An issuer names one object once,
    so two different ids from one issuer in one group contradict each other; ids from different
    issuers are the same object's names in different labs."""
    body = str(key or "")[len("sample:"):]
    scope, sep, _ = body.partition("/")
    return scope if sep and scope.startswith(("ror:", "org:", "group:")) else "global"


def sample_groups(record_ids) -> dict:
    """For each record id, what the discovery engine needs to know about shared specimens:
      sample     - the record's sample group, named by its lowest record_id (its first-created
                   record): every record joined to it by same_sample_as links read from both
                   ends and through other records, or by a shared sample.sample_id. None when
                   the record is alone.
      replicas   - the records it is joined to by one replica_of link, declared by either side.
                   Replication is pairwise: sibling replicates of one record are not joined.
      unresolved - why the sample group could not be named, or None: 'conflicting_sample_ids'
                   when the group holds two different sample_ids from one issuer (two globally
                   unique ids, or two local ids of one organization or group): a link or an id
                   is wrong, and merging would erase the difference. 'too_large' past
                   _SAMPLE_CLUSTER_CAP records.
    Raises when the index cannot be read, so a caller never reads a failure as independence."""
    ids = sorted({str(r).strip() for r in record_ids if str(r or "").strip()})
    out = {rid: {"sample": None, "replicas": [], "unresolved": None} for rid in ids}
    if not ids:
        return out
    refresh_record_graph_if_due()
    conn = get_db_connection()
    try:
        for rid in ids:
            _ensure_indexed(conn, rid)
        cur = conn.cursor()
        try:
            named = {}   # member -> (name, unresolved) for every group already resolved
            for rid in ids:
                if rid not in named:
                    try:
                        group = _linked_group(cur, rid, "same_sample_as", True, strict=True)
                    except GroupTooLargeError:
                        named[rid] = (None, "too_large")
                    else:
                        cur.execute("SELECT DISTINCT sample_id FROM record_keys "
                                    "WHERE record_id = ANY(%s) AND sample_id IS NOT NULL", (sorted(group),))
                        issuers = {}
                        for row in cur.fetchall():
                            issuers.setdefault(_sample_namespace(row["sample_id"]), set()).add(row["sample_id"])
                        verdict = ((None, "conflicting_sample_ids") if any(len(v) > 1 for v in issuers.values())
                                   else (min(group) if len(group) > 1 else None, None))
                        named.update({member: verdict for member in group})
                out[rid]["sample"], out[rid]["unresolved"] = named[rid]
            cur.execute("SELECT source_id::text AS a, target_id AS b FROM record_links "
                        "WHERE rel = 'replica_of' AND (source_id = ANY(%s) OR target_id = ANY(%s))",
                        (ids, ids))
            for row in cur.fetchall():
                a, b = row["a"].strip(), row["b"].strip()
                for one, other in ((a, b), (b, a)):
                    if one in out and other != one and other not in out[one]["replicas"]:
                        out[one]["replicas"].append(other)
            for entry in out.values():
                entry["replicas"].sort()
            conn.rollback()
        finally:
            cur.close()
    finally:
        conn.close()
    return out


def _cluster_condition(cur, record_id: str, dimension: str, keys_row):
    """(keys, SQL condition over records r / record_keys k, params) selecting the record's cluster
    in one dimension, or (keys, None, None) when it has none there."""
    import record_graph as rg
    keys_row = keys_row or {}
    if dimension == "study":
        keys = list(keys_row.get("study") or [])
        return (keys, "k.study && %s::text[]", [keys]) if keys else (keys, None, None)
    column = rg.DIMENSIONS[dimension]
    keys = [keys_row[column]] if keys_row.get(column) else []
    if dimension == "sample":
        members = _sample_members(cur, record_id)
        if len(members) > 1 or keys:
            return keys, "r.record_id = ANY(%s)", [sorted(members)]
        return keys, None, None
    return (keys, f"k.{column} = %s", keys) if keys else (keys, None, None)


def record_clusters(record_id: str, by: str | None = None, limit: int = 100, offset: int = 0):
    """The record's clusters. Without `by`: every dimension's key(s) and cluster size (the
    record itself included; 0 when it has no cluster there). With `by`: that cluster's members,
    ordered by record_id, paged. None when the record does not exist."""
    import record_graph as rg
    conn = get_db_connection()
    try:
        _ensure_indexed(conn, record_id)
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1 FROM records WHERE record_id = %s", (record_id,))
            if cur.fetchone() is None:
                return None
            cur.execute("SELECT * FROM record_keys WHERE record_id = %s", (record_id,))
            keys_row = cur.fetchone()
            base = "FROM records r LEFT JOIN record_keys k ON k.record_id = r.record_id WHERE "
            if by is None:
                clusters = {}
                for dimension in rg.DIMENSIONS:
                    keys, cond, params = _cluster_condition(cur, record_id, dimension, keys_row)
                    count = 0
                    if cond:
                        cur.execute("SELECT COUNT(*) AS n " + base + cond, params)
                        count = cur.fetchone()["n"]
                    clusters[dimension] = {"keys": keys, "count": count}
                conn.rollback()
                return {"record_id": record_id, "clusters": clusters}
            keys, cond, params = _cluster_condition(cur, record_id, by, keys_row)
            members, count = [], 0
            if cond:
                cur.execute("SELECT COUNT(*) AS n " + base + cond, params)
                count = cur.fetchone()["n"]
                cur.execute("SELECT r.record_id, r.record_type, r.record_domain, "
                            "r.data->'sample'->'material'->>'name' AS material, r.created_at "
                            + base + cond + " ORDER BY r.record_id LIMIT %s OFFSET %s",
                            params + [limit, offset])
                for row in cur.fetchall():
                    created = row["created_at"]
                    members.append({"record_id": row["record_id"].strip(), "record_type": row["record_type"],
                                    "record_domain": row["record_domain"], "material": row["material"],
                                    "created_at": created.isoformat() if hasattr(created, "isoformat") else created})
            conn.rollback()
            return {"record_id": record_id, "by": by, "keys": keys, "count": count,
                    "limit": limit, "offset": offset, "records": members}
        finally:
            cur.close()
    finally:
        conn.close()


def record_neighbors(record_id: str, direction: str = "both", rel: str | None = None,
                     limit: int = 200, offset: int = 0):
    """The records this one links to (declared 'out') and the records that link to it
    (declared 'in'), one entry per neighbor and relation ('both' when each declared it), with
    whether the neighbor exists. None when the record does not exist."""
    import record_graph as rg
    conn = get_db_connection()
    try:
        _ensure_indexed(conn, record_id)
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1 FROM records WHERE record_id = %s", (record_id,))
            if cur.fetchone() is None:
                return None
            parts, params = [], []
            rel_sql = " AND rel = %s" if rel else ""
            if direction in ("out", "both"):
                parts.append("SELECT target_id AS neighbor, rel, basis, 'out' AS declared "
                             "FROM record_links WHERE source_id = %s" + rel_sql)
                params += [record_id] + ([rel] if rel else [])
            if direction in ("in", "both"):
                parts.append("SELECT source_id::text AS neighbor, rel, basis, 'in' AS declared "
                             "FROM record_links WHERE target_id = %s" + rel_sql)
                params += [record_id] + ([rel] if rel else [])
            cur.execute("SELECT e.neighbor, e.rel, e.basis, e.declared, (r.record_id IS NOT NULL) AS present "
                        "FROM (" + " UNION ALL ".join(parts) + ") e "
                        "LEFT JOIN records r ON r.record_id = e.neighbor "
                        "ORDER BY e.rel, e.neighbor, e.declared DESC", params)
            merged = {}
            for row in cur.fetchall():
                key = (row["neighbor"].strip(), row["rel"])
                entry = merged.get(key)
                if entry is None:
                    merged[key] = {"record_id": key[0], "rel": row["rel"], "declared": row["declared"],
                                   "symmetric": row["rel"] in rg.SYMMETRIC_RELATIONS,
                                   "basis": row["basis"], "exists": bool(row["present"])}
                elif entry["declared"] != row["declared"]:
                    entry["declared"] = "both"
            conn.rollback()
            neighbors = list(merged.values())
            return {"record_id": record_id, "direction": direction, "rel": rel, "count": len(neighbors),
                    "limit": limit, "offset": offset, "neighbors": neighbors[offset:offset + limit]}
        finally:
            cur.close()
    finally:
        conn.close()


# SENSITIVE records-DB tables: readable ONLY by admins via /records/query (and never by
# nano-ISAAC). The NON-sensitive reference/scientific data open read-only to ANY authenticated
# user is `records`, `templates` (form scaffolding), and `vocabulary_cache` (the controlled
# ontology). Everything in the tuple below is admin-only. Sensitivity rationale: these carry
# login/usage PII (incl. client IPs), access-control / moderation identities, OR — for
# record_history — the editor identity (`actor`) plus full JSONB snapshots of archived,
# superseded, and DELETED record versions, i.e. an audit log, not public science.
#   The TRUE enforcement is the isaac_readonly role's grants (DB level); this is the in-code
#   belt. KEEP THE TWO IN SYNC — when opening a table here, the records-DB owner must GRANT
#   SELECT on it to isaac_readonly (and keep REVOKE on the sensitive tables below).
_AGENT_FORBIDDEN_TABLES = (
    "api_requests",          # usage log — usernames, endpoints, client IPs
    "portal_access_log",     # login activity — usernames, timestamps
    "vocabulary_sync_log",   # operational sync log
    "vocabulary_proposals",  # proposer/reviewer identities + moderation state
    "record_acl",            # who-can-edit-what (access-control / collaboration graph)
    "record_history",        # audit log: editor identity (actor) + archived/deleted snapshots
    "records_held",          # unpublished drafts, private to their owner until fixed
)

# The records-DB tables OPEN to any authenticated user via /records/query. Together
# with _AGENT_FORBIDDEN_TABLES (admin-only), this must cover EVERY table init_tables
# creates — a test introspects the DDL and fails on any UNCLASSIFIED table, so a new
# table can never silently ship readable-by-default. (The isaac_readonly DB GRANT is
# the real gate; this keeps the in-code belt honest as the schema grows.)
_AGENT_PUBLIC_TABLES = ("records", "templates", "vocabulary_cache", "record_keys", "record_links")


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
    - The row cap is enforced by a server-side cursor + fetchmany(max_rows) — it
      cannot be defeated by a `LIMIT` substring in a column name or a trailing
      comment (the old string-append bug), and it bounds set-returning queries
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
    max_rows = max(1, min(int(max_rows), 500))  # floor at 1, hard cap 500 (fetchmany size)
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

    # The row cap is enforced by a SERVER-SIDE cursor + fetchmany(max_rows) below,
    # NOT by appending a LIMIT to the SQL text. The old string-append was bypassable
    # — a `LIMIT` substring in a column name (the real descriptor
    # `limiting_current_density`!) skipped it, and a trailing `--` comment ate it —
    # and fetchall() then buffered the whole result. A streaming named cursor makes
    # the cap uncircumventable for streaming plans (seq scans, target-list
    # set-returning fns) — we fetch at most max_rows then close, so Postgres never
    # materializes the rest. Blocking plans (aggregates, unindexed ORDER BY,
    # FROM-clause SRFs) still run to completion, bounded by statement_timeout (as
    # they were under the old LIMIT). The whole DECLARE->FETCH->CLOSE->ROLLBACK
    # stays in ONE transaction, so it is safe under pgbouncer transaction pooling.

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
        qcur = conn.cursor(name="isaac_ro_query", cursor_factory=RealDictCursor)
        try:
            qcur.execute(stripped)
            rows = qcur.fetchmany(max_rows)  # hard cap — cannot be defeated by the SQL text
            qcur.close()
        except Exception as qe:
            conn.rollback()
            pgcode = getattr(qe, "pgcode", None)
            # 42501 = insufficient_privilege: read-only role lacks SELECT on a table that IS
            # allowed by the in-code belt but not yet GRANTed to isaac_readonly at the DB.
            if pgcode == "42501":
                raise ValueError(
                    "Read access to that table is not enabled. The `records`, `record_keys`, "
                    "`record_links`, `templates` and `vocabulary_cache` tables are open to all "
                    "authenticated users; other tables are admin-only — ask an admin.")
            # Any other Postgres error (bad column/function, syntax, timeout) is the USER's
            # query — surface the first line of the DB message as a 400, not a 500. It's about
            # their own SELECT over the public records schema, so nothing sensitive leaks.
            if pgcode:
                _msg = (getattr(qe, "pgerror", None) or str(qe) or "query error").strip()
                raise ValueError(f"SQL error: {_msg.splitlines()[0]}")
            raise  # connection/unexpected -> genuine 500
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
