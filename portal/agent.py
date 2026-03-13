"""
nano ISAAC - AI Chat Agent for querying the ISAAC database.

ReAct-style loop: user question -> LLM generates SQL -> execute read-only
-> feed results back to LLM -> LLM summarises -> user.
"""

import json
import os
import re

import requests as http_requests

import database
import ontology

# Stanford AI API Gateway (same config as ontology.py)
LLM_API_URL = "https://aiapi-prod.stanford.edu/v1/chat/completions"
LLM_MODEL = "gemini-2.5-pro"

MAX_TOOL_ROUNDS = 3
RESULT_TRUNCATION_BYTES = 8192

SYSTEM_PROMPT = """\
You are **nano ISAAC**, a helpful AI assistant for querying the ISAAC \
AI-Ready Record database. You answer researchers' questions about the \
scientific records stored in PostgreSQL.

## Database schema

```sql
CREATE TABLE records (
    id          INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    record_id   CHAR(26) UNIQUE NOT NULL,   -- ULID
    record_type VARCHAR(50) NOT NULL,       -- evidence | intent | synthesis
    record_domain VARCHAR(50) NOT NULL,     -- characterization | performance | simulation | theory | derived
    data        JSONB NOT NULL,             -- full ISAAC record
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
-- GIN index on data column for fast JSONB queries
CREATE INDEX idx_records_data_gin ON records USING GIN (data);
```

## JSONB structure inside `data`

Each record's `data` column is a JSON object with these top-level keys:
- `isaac_record_version` (string "1.0")
- `record_id`, `record_type`, `record_domain`, `source_type`
- `timestamps` → `{created_utc, acquired_start_utc, acquired_end_utc}`
- `sample` → `{material: {name, formula, provenance}, sample_form, composition{}, geometry{}}`
- `system` → `{domain, technique, facility{}, instrument{}, configuration{}}`
- `context` → `{environment, temperature_K, ...}`
- `measurement` → `{series[{series_id, independent_variables[], channels[{name, unit, role, values[]}]}], qc{status}}`
- `links` (array) → `[{rel, target, basis, notes}]`
- `assets` (array) → `[{asset_id, content_role, uri, sha256, media_type}]`
- `descriptors` → `{policy{}, outputs[{label, generated_utc, generated_by{}, descriptors[{name, kind, source, value, unit, uncertainty{}}]}]}`

## Useful JSONB query patterns

```sql
-- Count records by type
SELECT record_type, COUNT(*) FROM records GROUP BY record_type;

-- Find records for a specific material name
SELECT record_id, data->'sample'->'material'->>'name' AS material
FROM records
WHERE data->'sample'->'material'->>'name' ILIKE '%copper%';

-- Filter by domain inside JSONB
SELECT record_id, data->>'record_domain' AS domain
FROM records
WHERE data->>'record_domain' = 'characterization';

-- List distinct sample forms
SELECT DISTINCT data->'sample'->>'sample_form' AS form FROM records;

-- Get measurement channel names
SELECT record_id,
       ch->>'name' AS channel_name,
       ch->>'role' AS channel_role
FROM records,
     jsonb_array_elements(data->'measurement'->'series') AS s,
     jsonb_array_elements(s->'channels') AS ch;
```

## ISAAC Living Ontology (Controlled Vocabulary)

The vocabulary below is synced from the ISAAC GitHub wiki. It defines every \
controlled field, its description, and all allowed values. Use this to \
understand the scientific meaning of fields and to write correct queries \
(e.g., filtering by exact vocabulary terms). The vocabulary is organised by \
section > category > allowed values.

{vocabulary_block}

## Rules

1. When you need data from the database, write SQL inside a ```sql fenced \
code block. The system will execute it and show you the results.
2. You may make up to 3 SQL queries per user question.
3. Only SELECT / WITH (CTE) queries are allowed. No mutations.
4. Keep queries efficient. Use LIMIT when exploring.
5. After receiving query results, summarise them clearly for the researcher.
6. If a question cannot be answered from the database, say so.
7. Always be concise and scientifically precise.
8. Use vocabulary knowledge to interpret results and suggest follow-up queries.
"""


def _extract_sql_blocks(text: str) -> list[str]:
    """Extract all ```sql ... ``` fenced code blocks from LLM output."""
    return re.findall(r"```sql\s*\n(.*?)```", text, re.DOTALL)


def _call_llm(messages: list[dict]) -> str:
    """
    Call the Stanford AI API Gateway.

    Args:
        messages: OpenAI-compatible message list

    Returns:
        The assistant's response text

    Raises:
        RuntimeError: If the API call fails
    """
    api_key = os.environ.get("ISAAC_LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("ISAAC_LLM_API_KEY not configured")

    resp = http_requests.post(
        LLM_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "stream": False,
            "temperature": 0.2,
            "messages": messages,
        },
        timeout=60,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"LLM API returned {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _format_query_results(rows: list[dict], sql: str) -> str:
    """
    Format query results into a text block for the LLM, truncated at 8 KB.
    """
    header = f"Query:\n{sql}\n\nResults ({len(rows)} row{'s' if len(rows) != 1 else ''}):\n"
    if not rows:
        return header + "(no rows returned)"

    # Convert to JSON lines, respecting the truncation budget
    lines = [header]
    budget = RESULT_TRUNCATION_BYTES - len(header)
    for i, row in enumerate(rows):
        # Serialize datetimes
        for k, v in row.items():
            if hasattr(v, 'isoformat'):
                row[k] = v.isoformat()
        line = json.dumps(row, default=str)
        if budget - len(line) < 0:
            lines.append(f"... truncated ({len(rows) - i} more rows)")
            break
        lines.append(line)
        budget -= len(line) + 1  # +1 for newline

    return "\n".join(lines)


def _build_vocabulary_block() -> str:
    """
    Load the living ontology from the wiki-synced vocabulary cache and
    format it as a compact text block for the system prompt.
    """
    try:
        vocab = ontology.load_vocabulary()
    except Exception:
        vocab = {}

    if not vocab:
        return "(Vocabulary not available — answer based on raw database values.)"

    lines = []
    for section, categories in vocab.items():
        lines.append(f"### {section}")
        for cat_key, cat_data in categories.items():
            desc = cat_data.get("description", "")
            values = cat_data.get("values", [])
            values_str = ", ".join(values) if values else "(no values)"
            lines.append(f"- **{cat_key}**: {desc}")
            lines.append(f"  Values: [{values_str}]")
    return "\n".join(lines)


def build_initial_messages() -> list[dict]:
    """Create the initial conversation with the system prompt + live vocabulary."""
    vocab_block = _build_vocabulary_block()
    prompt = SYSTEM_PROMPT.replace("{vocabulary_block}", vocab_block)
    return [{"role": "system", "content": prompt}]


def run_agent_turn(conversation_history: list[dict]) -> tuple[str, list[dict]]:
    """
    Run one agent turn: call LLM, extract SQL, execute, feed back.

    Implements a ReAct loop with up to MAX_TOOL_ROUNDS iterations.

    Args:
        conversation_history: Full message list (system + user + assistant messages)

    Returns:
        (final_assistant_text, updated_conversation_history)
    """
    messages = list(conversation_history)

    for _round in range(MAX_TOOL_ROUNDS):
        assistant_text = _call_llm(messages)
        messages.append({"role": "assistant", "content": assistant_text})

        sql_blocks = _extract_sql_blocks(assistant_text)
        if not sql_blocks:
            # No SQL to execute — the LLM is done
            return assistant_text, messages

        # Execute each SQL block and feed results back
        tool_results = []
        for sql in sql_blocks:
            try:
                rows = database.execute_readonly_query(sql.strip())
                result_text = _format_query_results(rows, sql.strip())
            except (ValueError, Exception) as exc:
                result_text = f"Query error:\n{sql}\n\nError: {exc}"
            tool_results.append(result_text)

        # Feed results back as a user message (tool-result pattern)
        feedback = "\n\n---\n\n".join(tool_results)
        messages.append({"role": "user", "content": feedback})

    # If we exhausted rounds, do one final LLM call to summarise
    assistant_text = _call_llm(messages)
    messages.append({"role": "assistant", "content": assistant_text})
    return assistant_text, messages
