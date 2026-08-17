#!/usr/bin/env python3
"""Fail if the discovery contract teaches anybody's specific case — above all, ours.

WHY THIS IS A HARD GATE. The manifest is the FIRST thing every agent fetches, before it has
seen a project. For five weeks it carried two worked examples that were, verbatim, registered
predictions from the Cu-Au project the reproducibility programme benchmarks on:

    reference_condition:'80%→89% Cu (vs the 80% peak)'
    output_quantity:'ΔE_CO(Au-adjacent) − ΔE_CO(pure-Cu)'

Nobody copied answers into the contract. When `field_shapes` was written the Cu-Au project was
the only discovery project that existed, so it was what got used to illustrate the shapes, and
the benchmark was carved out of that same project five weeks later. Shared ancestry, not theft
— and the effect on a reproducibility study is identical either way. An instrument that hands
the reader part of the answer sheet is not an instrument.

WHAT THIS CHECKS, AND WHAT IT DELIBERATELY DOES NOT. It blocks the material systems under
benchmark, the frozen-set item identifiers, and the numbers that belong to those cases. It does
NOT block domain vocabulary. `adsorption_energy`, `faradaic_efficiency`, `xanes` and
`band_gap` are what the repository stores; a contract for a catalysis pathfinder may say so.
The line is between a VOCABULARY, which describes what can be recorded, and a CASE, which
tells the reader what answer to expect.

Adding a benchmark system means adding it here in the same commit. That is the point: the guard
should get stricter as the programme takes on more cases, never looser.

    python3 tools/check_manifest_generic.py            # exits 1 on any hit
"""
import json
import pathlib
import re
import sys

# discovery.py imports its siblings by bare name, so the PACKAGE dir goes on the path,
# not the repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "portal"))

# Systems the reproducibility programme measures on. A contract that names one of these is
# teaching the answer to a case we are grading. Extend when a case is added.
BENCHMARKED_SYSTEMS = [
    r"Cu[-–|/ ]?Au", r"Au[-–|/ ]?Cu", r"Cu[-–|/ ]?Ag", r"Ag[-–|/ ]?Cu",
    r"pure[- ]?Cu", r"pure[- ]?Au", r"pure[- ]?Ag", r"Au-adjacent", r"Cu\(100\)",
]

# Identifiers and numbers that belong to the frozen sets and to no one else's project.
CASE_ARTEFACTS = [
    r"\bV1-\d{3}\b", r"\bV2-I\d+\b",                     # frozen-set item ids
    r"H[1-5]-(CO|INTERFACE|RESIDUAL|LOCAL)[A-Z-]*",       # our hypothesis labels
    r"80\s*%?\s*(→|->|to)\s*89\s*%", r"89pct",             # the Cu-Au composition step
    r"\b23\.96\b", r"\b15\.13\b",                         # the Cu-Au selectivity numbers
    r"0\.83 ['\"]?reliable",                              # the Cu-Ag over-credit incident
    r"the Cu-A[gu] lesson",
]

PATTERNS = [("benchmarked system", p) for p in BENCHMARKED_SYSTEMS] + \
           [("case artefact", p) for p in CASE_ARTEFACTS]

# `field_shapes` is where an agent learns to WRITE a prediction, so it is the one block that
# must name no chemistry at all. A worked example there is copied; a worked example there in
# our case's numbers is the answer sheet. Stricter rule, scoped to that subtree.
SHAPE_ONLY = [
    (r"\b(?:Cu|Au|Ag|Pt|Pd|Ni|Fe|Co|Ir|Ru|Rh|Zn|Sn|Bi)\b", "an element symbol"),
    (r"\b\d{1,3}\s*%\s*(?:Cu|Au|Ag|[A-Z][a-z]?)\b", "a specific composition"),
    (r"\bCO2RR\b|\bCORR\b|\bOER\b|\bHER\b|\bORR\b", "a specific reaction"),
]


def leaves(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from leaves(v, "%s.%s" % (path, k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from leaves(v, "%s[%d]" % (path, i))
    elif isinstance(x, str):
        yield path, x


def main():
    import discovery
    man = discovery.get_manifest()
    hits = []
    for path, text in leaves(man):
        rules = list(PATTERNS)
        if path.startswith(".field_shapes"):
            rules += [("in field_shapes: " + why, pat) for pat, why in SHAPE_ONLY]
        for kind, pat in rules:
            for m in re.finditer(pat, text, re.I if "field_shapes" not in kind else 0):
                a, b = max(0, m.start() - 60), min(len(text), m.end() + 80)
                hits.append((kind, path, m.group(0), text[a:b].replace("\n", " ")))

    n = len(list(leaves(man)))
    if not hits:
        print("manifest %s: %d leaf strings, none names a benchmarked case"
              % (man.get("version"), n))
        return 0

    print("MANIFEST TEACHES A SPECIFIC CASE — %d hit(s) across %d leaf strings\n" % (len(hits), n))
    for kind, path, tok, ctx in hits:
        print("  [%s] %s" % (kind, path))
        print("      matched %r" % tok)
        print("      …%s…\n" % ctx)
    print("The contract is the first thing every agent reads. It may name a vocabulary; it may")
    print("not name a case we grade. Rewrite the example in a domain the programme does not")
    print("benchmark, or add the system to BENCHMARKED_SYSTEMS if it is genuinely new.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
