"""Keys that connect records, derived from what each record already says.

Two kinds of connection. A record belongs to shared things: a study (the paper, thesis or database
collection its values come from), a physical sample, a lab, an organization, a setup, a
computational method. Those are KEYS, one per record per dimension, so a study of 40 records is 40
rows holding one value, never 40 x 39 links. A record also relates to particular other records:
derived_from, calibration_of, follows, replica_of, intended_comparison_target, same_sample_as,
validates, invalidates. Those are LINKS, declared in the record's links[] and indexed here so each
one can be followed from either end.

Everything is derived from fields the record already carries, and normalized, so the same paper
written as '10.1038/x' and as 'https://doi.org/10.1038/X' is one study. Nothing here changes a
record or asks anything of an uploader. Pure functions only: the database layer stores the result
(tables record_keys and record_links) and serves it (GET /records?study=..., /records/<id>/cluster,
/records/<id>/neighbors).
"""
import functools
import hashlib
import json
import re
from pathlib import Path

# Bump when a derivation rule changes: the startup backfill re-derives every record whose keys are older.
KEYS_VERSION = 1
VOCAB_PATH = Path(__file__).resolve().parent.parent / "data" / "vocabulary.json"

# Cluster dimensions, and the record_keys column each one reads.
DIMENSIONS = {"study": "study", "sample": "sample_id", "lab": "lab", "organization": "organization",
              "setup": "setup", "method": "method"}
# Relations whose meaning does not depend on which record declared them.
SYMMETRIC_RELATIONS = frozenset({"same_sample_as", "replica_of", "intended_comparison_target"})

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
_DOI_SHAPE = re.compile(r"^10\.\d{4,9}/\S+$")
_STUDY_RELATIONS = (None, "", "source", "reports_this_work")
# Exact placeholder values ('N/A', 'none') come from the vocabulary; these phrases mark one anywhere.
_PLACEHOLDER_TEXT = re.compile(r"\b(?:not (?:specified|reported|stated|given|available)|unknown|unspecified|tbd)\b")
_AUTHORS_AS_GROUP = re.compile(
    r"^(?:the )?(?:(?:paper|publication|study|source|article)['’]?s? |original )?authors?"
    r"(?: of the (?:paper|publication|study|source|article))?$")
# A density-functional result is only comparable to another at the same functional.
_FUNCTIONAL_DEFINED = frozenset({"dft", "dft_u", "hybrid_dft", "aimd"})


@functools.lru_cache(maxsize=1)
def vocabulary() -> dict:
    """The vocabulary file the validator reads (organization aliases, ROR registry, placeholders)."""
    return json.loads(VOCAB_PATH.read_text())


def derivation_id(vocab: dict = None) -> str:
    """Names the rules and vocabulary the keys were derived with: a stored row derived under another
    id is stale, so a new rule (KEYS_VERSION) or a new alias re-derives the keys it changes."""
    aliases, registry, placeholders = _vocab_lists(vocabulary() if vocab is None else vocab)
    blob = json.dumps([aliases, registry, sorted(placeholders)], sort_keys=True)
    return f"{KEYS_VERSION}:{hashlib.sha256(blob.encode()).hexdigest()[:12]}"


def key_text(value) -> str:
    """Case-, whitespace- and underscore-insensitive form of a free-text value."""
    return re.sub(r"[\s_]+", " ", str(value or "")).strip().casefold()


def normalize_doi(value):
    """'https://doi.org/10.1038/X.', 'doi:10.1038/x' and '10.1038/x' are one DOI; anything else is None."""
    doi = _DOI_PREFIX.sub("", str(value or "").strip()).strip().rstrip(".,;)").lower()
    return doi if _DOI_SHAPE.match(doi) else None


def _dig(record, *path):
    node = record
    for part in path:
        node = node.get(part) if isinstance(node, dict) else None
    return node


def _vocab_lists(vocab):
    system = (vocab or {}).get("System") or {}
    aliases = (system.get("system.organization_aliases") or {}).get("map") or {}
    registry = (system.get("system.organizations") or {}).get("values") or {}
    placeholders = {key_text(p) for p in (system.get("system.organization_placeholders") or {}).get("values") or []}
    return (aliases if isinstance(aliases, dict) else {}), (registry if isinstance(registry, dict) else {}), placeholders


def _stated(value, placeholders=()) -> str:
    """The value as key text, or '' when it is empty or says the source does not give it."""
    text = key_text(value)
    return "" if not text or text in placeholders or _PLACEHOLDER_TEXT.search(text) else text


def study_keys(record: dict) -> list:
    """The works a record's values come from, as the validator reads a source: a citation whose
    relation is 'source', 'reports_this_work' or not given, or a doi.org link carrying no citation.
    'doi:<doi>' when the work has a DOI, 'work:<title>|<year>' when it has only a title, and
    'db:<database>:<collection>' for a database collection. A DOI extending another cited DOI (a
    paper's SI, '10.x/abc.s001' under '10.x/abc') is the same work."""
    dois, keys = set(), set()
    assets = record.get("assets") if isinstance(record.get("assets"), list) else []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        citation = asset.get("citation") if isinstance(asset.get("citation"), dict) else None
        uri = str(asset.get("uri") or "")
        uri_doi = normalize_doi(uri) if "doi.org/" in uri.lower() else None
        if citation is None:
            if uri_doi:
                dois.add(uri_doi)
        elif citation.get("relation") in _STUDY_RELATIONS:
            doi = normalize_doi(citation.get("doi")) or uri_doi
            if doi:
                dois.add(doi)
            elif key_text(citation.get("title")):
                keys.add(f"work:{key_text(citation.get('title'))}|{key_text(citation.get('year'))}")
        entry = asset.get("database_entry") if isinstance(asset.get("database_entry"), dict) else {}
        if key_text(entry.get("database")) and key_text(entry.get("collection")):
            keys.add(f"db:{key_text(entry['database'])}:{key_text(entry['collection'])}")
    keys |= {f"doi:{d}" for d in dois
             if not any(d != o and d.startswith(o) and d[len(o)] in "./-_" for o in dois)}
    return sorted(keys)


def sample_key(record: dict) -> str:
    sid = key_text(_dig(record, "sample", "sample_id"))
    return f"sample:{sid}" if sid else None


def lab_key(record: dict, placeholders=()) -> str:
    group = _stated(_dig(record, "attribution", "produced_by", "group"), placeholders)
    return f"group:{group}" if group and not _AUTHORS_AS_GROUP.match(group) else None


def organization_key(name, aliases: dict, registry: dict, placeholders=()) -> str:
    """One key per institution: 'ror:<id>' when the vocabulary registry knows it (after resolving
    aliases such as 'SLAC' or 'LBNL'), else 'org:<name>'."""
    raw = str(name or "").strip()
    if not _stated(raw, placeholders):
        return None
    canonical = aliases.get(raw) or {key_text(k): v for k, v in aliases.items()}.get(key_text(raw)) or raw
    ror = {key_text(k): v for k, v in registry.items()}.get(key_text(canonical))
    if isinstance(ror, str) and "ror.org/" in ror:
        return "ror:" + ror.rstrip("/").rsplit("/", 1)[-1].lower()
    return f"org:{key_text(canonical)}"


def setup_key(record: dict, placeholders=()) -> str:
    """Where and on what a measurement was made: facility, laboratory, beamline and instrument.
    A calculation has no setup (its code is its method), and an instrument model alone names no
    particular setup, since many labs own one."""
    system = record.get("system") if isinstance(record.get("system"), dict) else {}
    if key_text(system.get("domain")) == "computational":
        return None
    facility = system.get("facility") if isinstance(system.get("facility"), dict) else {}
    instrument = system.get("instrument") if isinstance(system.get("instrument"), dict) else {}
    where = [_stated(facility.get(k), placeholders) for k in ("facility_name", "laboratory", "beamline")]
    if not any(where):
        return None
    return "setup:" + "|".join(where + [_stated(instrument.get("instrument_name"), placeholders)])


def method_key(record: dict, placeholders=()) -> str:
    """Theory level: method family, functional and code. A density-functional method is known
    only with its functional."""
    method = _dig(record, "computation", "method")
    if not isinstance(method, dict):
        return None
    family, functional, code = (_stated(method.get(k), placeholders) for k in ("family", "functional_name", "code"))
    if not family or (str(method.get("family")).casefold() in _FUNCTIONAL_DEFINED and not functional):
        return None
    return f"method:{family}|{functional}|{code}"


def derive_keys(record: dict, vocab: dict = None) -> dict:
    """Every key of one record, by record_keys column. vocab supplies the organization aliases,
    the ROR registry and the placeholder values."""
    record = record if isinstance(record, dict) else {}
    aliases, registry, placeholders = _vocab_lists(vocabulary() if vocab is None else vocab)
    org = (_dig(record, "attribution", "produced_by", "organization")
           or _dig(record, "system", "facility", "organization"))
    return {
        "study": study_keys(record),
        "sample_id": sample_key(record),
        "lab": lab_key(record, placeholders),
        "organization": organization_key(org, aliases, registry, placeholders),
        "setup": setup_key(record, placeholders),
        "method": method_key(record, placeholders),
    }


def link_edges(record_id: str, record: dict) -> list:
    """[(target, rel, basis)], one per target and relation, for the links the record declares.
    Self-links and malformed links are skipped."""
    source = str(record_id or "").strip()
    edges = {}
    links = record.get("links") if isinstance(record.get("links"), list) else []
    for link in links:
        if not isinstance(link, dict):
            continue
        target, rel = str(link.get("target") or "").strip(), str(link.get("rel") or "").strip()
        basis = str(link.get("basis") or "").strip() or None
        if target and rel and target != source and edges.get((target, rel)) is None:
            edges[(target, rel)] = basis
    return sorted(((t, r, b) for (t, r), b in edges.items()), key=lambda e: (e[1], e[0]))


def key_from_param(dimension: str, value, vocab: dict = None):
    """A caller's filter value, in whatever spelling, as the stored key: a DOI in any form, a group,
    sample or institution name, a ROR id or URL, or any key the API returned. None when the value
    cannot name a key of that dimension."""
    value = str(value or "").strip()
    prefix, _, rest = value.partition(":")
    prefix = prefix.casefold()
    if not value:
        return None
    if dimension == "study":
        if prefix == "db":
            database, _, collection = rest.partition(":")
            return f"db:{key_text(database)}:{key_text(collection)}" if key_text(database) and key_text(collection) else None
        if prefix == "work":
            return f"work:{key_text(rest)}" if key_text(rest) else None
        doi = normalize_doi(value)
        return f"doi:{doi}" if doi else None
    if dimension in ("sample", "lab"):
        stored = "sample" if dimension == "sample" else "group"
        text = key_text(rest if prefix == stored else value)
        return f"{stored}:{text}" if text else None
    if dimension == "organization":
        if prefix == "ror":
            return f"ror:{rest.strip().lower()}" if rest.strip() else None
        if "ror.org/" in value.lower():
            return "ror:" + value.rstrip("/").rsplit("/", 1)[-1].lower()
        aliases, registry, _ = _vocab_lists(vocabulary() if vocab is None else vocab)
        return organization_key(rest if prefix == "org" else value, aliases, registry)
    if dimension in ("setup", "method") and prefix == dimension and key_text(rest):
        return dimension + ":" + "|".join(key_text(p) for p in rest.split("|"))
    return None
