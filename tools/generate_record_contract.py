#!/usr/bin/env python3
"""Render the record contract (data/record_contract.json) into the Record-Granularity wiki page.

The same file is served to agents by GET /portal/api/contract, so the wiki and the API say the
same thing. A code's tier (error or warning) comes from the validation registry, so the page
always shows what the validator does today.

Usage:
    python3 tools/generate_record_contract.py /path/to/wiki          # regenerate the section
    python3 tools/generate_record_contract.py --check /path/to/wiki  # exit 1 if the page is stale
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from generate_validation_docs import REGISTRY  # noqa: E402  (single source for code tiers)

CONTRACT = json.loads((ROOT / "data" / "record_contract.json").read_text())
BLOB = "https://github.com/ISAAC-DOE/isaac-ai-ready-record/blob/main/"
BEGIN = "<!-- BEGIN GENERATED:record-contract -->"
END = "<!-- END GENERATED:record-contract -->"
PAGE = "Record-Granularity.md"


def problems() -> list:
    out = []
    for row in CONTRACT["never_a_record"]:
        out += [f"unknown code {c} in never_a_record" for c in row.get("codes") or [] if c not in REGISTRY]
    for ex in CONTRACT["examples"]:
        out += [f"missing example {f}" for f in ex["files"] if not (ROOT / f).exists()]
    return out


def flagged(codes: list) -> str:
    return ", ".join(f"`{c}` ({REGISTRY[c][0]})" for c in codes) if codes else "not checked yet"


def render() -> str:
    c = CONTRACT
    lines = [BEGIN,
             f"*Generated from `data/record_contract.json` (version {c['version']}). Agents receive the same "
             f"contract from `GET /portal/api/contract`.*", "",
             "## One record is one result", "", c["definition"], "",
             "| Kind | `record_domain` | Example |", "|---|---|---|"]
    lines += [f"| {k['kind']} | `{k['record_domain']}` | {k['example']} |" for k in c["kinds"]]
    lines += ["", "## Deciding what goes in one record", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(c["procedure"], 1)]
    lines += ["", "## What is never a record", "", c["never_a_record_note"], "",
              "| Stored as | Why it fails | The ISAAC form | Flagged by the validator as |", "|---|---|---|---|"]
    lines += [f"| {r['stored_as']} | {r['why']} | {r['isaac_form']} | {flagged(r.get('codes') or [])} |"
              for r in c["never_a_record"]]
    lines += ["", "## Submitting records", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(c["submission"], 1)]
    lines += ["", "## Worked examples", "",
              "Each example is a complete record that passes the validator; CI checks every one on every change.", ""]
    lines += ["- **" + ex["case"] + "**: " + ", ".join(f"[{pathlib.Path(f).name}]({BLOB}{f})" for f in ex["files"])
              for ex in c["examples"]]
    lines += [END]
    return "\n".join(lines)


def main(argv: list) -> int:
    check = "--check" in argv
    args = [a for a in argv if a != "--check"]
    if not args:
        print(__doc__)
        return 2
    bad = problems()
    if bad:
        print("record contract is inconsistent: " + "; ".join(bad))
        return 1
    page = pathlib.Path(args[0]) / PAGE
    text = page.read_text()
    if BEGIN not in text or END not in text:
        print(f"{PAGE} has no {BEGIN} section")
        return 1
    new = text[:text.index(BEGIN)] + render() + text[text.index(END) + len(END):]
    if check:
        if new != text:
            print(f"STALE record contract section in {PAGE}: run tools/generate_record_contract.py")
            return 1
        print("record contract section up to date")
        return 0
    page.write_text(new)
    print(f"regenerated the record contract section of {PAGE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
