#!/usr/bin/env python3
"""
Generate the per-page Controlled Vocabulary appendices FROM data/vocabulary.json.

Hand-written vocab appendices drifted on every audited page (Sample still
taught pre-migration X_pct keys; Descriptors omitted 14 product tokens;
Computation had no appendix at all). This generator makes vocabulary.json
the single rendered truth, between markers CI can verify:

    <!-- BEGIN GENERATED:vocab -->   ...   <!-- END GENERATED:vocab -->

Usage:
  python3 tools/generate_wiki_vocab.py /path/to/wiki        # rewrite appendices in place
  python3 tools/generate_wiki_vocab.py --check /path/to/wiki  # exit 1 if any page stale
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VOCAB = json.loads((REPO / "data" / "vocabulary.json").read_text())
SCHEMA_VERSION = json.loads((REPO / "schema" / "isaac_record_v1.json").read_text()) \
    .get("properties", {}).get("isaac_record_version", {}).get("const", "1.05")

PAGE_SECTIONS = {
    "Sample.md": ["Sample"],
    "Context.md": ["Context"],
    "System.md": ["System"],
    "Measurement.md": ["Measurement"],
    "Descriptors.md": ["Descriptors"],
    "Computation.md": ["Computation"],
    "Assets.md": ["Assets"],
    "Links.md": ["Links"],
}

BEGIN = "<!-- BEGIN GENERATED:vocab -->"
END = "<!-- END GENERATED:vocab -->"


def render_section(section_names):
    lines = [BEGIN,
             f"## Controlled Vocabulary (generated from `data/vocabulary.json`, schema v{SCHEMA_VERSION})",
             "",
             "> Do not hand-edit between the GENERATED markers — regenerate with "
             "`tools/generate_wiki_vocab.py`. CI fails when this section is stale. "
             "Values listed here are exactly what the validator accepts.",
             ""]
    for sec in section_names:
        entries = VOCAB.get(sec)
        if not isinstance(entries, dict):
            continue
        for key in sorted(entries):
            e = entries[key]
            if not isinstance(e, dict):
                continue
            lines.append(f"**`{key}`**" + (f" — {e['description']}" if e.get("description") else ""))
            vals = e.get("values") or []
            if vals:
                lines.append("```")
                lines.append(", ".join(str(v) for v in vals))
                lines.append("```")
            mp = e.get("map")
            if isinstance(mp, dict) and mp:
                lines.append("| rejected alias | canonical |")
                lines.append("|---|---|")
                for a, c in sorted(mp.items()):
                    lines.append(f"| `{a}` | `{c}` |")
            lines.append("")
    lines.append(END)
    return "\n".join(lines)


def apply(page_path, section_names):
    text = page_path.read_text() if page_path.exists() else f"# {page_path.stem}\n"
    new_block = render_section(section_names)
    if BEGIN in text and END in text:
        text = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), new_block, text, flags=re.S)
    else:
        # Replace a legacy hand-written appendix if present, else append
        m = re.search(r"\n#+\s*(?:\d+\.\s*)?(?:Appendix:?\s*)?Controlled Vocabular.*?(?=\n## |\Z)", text, flags=re.S | re.I)
        if m:
            text = text[:m.start()] + "\n" + new_block + "\n" + text[m.end():]
        else:
            text = text.rstrip() + "\n\n" + new_block + "\n"
    return text


def main():
    check = "--check" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--check"]
    wiki = Path(args[0]) if args else REPO.parent / "isaac-ai-ready-record.wiki"
    stale = []
    for page, sections in PAGE_SECTIONS.items():
        p = wiki / page
        desired = apply(p, sections)
        current = p.read_text() if p.exists() else ""
        if desired != current:
            if check:
                stale.append(page)
            else:
                p.write_text(desired)
                print(f"regenerated: {page}")
    if check:
        if stale:
            print(f"STALE vocab appendices: {stale} — run tools/generate_wiki_vocab.py")
            return 1
        print("all vocab appendices up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
