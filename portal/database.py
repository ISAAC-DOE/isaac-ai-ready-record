"""
ISAAC AI-Ready Record - Database Connection Module
PostgreSQL connection for vocabulary, templates, and records storage
"""

import os
import json
import re
import logging
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("isaac-database")


def get_db_connection():
    """Create a database connection using environment variables"""
    return psycopg2.connect(
        host=os.environ.get('PGHOST', 'localhost'),
        port=os.environ.get('PGPORT', '5432'),
        database=os.environ.get('PGDATABASE', 'app'),
        user=os.environ.get('PGUSER', 'postgres'),
        password=os.environ.get('PGPASSWORD', ''),
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
        cur.execute('CREATE INDEX IF NOT EXISTS idx_records_data_gin ON records USING GIN (data)')

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
        return True
    except Exception as e:
        print(f"Error initializing tables: {e}")
        return False


# =============================================================================
# Record Operations
# =============================================================================

def save_record(record_data: dict, *, skip_validation: bool = False, uploaded_by: str | None = None) -> str:
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

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute('''
            INSERT INTO records (record_id, record_type, record_domain, data)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (record_id) DO UPDATE SET
                record_type = EXCLUDED.record_type,
                record_domain = EXCLUDED.record_domain,
                data = EXCLUDED.data
            RETURNING record_id
        ''', (record_id, record_type, record_domain, json.dumps(record_data)))

        result = cur.fetchone()
        conn.commit()
        return result['record_id'].strip()
    finally:
        cur.close()
        conn.close()


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


def log_api_request(username, method, endpoint, status, duration_ms):
    """Fire-and-forget API usage logging. MUST never break a request."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO api_requests (username, method, endpoint, status, duration_ms) "
            "VALUES (%s, %s, %s, %s, %s)",
            (username, method, endpoint, status, duration_ms))
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
        return {'days': days, 'total_requests': row['total'], 'distinct_users': row['users'],
                'rejection_count': row['rejections'], 'server_error_count': row['server_errors'],
                'daily': daily, 'by_user': by_user,
                'by_endpoint': by_endpoint}
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


def delete_record(record_id: str) -> bool:
    """
    Delete a record by its ID.

    Args:
        record_id: The record identifier to delete

    Returns:
        True if deleted, False if not found
    """
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


def execute_readonly_query(sql: str, max_rows: int = 50, timeout_ms: int = 5000) -> list:
    """
    Execute a read-only SQL query against the database.

    Security:
    - Only SELECT and WITH (CTE) statements are allowed
    - Mutation keywords (INSERT, UPDATE, DELETE, DROP, ALTER, etc.) are rejected
    - A LIMIT clause is enforced (appended if missing)
    - A statement timeout is set to prevent long-running queries

    Args:
        sql: The SQL query string (must be SELECT or WITH)
        max_rows: Maximum rows to return (default 50)
        timeout_ms: Statement timeout in milliseconds (default 5000)

    Returns:
        List of row dicts from the query result

    Raises:
        ValueError: If the query is not a safe read-only SELECT/WITH
    """
    stripped = sql.strip().rstrip(";")
    upper = stripped.upper()

    # Must start with SELECT or WITH
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        raise ValueError("Only SELECT or WITH (CTE) queries are allowed.")

    # Reject mutation keywords anywhere in the query
    forbidden = r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|COPY|EXECUTE|CALL)\b'
    if re.search(forbidden, upper):
        raise ValueError("Query contains forbidden mutation keywords.")

    # Enforce LIMIT if not present
    if "LIMIT" not in upper:
        stripped += f" LIMIT {max_rows}"

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(f"SET LOCAL statement_timeout = '{timeout_ms}'")
        cur.execute(stripped)
        rows = cur.fetchall()
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
