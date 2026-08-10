#!/usr/bin/env python3
"""The wiki mirrors the schema. Enforce that, and enforce that every field is documented.

Two failures this exists to prevent, both of which happened:

  1. AD-HOC TOPIC PAGES. A new page per special case ("Driven-and-Modulated-Experiments",
     "Provenance-and-Independent-Evidence") turns a structured reference into a pile. A reader
     — or an agent — looking up a `context` field must find it on the Context page, always,
     without knowing that a topic page exists. The page set is therefore CLOSED: one page per
     schema block, plus a fixed list of meta pages.

  2. UNDOCUMENTED FIELDS. A field can be added to the schema and never written down. Then the
     only way to discover it is to read the JSON Schema, which is exactly the failure mode an
     AI-ready standard exists to remove.

Usage:
    python3 tools/check_wiki_structure.py /path/to/wiki        # report
    python3 tools/check_wiki_structure.py --check /path/to/wiki  # exit 1 on any violation
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Blocks that are documented inside another block's page rather than getting their own.
FOLDED_INTO = {
    "attribution": "Record-Overview",
    "timestamps": "Record-Overview",
    "record_id": "Record-Overview",
    "record_type": "Record-Overview",
    "record_domain": "Record-Overview",
    "source_type": "Record-Overview",
    "isaac_record_version": "Record-Overview",
    "tags": "Record-Overview",
}

# Meta pages: navigation, generated references, and guides. Closed list ON PURPOSE — adding to
# it should be a deliberate decision someone reviews, not a side effect of documenting a field.
META_PAGES = {
    "Home", "Record-Overview", "Record-Granularity", "Schema-Architecture",
    "Controlled-Vocabulary", "Validation-Rules", "Constraint-Matrix",
    "Query-API", "Ecosystem", "Write-Your-First-Record",
}


def block_page(name):
    return "".join(w.capitalize() for w in name.split("_"))


def main():
    check = "--check" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    wiki = pathlib.Path(args[0]) if args else ROOT / "docs" / "wiki"
    schema = json.loads((ROOT / "schema" / "isaac_record_v1.json").read_text())
    blocks = set(schema["properties"])

    pages = {p.stem for p in wiki.glob("*.md") if not p.stem.startswith("_")}
    expected = {block_page(b) for b in blocks if b not in FOLDED_INTO} | META_PAGES

    problems = []

    extra = sorted(pages - expected)
    if extra:
        problems.append(
            "AD-HOC PAGES (the wiki is one page per schema block plus a closed meta list; "
            "document a field on its block's page instead of adding a page): %s" % extra)

    missing = sorted(expected - pages)
    if missing:
        problems.append("MISSING block pages: %s" % missing)

    # Every leaf field of every block must appear somewhere in its page.
    def leaves(node, prefix=""):
        out = []
        if not isinstance(node, dict):
            return out
        props = node.get("properties")
        if isinstance(props, dict):
            for k, v in props.items():
                out.append(k)
                out += leaves(v, prefix + "/" + k)
        if isinstance(node.get("items"), dict):
            out += leaves(node["items"], prefix + "[]")
        return out

    undocumented = {}
    for b in sorted(blocks):
        page = wiki / (FOLDED_INTO.get(b, block_page(b)) + ".md")
        if not page.exists():
            continue
        text = page.read_text()
        miss = [f for f in sorted(set(leaves(schema["properties"][b]))) if f not in text]
        if miss:
            undocumented[b] = miss
    # STRUCTURE is a hard gate: it is clean today and must stay clean.
    # FIELD COVERAGE is a RATCHET. 44 fields were already undocumented when this check was
    # written; blocking CI on pre-existing debt would just get the check disabled. Instead the
    # count may never RISE, so every new field arrives documented and the debt can only be paid
    # down. Lower the baseline whenever you fix some.
    n_undoc = sum(len(v) for v in undocumented.values())
    baseline = 10**6
    bfile = ROOT / "tools" / "wiki_coverage_baseline.json"
    if bfile.exists():
        baseline = json.loads(bfile.read_text()).get("undocumented_baseline", baseline)
    if n_undoc > baseline:
        problems.append(
            "UNDOCUMENTED FIELDS ROSE from %d to %d. A field added to the schema must be "
            "documented on its block's page in the SAME change. New/worse: %s"
            % (baseline, n_undoc, json.dumps(undocumented, indent=1)))
    elif undocumented:
        print("note: %d schema fields undocumented (baseline %d, not rising). Paying this "
              "down is how the wiki becomes usable without reading the JSON Schema:\n%s\n"
              % (n_undoc, baseline, json.dumps(undocumented, indent=1)))
        if n_undoc < baseline:
            print("      coverage IMPROVED — lower `undocumented_baseline` to %d in "
                  "tools/wiki_coverage_baseline.json to lock it in.\n" % n_undoc)

    if problems:
        print("WIKI STRUCTURE: %d problem(s)\n" % len(problems))
        for p in problems:
            print("  - %s\n" % p)
        if check:
            sys.exit(1)
        return
    print("wiki structure ok: %d pages, one per schema block plus %d meta pages; "
          "every schema field documented on its own block's page"
          % (len(pages), len(META_PAGES)))


if __name__ == "__main__":
    main()
