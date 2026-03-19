"""
ISAAC AI-Ready Record - Ontology/Vocabulary Module
Wiki-sourced living ontology with proposal/approval workflow.

Data flow:
  Wiki (source of truth) → sync_from_wiki() → vocabulary_cache table → load_vocabulary()
  Proposals: user submits → admin approves → apply_approved_proposal() → cache + wiki push
  Fallback: vocabulary_cache → vocabulary.json (file)
"""

import json
import os
import re
import tempfile
import shutil

import yaml
import git
import requests as http_requests

# Path to vocabulary file in data/ directory (fallback)
VOCAB_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "vocabulary.json")

# Database imports (optional, for PostgreSQL support)
try:
    from database import (
        get_db_connection, is_db_configured, test_db_connection,
        save_vocabulary_cache, load_vocabulary_cache, get_last_sync,
    )
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

# Wiki page → section name mapping
WIKI_PAGE_TO_SECTION = {
    "Record-Overview": "Record Info",
    "Sample": "Sample",
    "Context": "Context",
    "System": "System",
    "Measurement": "Measurement",
    "Assets": "Assets",
    "Links": "Links",
    "Descriptors": "Descriptors",
}

SECTION_TO_WIKI_PAGE = {v: k for k, v in WIKI_PAGE_TO_SECTION.items()}


def _use_database():
    """Determine if we should use database or file storage"""
    if not DB_AVAILABLE:
        return False
    if not is_db_configured():
        return False
    return test_db_connection()


def is_admin(username: str) -> bool:
    """Check if a username is in the ISAAC_ADMINS list."""
    admins_str = os.environ.get("ISAAC_ADMINS", "")
    if not admins_str:
        return False
    admins = [a.strip().lower() for a in admins_str.split(",") if a.strip()]
    return username.lower() in admins


# =============================================================================
# File-based operations (local development fallback)
# =============================================================================

def _load_vocabulary_from_file():
    """Loads the vocabulary JSON file."""
    if not os.path.exists(VOCAB_FILE):
        return {}
    with open(VOCAB_FILE, 'r') as f:
        return json.load(f)


# =============================================================================
# Wiki Sync Operations
# =============================================================================

def _get_wiki_url():
    """Get the wiki repo URL, optionally injecting GITHUB_TOKEN for auth."""
    url = os.environ.get("WIKI_REPO_URL", "")
    if not url:
        return None

    token = os.environ.get("GITHUB_TOKEN", "")
    if token and "github.com" in url and "@" not in url:
        # Inject token: https://github.com/... → https://<token>@github.com/...
        url = url.replace("https://github.com/", f"https://{token}@github.com/")
    return url


def _clone_or_pull_wiki(target_dir: str):
    """Clone or pull the wiki repo into target_dir using GitPython."""
    url = _get_wiki_url()
    if not url:
        raise ValueError("WIKI_REPO_URL not configured")

    repo_path = os.path.join(target_dir, "wiki")

    if os.path.exists(os.path.join(repo_path, ".git")):
        repo = git.Repo(repo_path)
        repo.remotes.origin.pull()
    else:
        git.Repo.clone_from(url, repo_path)

    return repo_path


def _parse_yaml_from_markdown(md_content: str) -> dict:
    """
    Extract vocabulary YAML from a markdown file's ## Controlled Vocabulary section.

    Looks for a pattern like:
        ## Controlled Vocabulary
        ```yaml
        key:
          description: "..."
          values: [...]
        ```

    Returns:
        Parsed dict from the YAML block, or empty dict if not found/parse error.
    """
    # Match both ATX (## Heading) and Setext (Heading\n---) style headings
    pattern = r'(?:##\s*Controlled\s+Vocabulary|Controlled\s+Vocabulary\n-+)\s*\n+```yaml\s*\n(.*?)```'
    match = re.search(pattern, md_content, re.DOTALL | re.IGNORECASE)

    if not match:
        return {}

    yaml_text = match.group(1)
    try:
        parsed = yaml.safe_load(yaml_text)
        if isinstance(parsed, dict):
            return parsed
        return {}
    except yaml.YAMLError:
        return {}


def sync_from_wiki(synced_by: str = "system") -> tuple:
    """
    Clone/pull the wiki, parse each page's Controlled Vocabulary YAML,
    and save to the vocabulary_cache table.

    Returns:
        (success: bool, message: str)
    """
    if not _use_database():
        return False, "Database not available"

    tmp_dir = tempfile.mkdtemp(prefix="isaac_wiki_")
    try:
        repo_path = _clone_or_pull_wiki(tmp_dir)

        vocab = {}
        parsed_pages = 0
        skipped_pages = []

        for wiki_page, section_name in WIKI_PAGE_TO_SECTION.items():
            md_file = os.path.join(repo_path, f"{wiki_page}.md")
            if not os.path.exists(md_file):
                skipped_pages.append(wiki_page)
                continue

            with open(md_file, 'r') as f:
                content = f.read()

            page_vocab = _parse_yaml_from_markdown(content)
            if page_vocab:
                vocab[section_name] = {}
                for key, data in page_vocab.items():
                    if isinstance(data, dict):
                        vocab[section_name][key] = {
                            'description': data.get('description', ''),
                            'values': data.get('values', [])
                        }
                parsed_pages += 1
            else:
                skipped_pages.append(wiki_page)

        if not vocab:
            return False, "No vocabulary data found in wiki pages"

        save_vocabulary_cache(vocab, synced_by)

        msg = f"Synced {parsed_pages} pages, {sum(len(cats) for cats in vocab.values())} categories"
        if skipped_pages:
            msg += f" (skipped: {', '.join(skipped_pages)})"
        return True, msg

    except Exception as e:
        return False, f"Wiki sync failed: {e}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _regenerate_yaml_block(vocab_for_section: dict) -> str:
    """Generate the YAML code block string for a section's vocabulary."""
    lines = []
    for key, data in sorted(vocab_for_section.items()):
        lines.append(f'{key}:')
        lines.append(f'  description: "{data.get("description", "")}"')
        values = data.get('values', [])
        values_str = ", ".join(values)
        lines.append(f'  values: [{values_str}]')
    return "\n".join(lines)


# =============================================================================
# LLM-Assisted Wiki Prose Generation (Stanford AI API Gateway)
# =============================================================================

LLM_API_URL = "https://aiapi-prod.stanford.edu/v1/chat/completions"
LLM_MODEL = "gemini-2.5-pro"


def _get_wiki_page_content(section: str) -> str:
    """Clone the wiki and return the full markdown content for a section's page."""
    wiki_page = SECTION_TO_WIKI_PAGE.get(section)
    if not wiki_page:
        return ""

    tmp_dir = tempfile.mkdtemp(prefix="isaac_wiki_read_")
    try:
        repo_path = _clone_or_pull_wiki(tmp_dir)
        md_file = os.path.join(repo_path, f"{wiki_page}.md")
        if not os.path.exists(md_file):
            return ""
        with open(md_file, 'r') as f:
            return f.read()
    except Exception:
        return ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def generate_wiki_description(section: str, category: str,
                              term: str = None, proposal_type: str = "add_term",
                              user_description: str = "") -> dict:
    """
    Use Stanford AI API to generate wiki-style description for a new term or category.

    Args:
        section: ontology section (e.g. "System")
        category: category key (e.g. "system.domain")
        term: new term value (for add_term)
        proposal_type: 'add_term' or 'add_category'
        user_description: proposer's plain-language description of the term/category

    Returns:
        {
            'yaml_description': str,   # One-line description for YAML block
            'wiki_prose': str,         # Markdown prose to insert into wiki page
            'success': bool,
            'error': str or None
        }
    """
    api_key = os.environ.get("ISAAC_LLM_API_KEY", "")
    if not api_key:
        return {
            'yaml_description': '',
            'wiki_prose': '',
            'success': False,
            'error': 'ISAAC_LLM_API_KEY not configured'
        }

    # Get existing wiki page for tone reference
    wiki_content = _get_wiki_page_content(section)
    if not wiki_content:
        wiki_content = "(Wiki page not available — generate in a generic scientific style.)"

    if proposal_type == "add_term":
        prompt = f"""You are editing the ISAAC AI-Ready Record wiki — a rigorous scientific metadata standard.

Below is the FULL content of the wiki page for the "{section}" section. Study its tone, structure, and the way existing enum values are defined (terse, precise, one-line definitions using the pattern `*   \\`value\\`: Definition.`).

---
{wiki_content}
---

A new term `{term}` is being added to the enum `{category}`.

The proposer described this term as:
"{user_description or '(no description provided)'}"

Use this description as the basis for the definition, but rewrite it to match the wiki's terse, normative style.

Generate TWO things:

1. **yaml_description**: If the existing YAML description for this category is adequate, return it unchanged. Only update it if the new term changes the scope.

2. **wiki_prose**: A single bullet-point definition for `{term}` matching the EXACT style of the existing bullet points for this enum. Format: `*   \\`{term}\\`: <terse definition>.`

Return ONLY valid JSON (no markdown fencing):
{{"yaml_description": "...", "wiki_prose": "..."}}"""

    elif proposal_type == "add_category":
        prompt = f"""You are editing the ISAAC AI-Ready Record wiki — a rigorous scientific metadata standard.

Below is the FULL content of the wiki page for the "{section}" section. Study its tone, structure, and the way subsections are written.

---
{wiki_content}
---

A new category `{category}` is being added to this section.

The proposer described this category as:
"{user_description or term or '(no description provided)'}"

Use this description as the basis, but rewrite to match the wiki's terse, normative style.

Generate TWO things:

1. **yaml_description**: A terse one-line description for this category (matching the style of existing YAML descriptions).

2. **wiki_prose**: A new subsection for this category matching the wiki's style. Include a heading (### level), type, description, and any relevant constraints. Keep it concise and normative.

Return ONLY valid JSON (no markdown fencing):
{{"yaml_description": "...", "wiki_prose": "..."}}"""
    else:
        return {'yaml_description': '', 'wiki_prose': '', 'success': False, 'error': f'Unknown type: {proposal_type}'}

    try:
        resp = http_requests.post(
            LLM_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "stream": False,
                "temperature": 0.3,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )

        if resp.status_code != 200:
            return {
                'yaml_description': '', 'wiki_prose': '', 'success': False,
                'error': f'LLM API returned {resp.status_code}: {resp.text[:200]}'
            }

        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()

        # Parse the JSON response (handle potential markdown fencing)
        content = re.sub(r'^```json\s*', '', content)
        content = re.sub(r'\s*```$', '', content)
        result = json.loads(content)

        return {
            'yaml_description': result.get('yaml_description', ''),
            'wiki_prose': result.get('wiki_prose', ''),
            'success': True,
            'error': None
        }

    except json.JSONDecodeError as e:
        return {
            'yaml_description': '', 'wiki_prose': content if 'content' in dir() else '',
            'success': False, 'error': f'Failed to parse LLM response as JSON: {e}'
        }
    except Exception as e:
        return {
            'yaml_description': '', 'wiki_prose': '', 'success': False,
            'error': f'LLM request failed: {e}'
        }


# =============================================================================
# Wiki Push Operations
# =============================================================================

def push_change_to_wiki(section: str, vocab_for_section: dict,
                        wiki_prose: str = "", category: str = "",
                        proposal_type: str = "add_term") -> tuple:
    """
    Clone the wiki, update the YAML block for the given section,
    optionally insert wiki prose, commit & push.

    Args:
        section: section name (e.g. "System")
        vocab_for_section: full vocabulary dict for this section
        wiki_prose: optional markdown prose to insert into wiki page
        category: category key (e.g. "system.domain") — used to locate the right subsection
        proposal_type: 'add_term' or 'add_category'

    Returns:
        (success: bool, message: str)
    """
    wiki_page = SECTION_TO_WIKI_PAGE.get(section)
    if not wiki_page:
        return False, f"No wiki page mapping for section '{section}'"

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return False, "GITHUB_TOKEN not configured — cannot push to wiki"

    tmp_dir = tempfile.mkdtemp(prefix="isaac_wiki_push_")
    try:
        repo_path = _clone_or_pull_wiki(tmp_dir)
        md_file = os.path.join(repo_path, f"{wiki_page}.md")

        if not os.path.exists(md_file):
            return False, f"Wiki page {wiki_page}.md not found"

        with open(md_file, 'r') as f:
            content = f.read()

        # Insert wiki prose into the correct location
        if wiki_prose and wiki_prose.strip():
            inserted = False

            if proposal_type == "add_term" and category:
                # For add_term: find the category's subsection and append
                # the bullet inside the **Values**: list.
                # Look for a heading like: ### 2.1 `system.domain`
                cat_heading = re.search(
                    r'###[^`\n]*`' + re.escape(category) + r'`',
                    content
                )
                if cat_heading:
                    # Find the **Values**: bullet within this subsection
                    # (search from the heading to the next ### or ## heading)
                    sub_start = cat_heading.start()
                    next_heading = re.search(r'\n#{2,3}\s', content[sub_start + 1:])
                    sub_end = sub_start + 1 + next_heading.start() if next_heading else len(content)
                    subsection = content[sub_start:sub_end]

                    # Find the last indented value bullet (    *   `value`: ...)
                    # to insert after it
                    value_bullets = list(re.finditer(
                        r'^    \*\s+`[^`]+`\s*:.*$',
                        subsection,
                        re.MULTILINE
                    ))
                    if value_bullets:
                        last_bullet = value_bullets[-1]
                        # Check for sub-bullets (constraints) after the last value
                        remaining = subsection[last_bullet.end():]
                        extra = 0
                        for line in remaining.split('\n'):
                            if line.startswith('        *'):
                                extra += len(line) + 1
                            elif line.strip() == '':
                                extra += len(line) + 1
                                continue
                            else:
                                break
                        abs_insert = sub_start + last_bullet.end() + extra
                        # Ensure the prose has proper indentation (4 spaces)
                        prose_line = wiki_prose.strip()
                        if not prose_line.startswith('    '):
                            prose_line = '    ' + prose_line
                        content = content[:abs_insert] + "\n" + prose_line + content[abs_insert:]
                        inserted = True

            if not inserted:
                # Fallback for add_category or if subsection not found:
                # insert before Controlled Vocabulary heading
                cv_pattern = r'(#{1,2}\s*Controlled\s+Vocabulary|Controlled\s+Vocabulary\n-+)'
                match = re.search(cv_pattern, content, re.IGNORECASE)
                if match:
                    insert_pos = match.start()
                    content = content[:insert_pos] + wiki_prose.strip() + "\n\n" + content[insert_pos:]
                else:
                    content = content.rstrip() + "\n\n" + wiki_prose.strip() + "\n"

        # Update the YAML block
        new_yaml = _regenerate_yaml_block(vocab_for_section)

        # Match both ATX (## Heading) and Setext (Heading\n---) style headings
        yaml_pattern = r'(?:##\s*Controlled\s+Vocabulary|Controlled\s+Vocabulary\n-+)\s*\n+```yaml\s*\n.*?```'
        if re.search(yaml_pattern, content, re.DOTALL | re.IGNORECASE):
            # Preserve the original heading style by only replacing from ```yaml onward
            yaml_only = r'((?:##\s*Controlled\s+Vocabulary|Controlled\s+Vocabulary\n-+)\s*\n+)```yaml\s*\n.*?```'
            new_content = re.sub(
                yaml_only,
                r'\g<1>' + f"```yaml\n{new_yaml}\n```",
                content,
                flags=re.DOTALL | re.IGNORECASE
            )
        else:
            new_content = content.rstrip() + "\n\n## Controlled Vocabulary\n\n```yaml\n" + new_yaml + "\n```\n"

        with open(md_file, 'w') as f:
            f.write(new_content)

        # Commit and push
        repo = git.Repo(repo_path)
        repo.index.add([f"{wiki_page}.md"])

        if repo.is_dirty() or repo.untracked_files:
            repo.index.commit(f"Update vocabulary for {section} (ISAAC Portal)")
            repo.remotes.origin.push()
            return True, f"Pushed vocabulary update for {section} to wiki"
        else:
            return True, "No changes to push"

    except Exception as e:
        return False, f"Wiki push failed: {e}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def apply_approved_proposal(proposal: dict, wiki_prose: str = "") -> tuple:
    """
    Apply an approved proposal: update DB cache and push to wiki (with prose).

    Args:
        proposal: dict with proposal_type, section, category, term, description
        wiki_prose: LLM-generated (admin-edited) prose to insert into wiki page

    Returns:
        (success: bool, message: str, wiki_push_ok: bool)
    """
    vocab = load_vocabulary()

    section = proposal['section']
    proposal_type = proposal['proposal_type']

    if section not in vocab:
        vocab[section] = {}

    if proposal_type == 'add_term':
        category = proposal['category']
        term = proposal['term']
        if category not in vocab[section]:
            return False, f"Category '{category}' not found in '{section}'", False
        if term in vocab[section][category]['values']:
            return False, f"Term '{term}' already exists", False
        vocab[section][category]['values'].append(term)

    elif proposal_type == 'add_category':
        category = proposal['category']
        description = proposal.get('description', '')
        if category in vocab[section]:
            return False, f"Category '{category}' already exists", False
        vocab[section][category] = {'description': description, 'values': []}

    else:
        return False, f"Unknown proposal type: {proposal_type}", False

    # Update DB cache
    if _use_database():
        try:
            save_vocabulary_cache(vocab, proposal.get('reviewed_by', 'system'))
        except Exception as e:
            return False, f"Failed to update cache: {e}", False

    # Push to wiki (with prose)
    wiki_push_ok = True
    wiki_msg = ""
    try:
        ok, wiki_msg = push_change_to_wiki(
            section, vocab[section], wiki_prose=wiki_prose,
            category=proposal.get('category', ''),
            proposal_type=proposal_type
        )
        wiki_push_ok = ok
    except Exception as e:
        wiki_push_ok = False
        wiki_msg = str(e)

    msg = f"Applied proposal: {proposal_type}"
    if not wiki_push_ok:
        msg += f" (wiki push warning: {wiki_msg})"

    return True, msg, wiki_push_ok


# =============================================================================
# Public API
# =============================================================================

def load_vocabulary():
    """Loads the vocabulary from DB cache, falling back to file."""
    if _use_database():
        try:
            cached = load_vocabulary_cache()
            if cached:
                return cached
        except Exception:
            pass
    return _load_vocabulary_from_file()


def get_sections():
    """Returns list of top-level sections."""
    vocab = load_vocabulary()
    return list(vocab.keys())


def get_categories(section):
    """Returns categories in a section."""
    vocab = load_vocabulary()
    if section in vocab:
        return vocab[section]
    return {}


def add_term(section, category, term):
    """Deprecated — returns message directing to proposal workflow."""
    return False, "Direct edits are disabled. Please use the Propose form to suggest changes."


def add_category(section, category, description=""):
    """Deprecated — returns message directing to proposal workflow."""
    return False, "Direct edits are disabled. Please use the Propose form to suggest changes."


def sync_file_to_db():
    """Utility to sync vocabulary from file to database (for initial setup)."""
    if not _use_database():
        return False, "Database not configured"

    vocab = _load_vocabulary_from_file()
    if not vocab:
        return False, "No vocabulary file found"

    save_vocabulary_cache(vocab, "file_sync")
    return True, f"Synced {sum(len(cats) for cats in vocab.values())} categories to database"


# =============================================================================
# Record Vocabulary Validation
# =============================================================================

# Category keys that are namespaces (not enum fields in the record)
_SKIP_CATEGORIES = {"descriptors.theoretical_metric"}


def _resolve_path(data, path_parts):
    """
    Walk *data* following *path_parts* (list of key strings).

    When a value along the path is a list, iterate over every element and
    continue walking the remaining keys inside each element.  This lets a
    single dotted key like ``measurement.series.channels.role`` reach into
    ``record["measurement"]["series"][*]["channels"][*]["role"]``.

    Returns a list of ``(dotted_path_string, leaf_value)`` tuples for every
    leaf reached.
    """
    if not path_parts:
        return []

    results = []

    def _walk(obj, remaining, breadcrumb):
        if not remaining:
            # Reached the end — obj is the leaf value
            results.append((".".join(breadcrumb), obj))
            return

        key = remaining[0]
        rest = remaining[1:]

        if isinstance(obj, dict):
            if key in obj:
                _walk(obj[key], rest, breadcrumb + [key])
        elif isinstance(obj, list):
            # Transparently iterate over array elements
            for idx, item in enumerate(obj):
                _walk(item, remaining, breadcrumb + [str(idx)])

    _walk(data, list(path_parts), [])
    return results


def validate_record_vocabulary(record):
    """
    Validate *record* (a dict) against the live vocabulary.

    Returns a list of error dicts ``[{"path": ..., "message": ...}]``.
    An empty list means all vocabulary terms are valid.
    """
    vocab = load_vocabulary()
    if not vocab:
        return []  # No vocabulary loaded — skip validation

    errors = []

    for section_name, categories in vocab.items():
        for cat_key, cat_data in categories.items():
            if cat_key in _SKIP_CATEGORIES:
                continue

            allowed = cat_data.get("values", [])
            if not allowed:
                continue

            path_parts = cat_key.split(".")
            hits = _resolve_path(record, path_parts)

            for dotted_path, value in hits:
                if isinstance(value, str) and value not in allowed:
                    errors.append({
                        "path": dotted_path,
                        "message": (
                            f"'{value}' is not in the vocabulary for "
                            f"{cat_key}. Allowed: {allowed}"
                        ),
                    })

    return errors


def validate_semantic_integrity(data: dict) -> list:
    """
    Validate hard semantic rules that JSON Schema alone cannot enforce.

    Rules:
    1. Evidence records must contain at least one descriptor with a non-null
       value. A record with all-null descriptor values is a template/facility
       description, not scientific evidence.
    2. generated_utc in descriptor outputs must not contain placeholder text.

    Returns a list of error dicts ``[{"path": ..., "message": ...}]``.
    """
    errors = []
    record_type = data.get("record_type")

    if record_type == "evidence":
        outputs = data.get("descriptors", {}).get("outputs", [])
        has_real_value = False
        for output in outputs:
            gen_utc = output.get("generated_utc", "")
            if gen_utc and ("PLACEHOLDER" in gen_utc.upper()
                           or "TBD" in gen_utc.upper()):
                errors.append({
                    "path": (
                        f"descriptors/outputs/"
                        f"{output.get('label', '?')}/generated_utc"
                    ),
                    "message": (
                        f"Placeholder timestamp '{gen_utc}' is not allowed. "
                        f"Use a real ISO 8601 timestamp."
                    ),
                })
            for desc in output.get("descriptors", []):
                if desc.get("value") is not None:
                    has_real_value = True
                    break
            if has_real_value:
                break

        if not has_real_value:
            errors.append({
                "path": "descriptors",
                "message": (
                    "Evidence record rejected: every descriptor has a null "
                    "value. An evidence record must assert at least one "
                    "scientific claim (a descriptor with a non-null value). "
                    "Facility templates, setup descriptions, and blank forms "
                    "are not valid evidence records."
                ),
            })

    return errors


def merge_vocabulary_into_schema(schema: dict) -> dict:
    """
    Return a deep copy of *schema* with vocabulary ``enum`` constraints
    injected for every field governed by the live vocabulary.

    Where the base schema already has an ``enum``, it is **replaced** by the
    vocabulary values so the returned schema is the single source of truth.
    Fields not covered by the vocabulary are left untouched.
    """
    import copy

    vocab = load_vocabulary()
    if not vocab:
        return copy.deepcopy(schema)

    merged = copy.deepcopy(schema)

    for _section_name, categories in vocab.items():
        for cat_key, cat_data in categories.items():
            if cat_key in _SKIP_CATEGORIES:
                continue

            allowed = cat_data.get("values", [])
            if not allowed:
                continue

            # Walk the JSON Schema tree following the dotted path.
            # When a schema node is {"type": "array", "items": {...}}
            # we transparently descend into "items" — mirroring how
            # _resolve_path walks actual record data through arrays.
            path_parts = cat_key.split(".")
            node = merged.get("properties", {})
            resolved = True

            for i, key in enumerate(path_parts):
                if key not in node:
                    resolved = False
                    break

                if i == len(path_parts) - 1:
                    # Leaf — inject the enum
                    node[key]["enum"] = allowed
                else:
                    # Intermediate — descend
                    sub = node[key]
                    # Traverse into array items transparently
                    if sub.get("type") == "array" and "items" in sub:
                        sub = sub["items"]
                    node = sub.get("properties", {})

            if not resolved:
                # Path not found in schema — vocabulary covers an
                # extension field not (yet) in the base schema; skip.
                continue

    return merged
