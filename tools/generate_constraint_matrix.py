#!/usr/bin/env python3
"""
Generate the ISAAC Constraint Matrix wiki page FROM the schema + validator, so
an agent (or a newcomer) can read ONE table to learn: what is valid, where each
field lives, and — critically — what TIER each rule is (error = blocks upload,
warning = accepted and teaches, info = suggests) and what SCOPE it has (every
record, vs only electrochemistry / only computation records).

ISAAC is a GENERIC scientific-record schema (a synthesis run, an XRD of a
pellet, a UV-Vis of a solution, a DFT slab — not only catalysis). The Scope
column exists so this never reads as "catalysis only": almost every hard rule is
domain-agnostic structure; the electrochemistry rules only fire when an EC
context is present and are invisible to a spectrum or a synthesis record.

The page can never drift: rule facts are ASSERTED against schema/validator at
generation time (a schema change that breaks an assertion fails the build), the
validation codes are imported from the same REGISTRY the codes table uses, and
the open-namespace list is derived live from the schema.

Usage:
  python3 tools/generate_constraint_matrix.py /path/to/wiki          # rewrite
  python3 tools/generate_constraint_matrix.py --check /path/to/wiki  # exit 1 if stale
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO / "schema" / "isaac_record_v1.json").read_text())
VALIDATION = (REPO / "portal" / "validation.py").read_text()

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_validation_docs import REGISTRY  # noqa: E402  (single source for code tiers)

BEGIN = "<!-- BEGIN GENERATED:constraint-matrix -->"
END = "<!-- END GENERATED:constraint-matrix -->"

# Scope of each validation code: "all records" (domain-agnostic) vs the domain
# layer that triggers it. Every REGISTRY code MUST appear here, so a newly added
# rule is forced to declare whether it touches non-EC records.
CODE_SCOPE = {
    "SIGN_CONVENTION": "electrochemistry",
    "WRONG_BLOCK": "electrochemistry",
    "MISSING_PH": "electrochemistry",
    "MISSING_ELECTRODE_TYPE": "electrochemistry",
    "GALVANOSTATIC_NO_POTENTIAL": "electrochemistry",
    "IMPLAUSIBLE_CURRENT_DENSITY": "electrochemistry",
    "FE_SUM_EXCEEDS_UNITY": "electrochemistry",
    "FE_ROLE_VIOLATION": "electrochemistry",
    "FE_SERIES_DUPLICATE": "electrochemistry",
    "NO_LINKS": "all records",
    "NO_DATA_OWNER": "all records",
    "QC_COMPROMISED_NO_EVIDENCE": "all records",
    "SIGMA_ZERO_PLACEHOLDER": "all records",
    "UNIT_NOT_IN_VOCABULARY": "all records",
}


def _assert(cond, msg):
    if not cond:
        raise SystemExit(f"constraint-matrix: schema/validator changed — {msg}. "
                         f"Update tools/generate_constraint_matrix.py.")


def _facts():
    """Pin the invariants this page asserts; fail loudly if the schema moved."""
    root_req = set(SCHEMA.get("required", []))
    _assert(root_req == {"isaac_record_version", "record_id", "record_type",
                         "record_domain", "source_type", "timestamps"},
            f"root required set is now {sorted(root_req)}")
    _assert(SCHEMA.get("additionalProperties") is False, "root is no longer closed")
    at = (SCHEMA["properties"]["descriptors"]["properties"]["outputs"]["items"]
          ["properties"]["descriptors"]["items"]["properties"]["at"])
    _assert(at.get("additionalProperties") is False, "descriptors…at is no longer closed")
    at_keys = set(at.get("properties", {}))
    _assert(len(at_keys) == 6, f"descriptors…at now has {len(at_keys)} keys: {sorted(at_keys)}")
    _assert("electrochemistry" not in SCHEMA["properties"]["context"].get("required", []),
            "context.electrochemistry is no longer optional")
    _assert(set(REGISTRY) == set(CODE_SCOPE),
            f"validation codes without a declared scope: {set(REGISTRY) - set(CODE_SCOPE)}")
    fe = re.findall(r"val < 0 or val > ([\d.]+)", VALIDATION)
    _assert(fe == ["1.5"], f"FE upper bound changed: {fe}")
    return sorted(at_keys)


def _open_namespaces():
    """Live-derive the object namespaces that accept arbitrary keys (any domain's
    extension points). additionalProperties != False on an object node."""
    opens = []

    def walk(node, path="record"):
        if not isinstance(node, dict):
            return
        is_obj = node.get("type") == "object" or "properties" in node
        if is_obj and node.get("additionalProperties") is not False and path != "record":
            opens.append(path)
        for k, v in node.get("properties", {}).items():
            walk(v, f"{path}.{k}")
        if node.get("type") == "array" and isinstance(node.get("items"), dict):
            walk(node["items"], f"{path}[]")

    walk(SCHEMA)
    return [p[len("record."):] for p in opens]


def render():
    at_keys = _facts()
    opens = _open_namespaces()

    # Structural rules verified above. (rule, scope, tier, meaning)
    structural = [
        ("Record carries `isaac_record_version, record_id, record_type, record_domain, "
         "source_type, timestamps`", "all records", "error",
         "The six always-required fields. None are domain-specific — a spectrum, a "
         "synthesis run and a DFT slab all need exactly these."),
        ("No unknown top-level blocks", "all records", "error",
         "The root is closed: only the 16 named blocks are allowed (sample, system, "
         "context, measurement, descriptors, computation, assets, links, attribution, "
         "tags, …). A typo or invented block is rejected."),
        ("`record_type=evidence` ⇒ `descriptors` present", "all records", "error",
         "An evidence record must assert at least one non-null scientific claim "
         "(any domain: a peak position, an edge energy, a yield, an FE)."),
        ("Descriptor `name` matches the naming grammar", "all records", "error",
         "`^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)*$`; no `_magnitude`/`_ratio.`/"
         "`_normalized`/`current_fraction.`/`.partial_sum_`. Applies to every "
         "descriptor name, electrochemical or not."),
        ("`record_id` and `links[].target` are 26-char ULIDs", "all records", "error",
         "`^[0-9A-Z]{26}$` — the record graph is domain-agnostic."),
        ("`attribution.contributors[].orcid` is a valid ORCID", "all records", "error",
         "`^\\d{4}-\\d{4}-\\d{4}-\\d{3}[0-9X]$` when an ORCID is given."),
        ("`tags[]` entries are non-empty, ≤64 chars, unique", "all records", "error",
         "Free-form grouping labels; the only constraint is the string shape."),
        (f"Descriptor `at:` uses only {{{', '.join('`'+k+'`' for k in at_keys)}}}",
         "all records", "error",
         "The per-point condition map is closed. `temperature_K`/`time_s`/`pressure_bar` "
         "are generic; the CO/glyoxal keys are electrochemistry extras. Sample identity "
         "and operating conditions do NOT go here — a different condition is a different record."),
        ("Faradaic-efficiency descriptor value ∈ `[0, 1.5]`", "electrochemistry", "error",
         "FE is a fraction; a value like 91 (percent-encoded) is rejected. Never touches "
         "a non-FE descriptor."),
        ("`context.electrochemistry.potential_vs_RHE` with a derived `rhe_basis` ⇒ "
         "`value_V` + `conversion` present", "electrochemistry", "error",
         "The Potential Contract: a derived potential must carry its provenance. Only "
         "relevant when an electrochemistry context exists."),
    ]

    order = {"error": 0, "warning": 1, "info": 2}
    scope_order = {"all records": 0, "electrochemistry": 1, "computation": 2}

    code_rows = [(f"`{c}`", CODE_SCOPE[c], REGISTRY[c][0], REGISTRY[c][1]) for c in REGISTRY]
    rows = structural + code_rows
    rows.sort(key=lambda r: (order[r[2]], scope_order.get(r[1], 3), r[0]))

    L = [BEGIN,
         "## Constraint matrix (generated from `schema/isaac_record_v1.json` + `portal/validation.py`)",
         "",
         "> Generated — do not hand-edit between the markers; CI fails on drift. **Tier:** "
         "`error` blocks the upload (HTTP 400); `warning` is accepted (201) and teaches; "
         "`info` suggests. **Scope:** `all records` is the domain-agnostic spine that a "
         "synthesis run, an XRD/XPS/UV-Vis spectrum and a DFT record all obey; "
         "`electrochemistry` / `computation` rules fire ONLY when that optional context is "
         "present and are invisible to records that don't use it.",
         "",
         "| Rule | Scope | Tier | Meaning |",
         "|---|---|---|---|"]
    for rule, scope, tier, meaning in rows:
        L.append(f"| {rule} | {scope} | {tier} | {meaning} |")
    L.append("")
    L.append("### Open extension points (any domain may add its own keys here)")
    L.append("")
    L.append("These object namespaces are **not** closed — put domain-specific fields here "
             "and the validator will not reject them. This is how the schema stays generic: "
             "the spine is fixed, the leaves are yours. (Recurring keys in the current corpus "
             "are documented on the relevant block page; reuse an existing key before inventing one.)")
    L.append("")
    for p in opens:
        L.append(f"- `{p}`")
    L.append(END)
    return "\n".join(L)


def apply(page: Path):
    block = render()
    text = page.read_text() if page.exists() else (
        "# Constraint Matrix\n\nOne legible, generated view of every validation rule, its "
        "enforcement tier, and its scope. ISAAC is a generic scientific-record schema; the "
        "Scope column shows how few rules are domain-specific.\n\n")
    if BEGIN in text and END in text:
        return re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    return text.rstrip() + "\n\n" + block + "\n"


def main():
    check = "--check" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--check"]
    wiki = Path(args[0]) if args else REPO.parent / "isaac-ai-ready-record.wiki"
    page = wiki / "Constraint-Matrix.md"
    desired = apply(page)
    if check:
        if not page.exists() or page.read_text() != desired:
            print("STALE: Constraint-Matrix.md out of sync — run tools/generate_constraint_matrix.py")
            return 1
        print("constraint matrix up to date")
        return 0
    page.write_text(desired)
    print(f"regenerated Constraint-Matrix.md ({len(REGISTRY)} codes + structural rules)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
