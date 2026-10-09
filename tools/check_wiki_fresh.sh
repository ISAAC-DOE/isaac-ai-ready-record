#!/bin/bash
# The published wiki matches this checkout's generators: the same six checks the main CI runs, on a fresh
# clone of the wiki as GitHub serves it (so a wiki edit that was never pushed is caught too).
# Usage: tools/check_wiki_fresh.sh      exit 1 if any generated section is stale
W=$(mktemp -d)/wiki
git clone -q --depth 1 https://github.com/ISAAC-DOE/isaac-ai-ready-record.wiki.git "$W" || { echo "wiki clone failed"; exit 1; }
rc=0
python3 tools/generate_schema_diagram.py --check "$W/Schema-Architecture.md" || rc=1
python3 tools/generate_wiki_vocab.py --check "$W" || rc=1
python3 tools/generate_validation_docs.py --check "$W" || rc=1
python3 tools/generate_constraint_matrix.py --check "$W" || rc=1
python3 tools/check_wiki_structure.py --check "$W" > /dev/null || rc=1
python3 tools/generate_record_contract.py --check "$W" || rc=1
rm -rf "$(dirname "$W")"
exit $rc
