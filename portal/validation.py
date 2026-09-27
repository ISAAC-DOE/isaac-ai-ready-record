"""
ISAAC AI-Ready Record — unified validation.

THE single source of truth for record validation. Every ingestion path —
the REST API, the Streamlit validator page, the Streamlit record form,
and any future tool — validates through this module. The enforcement
point is database.save_record(), which calls validate_record_full()
internally and refuses to persist a failing record, so a new upload path
added later is guarded automatically even if its author forgets to
validate.

To change what validation does, change it here (or in the schema /
vocabulary files this module loads). All upload paths pick up the change
simultaneously.

Layers:
  1. JSON Schema  (schema/isaac_record_v1.json, Draft 2020-12)
  2. Vocabulary   (ontology.validate_record_vocabulary — living vocabulary)
  3. Semantic     (ontology.validate_semantic_integrity — cross-field rules)
"""

import json
import logging
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

# Make sibling modules importable regardless of caller CWD (same pattern
# as api.py / app.py, which run with different working directories).
_portal_dir = Path(__file__).resolve().parent
if str(_portal_dir) not in sys.path:
    sys.path.insert(0, str(_portal_dir))

import ontology  # noqa: E402

logger = logging.getLogger("isaac-validation")

# ---------------------------------------------------------------------------
# Schema (loaded once at import)
# ---------------------------------------------------------------------------
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "isaac_record_v1.json"
with open(SCHEMA_PATH) as f:
    ISAAC_SCHEMA = json.load(f)

# FIX (2026-06-11): FormatChecker was never passed, so every `format:
# "date-time"` in the schema was decorative — empty strings and
# space-separated timestamps passed. Requires the rfc3339-validator
# package (in requirements.txt) for date-time to actually be checked;
# we assert its presence so the enforcement can never silently vanish.
import rfc3339_validator  # noqa: F401  (presence assertion — see above)
ISAAC_VALIDATOR = Draft202012Validator(ISAAC_SCHEMA, format_checker=FormatChecker())

# ---------------------------------------------------------------------------
# Canonical forms (Decisions A & B, 2026-06-11) — loaded from the vocabulary
# single source of truth. Deprecated unit spellings and product tokens are
# REJECTED with a message naming the canonical replacement.
# ---------------------------------------------------------------------------
VOCAB_PATH = Path(__file__).resolve().parent.parent / "data" / "vocabulary.json"
try:
    with open(VOCAB_PATH) as f:
        _VOCAB = json.load(f)
    UNIT_ALIASES = _VOCAB.get("Units", {}).get("units.aliases", {}).get("map", {})
    PRODUCT_ALIASES = _VOCAB.get("Descriptors", {}).get("descriptors.product_aliases", {}).get("map", {})
except Exception as _exc:  # degrade gracefully; canonical checks become no-ops
    logger.warning("Could not load canonical-form maps from %s: %s", VOCAB_PATH, _exc)
    UNIT_ALIASES, PRODUCT_ALIASES = {}, {}

PRODUCT_CLASS_PREFIXES = (
    "faradaic_efficiency.", "partial_current_density.", "production_rate.",
    "initial_faradaic_efficiency.", "final_faradaic_efficiency.",
)

# Canonical product tokens (Decision B). Unknown tokens (typos, ad-hoc
# inventions) are rejected; known aliases get a rename message instead.
try:
    CANONICAL_PRODUCTS = set(
        _VOCAB.get("Descriptors", {})
        .get("descriptors.faradaic_efficiency_products", {})
        .get("values", [])
    )
except Exception:
    CANONICAL_PRODUCTS = set()

# Grandfathered non-product suffixes pending a wave-2 decision (derived
# metric stored as a token). Documented in the improvement plan.
GRANDFATHERED_PRODUCT_TOKENS = {"ratio_CH4_to_C2plus"}


def _canonical_form_errors(record: dict) -> list:
    """
    Enforce Decision A (slash-form unit grammar) and Decision B (formula-style
    product tokens): any unit string or product-token suffix found in the
    deprecation maps is an error pointing at the canonical replacement.
    """
    errors = []

    def unit_err(path, u):
        return {"path": path,
                "message": f"Unit '{u}' is a deprecated alias; use canonical "
                           f"'{UNIT_ALIASES[u]}' (slash-form unit grammar, see "
                           f"Controlled-Vocabulary wiki)."}

    outputs = (record.get("descriptors") or {}).get("outputs") or []
    for oi, o in enumerate(outputs):
        for di, d in enumerate(o.get("descriptors") or [] if isinstance(o, dict) else []):
            nm = d.get("name", "") or ""
            for p in PRODUCT_CLASS_PREFIXES:
                if nm.startswith(p):
                    suffix = nm[len(p):]
                    if suffix in PRODUCT_ALIASES:
                        errors.append({
                            "path": f"descriptors/outputs/{oi}/descriptors/{di}/name",
                            "message": f"Product token '{suffix}' is a deprecated alias; "
                                       f"use canonical '{PRODUCT_ALIASES[suffix]}' "
                                       f"(formula-style tokens, see Controlled-Vocabulary wiki).",
                        })
                    elif (CANONICAL_PRODUCTS
                          and suffix not in CANONICAL_PRODUCTS
                          and suffix not in GRANDFATHERED_PRODUCT_TOKENS):
                        errors.append({
                            "path": f"descriptors/outputs/{oi}/descriptors/{di}/name",
                            "message": f"Product token '{suffix}' is not in the canonical "
                                       f"product vocabulary (descriptors.faradaic_efficiency_products). "
                                       f"If this is a genuinely new product, request a vocabulary "
                                       f"addition; do not invent tokens.",
                        })
                    break
            # FE physics: fraction-encoded values must be physical. A value
            # above 1.5 is almost certainly percent-encoded in a fraction
            # field (e.g. 91 instead of 0.91).
            if nm.startswith(("faradaic_efficiency.", "total_faradaic_efficiency")):
                val = d.get("value")
                if isinstance(val, (int, float)) and (val < 0 or val > 1.5):
                    errors.append({
                        "path": f"descriptors/outputs/{oi}/descriptors/{di}/value",
                        "message": f"Faradaic efficiency value {val} is outside [0, 1.5]. "
                                   f"FE is a fraction (0-1); values like 91 are percent-encoded "
                                   f"— divide by 100.",
                    })
            u = d.get("unit")
            if u in UNIT_ALIASES:
                errors.append(unit_err(f"descriptors/outputs/{oi}/descriptors/{di}/unit", u))
            uu = (d.get("uncertainty") or {}).get("unit")
            if uu in UNIT_ALIASES:
                errors.append(unit_err(f"descriptors/outputs/{oi}/descriptors/{di}/uncertainty/unit", uu))

    series = (record.get("measurement") or {}).get("series") or []
    for si, s in enumerate(series):
        for kind in ("channels", "independent_variables"):
            for ci, ch in enumerate(s.get(kind) or []):
                u = ch.get("unit")
                if u in UNIT_ALIASES:
                    errors.append(unit_err(f"measurement/series/{si}/{kind}/{ci}/unit", u))

    return errors


class ValidationError(Exception):
    """
    Raised by the persistence chokepoint (database.save_record) when a
    record fails validation. Carries the full structured result so callers
    can render per-layer errors.
    """

    def __init__(self, result: dict):
        self.result = result
        n = len(result.get("errors", []))
        super().__init__(f"Record failed ISAAC validation with {n} error(s)")


def _potential_contract_errors(record: dict) -> list:
    """
    Canonical Potential Contract (2026-06-12):
    1. potential_scale naming a physical electrode requires the structured
       reference_electrode block (with a numeric offset for convertibility).
    2. For derived rhe_basis values, the stored value_V must match the
       recomputation from its own frozen conversion inputs within 5 mV —
       provenance and value can never silently drift apart.
    """
    errors = []
    ec = ((record.get("context") or {}).get("electrochemistry") or {})
    if not isinstance(ec, dict):
        return errors

    scale = ec.get("potential_scale")
    if scale in ("Ag/AgCl", "SCE", "Hg/HgO", "Hg/HgSO4"):
        ref = ec.get("reference_electrode")
        if not isinstance(ref, dict) or not ref.get("type"):
            errors.append({
                "path": "context/electrochemistry/reference_electrode",
                "message": f"potential_scale '{scale}' names a physical reference electrode; the structured "
                           f"reference_electrode block (type, filling_solution, offset_V_vs_SHE) is required "
                           f"so the measurement is convertible (Potential Contract).",
            })
        elif ref.get("type") != scale:
            errors.append({
                "path": "context/electrochemistry/reference_electrode/type",
                "message": f"reference_electrode.type '{ref.get('type')}' must equal potential_scale '{scale}'.",
            })

    pvr = ec.get("potential_vs_RHE")
    if isinstance(pvr, dict) and pvr.get("rhe_basis") in ("derived_calibrated", "derived_nominal"):
        conv = pvr.get("conversion") or {}
        val = pvr.get("value_V")
        src = ec.get("potential_setpoint_V")
        cal = conv.get("rhe_conversion_offset_V")
        off = conv.get("offset_V_vs_SHE_used")
        ph = conv.get("pH_used")
        recomputed = None
        label = None
        if isinstance(val, (int, float)) and isinstance(src, (int, float)):
            if isinstance(cal, (int, float)):
                # Calibrated single-constant path. The constant bundles reference
                # offset + Nernst pH term + electrode drift (no separate pH term).
                # SIGN is taken from the stated formula so raw source values are
                # preserved (e.g. Caltech reports a NEGATIVE offset with a
                # SUBTRACTIVE formula). Default additive when the formula is silent.
                fml = str(conv.get("formula", "")).lower().replace(" ", "")
                subtractive = ("-rhe_conversion_offset" in fml
                               or "e_measured-" in fml or "e_meas-" in fml)
                if subtractive:
                    recomputed = src - cal
                    label = f"E_measured({src}) - rhe_conversion_offset_V({cal})"
                else:
                    recomputed = src + cal
                    label = f"E_measured({src}) + rhe_conversion_offset_V({cal})"
            elif isinstance(off, (int, float)) and isinstance(ph, (int, float)):
                # Nominal path: offset vs SHE + Nernst slope * pH.
                slope = 0.05916 if "0.05916" in str(conv.get("formula", "")) else 0.0591
                recomputed = src + off + slope * ph
                label = f"{src} + {off} + {slope}*{ph}"
        # 5 mV tolerance absorbs source-side rounding of value_V while still
        # catching genuine value/provenance drift (which is tens of mV).
        if recomputed is not None and abs(recomputed - val) > 0.005:
            errors.append({
                "path": "context/electrochemistry/potential_vs_RHE/value_V",
                "message": f"Derived value_V={val} does not match recomputation from its own conversion "
                           f"inputs ({label} = {recomputed:.4f}); tolerance 5 mV. Provenance and value must "
                           f"agree (Potential Contract). The recompute follows the SIGN in conversion.formula "
                           f"(E_measured + offset, or E_measured - offset) — keep value_V, offset, and formula "
                           f"mutually consistent.",
            })
    return errors


# ---------------------------------------------------------------------------
# Descriptor names and read-out conditions (2026-09-27).
#
# A descriptor name says WHAT quantity is claimed; nothing else. The reaction lives in
# context, the technique in system.technique, experimental vs computational in
# system.domain, and the condition at which a quantity was read (a current density, a
# potential, a temperature, a time) in the descriptor's `at`. When those facts are
# written into names instead, every uploader invents a new name for the same quantity
# (the 2026-09-27 repository audit counted 813 names for 5,852 values, 10% canonical),
# and an agent asking for "the overpotential at 10 mA/cm2" finds nothing. Which tokens
# are forbidden prefixes, which classes are canonical and which unit each class carries
# are DATA in data/vocabulary.json, so the wiki renders the same lists this code enforces.
# ---------------------------------------------------------------------------
def _vocab_values(section: str, key: str) -> list:
    return list(((_VOCAB.get(section) or {}).get(key) or {}).get("values") or [])


def _vocab_map(section: str, key: str) -> dict:
    return dict(((_VOCAB.get(section) or {}).get(key) or {}).get("map") or {})


CLASS_UNITS = _vocab_map("Descriptors", "descriptors.class_units")
CLASS_ALIASES = _vocab_map("Descriptors", "descriptors.class_aliases")
NAME_PREFIX_TOKENS = sorted(_vocab_values("Descriptors", "descriptors.name_prefix_tokens"),
                            key=len, reverse=True)
CANONICAL_CLASSES = set(CLASS_UNITS)
for _k in ("descriptors.electrochemical_performance", "descriptors.catalytic_performance",
           "descriptors.spectroscopy", "descriptors.structure", "descriptors.theoretical",
           "descriptors.theoretical_metric"):
    CANONICAL_CLASSES.update(_vocab_values("Descriptors", _k))
CANONICAL_CLASSES.update(p.rstrip(".") for p in PRODUCT_CLASS_PREFIXES)

# `at` keys that state the point on a SWEEP at which a quantity was read.
READOUT_AT_KEYS = ("current_density_mA_cm2", "current_density_ECSA_mA_cm2", "potential_V_RHE")
SWEEP_CONTROL_MODES = {"potentiodynamic", "mixed"}

_CONDITION_PATTERNS = (
    ("an '_at_' operating point", re.compile(r"(?:^|[._])at_", re.I)),
    ("a current density", re.compile(
        r"\d+(?:p\d+)?_?m?a_?cm_?2|(?:^|[._])\d+(?:p\d+)?ma(?:$|[._])", re.I)),
    ("a potential", re.compile(
        r"(?:minus|neg)?\d+p\d+_?v(?:_?rhe)?(?:$|[._])|\d+(?:p\d+)?_?v_?rhe|(?:^|[._])\d+mv(?:$|[._])",
        re.I)),
    ("a temperature", re.compile(r"(?:^|[._])\d{2,4}_?(?:c|k|degc)(?:$|[._])", re.I)),
    ("a time", re.compile(
        r"(?:^|[._])\d+(?:p\d+)?_?(?:s|sec|min|h|hr|hours?)(?:$|[._])", re.I)),
)
_LOWER_CLASS = re.compile(r"^[a-z][a-z0-9_]*$")


def _strip_conditions(stem: str) -> str:
    """Remove condition fragments from a class stem, for rename suggestions only."""
    stem = re.split(r"_at_", stem, maxsplit=1, flags=re.I)[0]
    for _label, rx in _CONDITION_PATTERNS:
        stem = rx.sub("_", stem)
    return re.sub(r"_+", "_", stem).strip("_")


def _suggest_class(stem: str):
    """A canonical rename for a stem whose prefix/conditions were removed, or None."""
    low = stem.lower()
    if low in CANONICAL_CLASSES:
        return low
    for c in sorted(CANONICAL_CLASSES, key=len, reverse=True):
        if low.endswith("_" + c):
            return f"{c}.{stem[: -len(c) - 1]}"
        if low.startswith(c + "_"):
            return f"{c}.{stem[len(c) + 1:]}"
    return None


def _descriptor_name_errors(record: dict) -> list:
    errors = []
    ec = ((record.get("context") or {}).get("electrochemistry") or {})
    control = ec.get("control_mode") if isinstance(ec, dict) else None
    outputs = (record.get("descriptors") or {}).get("outputs") or []
    for oi, o in enumerate(outputs):
        seen = {}
        for di, d in enumerate(o.get("descriptors") or [] if isinstance(o, dict) else []):
            if not isinstance(d, dict):
                continue
            name = d.get("name") or ""
            path = f"descriptors/outputs/{oi}/descriptors/{di}"
            stem = name.split(".")[0]
            low = stem.lower()

            if name in seen:
                errors.append({
                    "code": "DUPLICATE_DESCRIPTOR_NAME", "path": f"{path}/name",
                    "message": (f"Descriptor '{name}' appears twice in output block {oi} (positions "
                                f"{seen[name]} and {di}). One name, one value per block: a second value of "
                                f"the same quantity is a different condition (state it in `at`) or a "
                                f"different record, never a duplicate.")})
            seen.setdefault(name, di)

            alias = CLASS_ALIASES.get(name) or CLASS_ALIASES.get(stem)
            if alias:
                errors.append({
                    "code": "DESCRIPTOR_CLASS_ALIAS", "path": f"{path}/name",
                    "message": (f"Descriptor '{name}' uses a deprecated spelling; the canonical class is "
                                f"'{alias}' (descriptors.class_aliases, Descriptors wiki).")})
                continue

            for p in NAME_PREFIX_TOKENS:
                if low == p or low.startswith(p + "_"):
                    rest = stem[len(p) + 1:] if low != p else ""
                    remainder = (rest + name[len(stem):]) if rest else name[len(stem) + 1:]
                    rest_stem = remainder.split(".")[0]
                    sugg = _suggest_class(_strip_conditions(rest_stem)) if rest_stem else None
                    if sugg and "." in remainder and "." not in sugg:
                        sugg = sugg + remainder[len(rest_stem):]
                    hint = (f"Use '{sugg}'." if sugg else
                            "Rename it to a canonical class (Descriptors wiki, section 8) or request a "
                            "vocabulary addition.")
                    errors.append({
                        "code": "PREFIX_IN_DESCRIPTOR_NAME", "path": f"{path}/name",
                        "message": (f"Descriptor '{name}' begins with '{p}', a reaction, technique or method "
                                    f"token. Those facts have structured homes (context.electrochemistry."
                                    f"reaction, system.technique, system.domain), so the name carries only "
                                    f"the quantity. {hint}")})
                    break

            for label, rx in _CONDITION_PATTERNS:
                if rx.search(name):
                    base = _suggest_class(_strip_conditions(stem)) or _strip_conditions(stem)
                    errors.append({
                        "code": "CONDITION_IN_DESCRIPTOR_NAME", "path": f"{path}/name",
                        "message": (f"Descriptor '{name}' writes {label} into its name. Conditions are "
                                    f"structured data: put the read-out point in the descriptor's `at` "
                                    f"(current_density_mA_cm2, current_density_ECSA_mA_cm2, potential_V_RHE, "
                                    f"temperature_K, time_s, pressure_bar, ...) or in context, and keep the "
                                    f"name to the quantity, e.g. '{base or 'overpotential'}' with "
                                    f"at.current_density_mA_cm2 = 10. A second condition is a second "
                                    f"descriptor or a second record, never a longer name.")})
                    break

            if name and not _LOWER_CLASS.match(stem):
                errors.append({
                    "code": "DESCRIPTOR_CLASS_NOT_LOWERCASE", "path": f"{path}/name",
                    "message": (f"Descriptor '{name}': the class (the part before the first dot) must be "
                                f"lowercase letters, digits and underscores. Element, species or layer "
                                f"labels are qualifiers after the dot: 'layer_thickness.Cu', not "
                                f"'Cu_thickness'; 'oxidation_state.Cu', not 'Cu_oxidation_state'.")})

            val = d.get("value")
            unit = d.get("unit")
            if (stem in CLASS_UNITS and isinstance(val, (int, float)) and not isinstance(val, bool)
                    and unit not in CLASS_UNITS[stem] and unit not in UNIT_ALIASES):
                errors.append({
                    "code": "CLASS_UNIT_MISMATCH", "path": f"{path}/unit",
                    "message": (f"Descriptor '{name}' is in '{unit}', but class '{stem}' is always reported "
                                f"in {CLASS_UNITS[stem]} (descriptors.class_units). Convert the value; do "
                                f"not relabel the unit. One class has one unit, so values from different "
                                f"records compare without conversion.")})

            at = d.get("at") if isinstance(d.get("at"), dict) else {}
            used = [k for k in READOUT_AT_KEYS if k in at]
            if used and control not in SWEEP_CONTROL_MODES:
                errors.append({
                    "code": "AT_READOUT_WITHOUT_SWEEP", "path": f"{path}/at",
                    "message": (f"Descriptor '{name}' states a read-out point {used} in `at`, but this "
                                f"record's control_mode is {control!r}. Read-out keys locate a value on a "
                                f"SWEEP (control_mode 'potentiodynamic'): 'the overpotential at 10 mA/cm2', "
                                f"'the mass activity at 0.9 V_RHE'. A record held at one potential or one "
                                f"current states it once, in context.electrochemistry "
                                f"(potential_setpoint_V + potential_vs_RHE, or current_setpoint_mA_cm2).")})
    return errors


# ---------------------------------------------------------------------------
# The reaction (2026-09-27). One home for every kind of chemistry.
#
# The reaction used to live only inside context.electrochemistry, so a thermal, photo-,
# homogeneous or enzymatic reaction could not be stated at all: 198 performance records
# (NH3 synthesis, dry reforming, CO oxidation, Li-S cathodes, ...) carried none. It now
# lives in context.reaction {name, drive, catalysis}, required on performance records.
# context.electrochemistry.reaction is deprecated; stored records keep validating on
# it, and every read path accepts both, preferring the new home.
# ---------------------------------------------------------------------------
ELECTROCHEMICAL_DRIVES = {"electrochemical", "photoelectrochemical"}


def _reaction_name(record: dict):
    """context.reaction.name, else the deprecated context.electrochemistry.reaction."""
    ctx = record.get("context") or {}
    rx = ctx.get("reaction")
    if isinstance(rx, dict) and rx.get("name"):
        return rx["name"]
    ec = ctx.get("electrochemistry")
    legacy = ec.get("reaction") if isinstance(ec, dict) else None
    return None if legacy in (None, "None") else legacy


def _reaction_checks(record: dict):
    """(errors, warnings) for the context.reaction contract."""
    errors, warnings = [], []
    ctx = record.get("context") or {}
    rx = ctx.get("reaction") if isinstance(ctx.get("reaction"), dict) else None
    ec = ctx.get("electrochemistry") if isinstance(ctx.get("electrochemistry"), dict) else None
    legacy = (ec or {}).get("reaction")
    legacy = None if legacy in (None, "None") else legacy

    if record.get("record_domain") == "performance" and not rx:
        move = (f" This record states it only in the deprecated context.electrochemistry.reaction; move it: "
                f"context.reaction = {{name: '{legacy}', drive: 'electrochemical', catalysis: 'heterogeneous'}}."
                if legacy else "")
        errors.append({
            "code": "MISSING_REACTION", "path": "context/reaction",
            "message": ("A performance record reports how well something performs a reaction, so it must say "
                        "which: context.reaction {name, drive, catalysis}. name is a token from "
                        "context.reaction.name (CO2RR, OER, NH3_synthesis, CO_oxidation, methanol_synthesis, ...); "
                        "drive is electrochemical, thermal, photochemical, photoelectrochemical, plasma, "
                        "mechanochemical or biochemical; catalysis is heterogeneous, homogeneous, enzymatic or "
                        "uncatalyzed. A reaction missing from the list is added through a vocabulary proposal, "
                        "never invented in a record." + move)})
    if rx and legacy and legacy != rx.get("name"):
        errors.append({
            "code": "REACTION_MISMATCH", "path": "context/electrochemistry/reaction",
            "message": (f"context.reaction.name is '{rx.get('name')}' but the deprecated "
                        f"context.electrochemistry.reaction says '{legacy}'. A record has one reaction; remove "
                        f"the deprecated field.")})
    if legacy:
        warnings.append({
            "code": "REACTION_FIELD_DEPRECATED", "path": "context/electrochemistry/reaction",
            "message": ("context.electrochemistry.reaction is deprecated: the reaction lives in context.reaction "
                        "{name, drive, catalysis}, which serves every kind of chemistry. Move it there and remove "
                        "this field.")})
    if rx:
        drive = rx.get("drive")
        if drive in ELECTROCHEMICAL_DRIVES and not ec:
            errors.append({
                "code": "REACTION_DRIVE_INCONSISTENT", "path": "context/electrochemistry",
                "message": (f"context.reaction.drive is '{drive}' but the record has no context.electrochemistry "
                            f"block. An electrochemical reaction is stated with its cell: control_mode, cell_type, "
                            f"electrolyte and the applied potential or current (Context wiki, 3.3).")})
        if ec and ec.get("control_mode") and drive and drive not in ELECTROCHEMICAL_DRIVES:
            errors.append({
                "code": "REACTION_DRIVE_INCONSISTENT", "path": "context/reaction/drive",
                "message": (f"The record declares an electrochemical control_mode "
                            f"('{ec.get('control_mode')}') but context.reaction.drive is '{drive}'. Set drive to "
                            f"electrochemical (or photoelectrochemical under illumination).")})
    return errors, warnings


# ---------------------------------------------------------------------------
# A record is knowledge, not reasoning (2026-09-27).
#
# The repository holds records of knowledge: what was measured, computed or reported, on
# what, under which conditions, by whom, from which source. Hypotheses, verdicts, the use a
# record was collected for, and benchmark or case bookkeeping belong to whatever USES the
# records, outside the repository. On 2026-09-27, 173 records carried such text ("decisive
# against H4", "tests the residual's central claim", "the local frozen-set record
# (case_20-LIT-5004)") and 436 carried benchmark item ids as tags; none of the other 1,776
# records matched these patterns. Paper quotes stored in assets are source text and are
# not scanned.
# ---------------------------------------------------------------------------
_REASONING_PATTERNS = (
    ("a hypothesis label", re.compile(
        r"\b(?:hypothes[ie]s|mechanism|explanation)\s+H[1-9]\b"
        r"|\bH[1-9]\s*(?:['\u2019]s\b|is\s+(?:supported|refuted|favou?red|disfavou?red|scored|decisive)|would\b|predicts?\b)"
        r"|\b(?:against|supports?|refutes?|contradicts?|favou?rs?|disfavou?rs?|keeps?|scored\s+as)\s+H[1-9]\b", re.I)),
    ("a reference to a competing hypothesis", re.compile(
        r"\bthe\s+residual(?:['\u2019]s)?\s+(?:hypothesis|claim|mechanism|explanation|central)\b"
        r"|\bresidual\s+hypothesis\b", re.I)),
    ("benchmark machinery", re.compile(
        r"\b(?:frozen[- ]set|cold[- ]seat|answer[- ]key|gold[- ](?:set|verdict|key)|benchmark\s+(?:case|item|question)"
        r"|wave[- ]\d+\s+(?:seat|run|agent)s?)\b", re.I)),
    ("a benchmark case or item identifier", re.compile(r"\bcase_\d+\b|\bLIT-\d{3,4}\b", re.I)),
    ("a verdict", re.compile(r"\bdecisive\s+(?:against|for|record|test|evidence)\b", re.I)),
    ("the purpose the record serves", re.compile(
        r"\b(?:this|the)\s+record\s+(?:tests|supports|refutes|contradicts|is\s+decisive|was\s+(?:chosen|included|selected)"
        r"|matters\s+because|is\s+included)\b", re.I)),
)
_WORKFLOW_TAG = re.compile(
    r"^(?:case[_-]?\d+.*|.*\blit-\d{3,}.*|h\d|.*hypothes.*|.*frozen.*|.*answer[-_]key.*|seat[-_][a-f])$", re.I)


def _curated_text_fields(record: dict):
    """(path, text) for the free-text fields a curator writes (not quotes of a source)."""
    out = []
    mat = ((record.get("sample") or {}).get("material") or {})
    out += [("sample/material/name", mat.get("name")), ("sample/material/notes", mat.get("notes"))]
    qc = ((record.get("measurement") or {}).get("qc") or {})
    out += [("measurement/qc/notes", qc.get("notes")), ("measurement/qc/evidence", qc.get("evidence"))]
    for oi, o in enumerate((record.get("descriptors") or {}).get("outputs") or []):
        for di, d in enumerate(o.get("descriptors") or [] if isinstance(o, dict) else []):
            if isinstance(d, dict):
                out.append((f"descriptors/outputs/{oi}/descriptors/{di}/definition", d.get("definition")))
    ctx = record.get("context") or {}
    ec = ctx.get("electrochemistry") if isinstance(ctx.get("electrochemistry"), dict) else {}
    rx = ctx.get("reaction") if isinstance(ctx.get("reaction"), dict) else {}
    out += [("context/electrochemistry/notes", ec.get("notes")),
            ("context/electrochemistry/reaction_notes", ec.get("reaction_notes")),
            ("context/reaction/notes", rx.get("notes"))]
    return [(p, t) for p, t in out if isinstance(t, str) and t]


def _record_content_errors(record: dict) -> list:
    errors = []
    for path, text in _curated_text_fields(record):
        for label, rx in _REASONING_PATTERNS:
            m = rx.search(text)
            if m:
                excerpt = text[max(0, m.start() - 40): m.end() + 40].replace("\n", " ")
                errors.append({
                    "code": "REASONING_IN_RECORD", "path": path,
                    "message": (f"This field contains {label} ('...{excerpt}...'). A record is knowledge: what "
                                f"was measured, computed or reported, on what, under which conditions, by whom, "
                                f"from which source. Hypotheses, verdicts, the use the record was collected for, "
                                f"and benchmark or case identifiers belong to whatever uses the record, outside "
                                f"the repository. Rewrite the field to describe the data only.")})
                break
    for i, t in enumerate(record.get("tags") or []):
        if isinstance(t, str) and _WORKFLOW_TAG.match(t):
            errors.append({
                "code": "TAG_ENCODES_USE", "path": f"tags/{i}",
                "message": (f"Tag '{t}' names how the record is used (a benchmark case or item, a hypothesis), not "
                            f"what the data is. Tags group data: a dataset, a campaign, a material system, a "
                            f"facility, a publication (e.g. 'jcap-hte', 'xu-2026-cuag-stripes'). Keep workflow "
                            f"bookkeeping outside the repository.")})
    return errors


# ---------------------------------------------------------------------------
# Honest potentials and a described cell (2026-09-27).
#
# A potential on the axis is a statement about THIS experiment: measured in it, or
# reported for it by the source. When a source reports only the current (a galvanostatic
# run, often in a gas-diffusion or membrane-electrode-assembly cell), the record carries the
# current and a complete description of the cell, and says openly that the potential was
# not reported. It never borrows a potential stated for a model, a DFT calculation or
# another experiment (on 2026-09-27, ten literature records carried -1.11 V_RHE converted
# from a potential their source stated for its DFT calculations, labelled as measured).
# ---------------------------------------------------------------------------
FULL_CELLS = {"mea_cell", "zero_gap_cell"}
FLOW_FED_CELLS = {"gde_cell", "mea_cell", "zero_gap_cell"}
_POTENTIAL_CLASSES = {"steady_state_potential", "onset_potential", "half_wave_potential"}
_OTHER_CONTEXT = re.compile(
    r"\b(?:DFT|density[- ]functional|for\s+(?:the\s+)?(?:DFT\s+)?calculations?|model(?:led)?\s+(?:condition|potential)"
    r"|simulation\s+(?:condition|potential))\b", re.I)


def _cell_and_potential_checks(record: dict):
    """(errors, warnings) for honest potentials and a described cell."""
    errors, warnings = [], []
    ctx = record.get("context") or {}
    ec = ctx.get("electrochemistry") if isinstance(ctx.get("electrochemistry"), dict) else None
    if not ec:
        return errors, warnings
    sample = record.get("sample") or {}
    pvr = ec.get("potential_vs_RHE") if isinstance(ec.get("potential_vs_RHE"), dict) else {}
    cell = ec.get("cell_type")
    experimental = (record.get("system") or {}).get("domain") != "computational"

    if experimental:
        for oi, o in enumerate((record.get("descriptors") or {}).get("outputs") or []):
            for di, d in enumerate(o.get("descriptors") or [] if isinstance(o, dict) else []):
                if not isinstance(d, dict) or (d.get("name") or "").split(".")[0] not in _POTENTIAL_CLASSES:
                    continue
                if _OTHER_CONTEXT.search(d.get("definition") or ""):
                    errors.append({
                        "code": "POTENTIAL_FROM_OTHER_CONTEXT", "path": f"descriptors/outputs/{oi}/descriptors/{di}",
                        "message": (f"Descriptor '{d.get('name')}': its definition says the value was stated for a "
                                    f"model or a calculation. A potential on an experimental record is the potential "
                                    f"measured in THIS experiment or reported for it by the source; a value stated "
                                    f"for a DFT model, a simulation or another experiment was never measured here. "
                                    f"If the source reports only the current, remove the potential, declare "
                                    f"potential_vs_RHE {{value_V: null, rhe_basis: 'not_reported'}} and describe "
                                    f"the cell completely (Context wiki, Potential Contract).")})

    if record.get("record_domain") == "performance" and not cell:
        errors.append({
            "code": "CELL_TYPE_MISSING", "path": "context/electrochemistry/cell_type",
            "message": ("An electrochemical performance record names its cell body in "
                        "context.electrochemistry.cell_type (flow_cell, h_cell, gde_cell, mea_cell, zero_gap_cell, "
                        "beaker_cell, scanning_droplet_cell, ...). Performance is a property of a catalyst IN a "
                        "cell; without the cell it cannot be compared.")})
    if cell == "three_electrode":
        warnings.append({
            "code": "DEPRECATED_CELL_TYPE", "path": "context/electrochemistry/cell_type",
            "message": ("cell_type 'three_electrode' describes wiring, not the cell body. Name the body (beaker_cell, "
                        "h_cell, flow_cell, ...); the electrode configuration goes in "
                        "system.configuration.electrode_configuration.")})

    if cell in FULL_CELLS:
        comp = sample.get("composition") or {}
        geo = sample.get("geometry") or {}
        missing = []
        if not (ec.get("membrane") or comp.get("membrane")):
            missing.append("the membrane (context.electrochemistry.membrane)")
        if not (geo.get("geometric_area_cm2") or comp.get("active_area_cm2")):
            missing.append("the active area (sample.geometry.geometric_area_cm2 or sample.composition.active_area_cm2)")
        if missing:
            errors.append({
                "code": "FULL_CELL_DESCRIPTION_INCOMPLETE", "path": "context/electrochemistry",
                "message": (f"A {cell} record must describe the device: {'; '.join(missing)} is missing. A two-"
                            f"electrode device is characterised by its catalysts and loadings on each side, the "
                            f"membrane, the active area, what each side is fed and the temperature (Context wiki, "
                            f"Full-cell electrolyzers).")})
        if pvr.get("value_V") is not None and not ec.get("reference_electrode"):
            errors.append({
                "code": "HALF_CELL_POTENTIAL_IN_FULL_CELL", "path": "context/electrochemistry/potential_vs_RHE",
                "message": (f"A {cell} is a two-electrode device: without a reference electrode it has no half-cell "
                            f"potential. Report the cell voltage as the descriptor cell_voltage (V_cell) and declare "
                            f"potential_vs_RHE {{value_V: null, rhe_basis: 'not_applicable'}}. If a reference "
                            f"electrode was integrated, declare it in reference_electrode.")})
    if cell in FLOW_FED_CELLS and not ((ctx.get("transport") or {}).get("feed")):
        errors.append({
            "code": "FEED_UNDECLARED", "path": "context/transport/feed",
            "message": (f"A {cell} is fed: declare what, in context.transport.feed {{phase, composition, flow_rate, "
                        f"flow_rate_unit}} (e.g. CO2 gas at 20 sccm, humidified CO, deionized water, 1 M KOH). The "
                        f"feed decides the reaction environment and is part of the cell description.")})

    if record.get("record_domain") == "performance" and ec.get("control_mode") == "galvanostatic":
        voltage_accounted = pvr.get("rhe_basis") in ("not_reported", "not_applicable") or cell in FULL_CELLS
        if not voltage_accounted and pvr.get("value_V") is None:
            has_pot = any("potential" in (d.get("name") or "").lower() or "cell_voltage" in (d.get("name") or "").lower()
                          for o in (record.get("descriptors") or {}).get("outputs") or []
                          for d in (o.get("descriptors") or [] if isinstance(o, dict) else []) if isinstance(d, dict))
            has_pot = has_pot or any(
                "potential" in (ch.get("name") or "").lower() or "cell_voltage" in (ch.get("name") or "").lower()
                or ch.get("unit") == "V_cell"
                for se in (record.get("measurement") or {}).get("series") or []
                for ch in (se.get("channels") or []) + (se.get("independent_variables") or []) if isinstance(ch, dict))
            if not has_pot:
                errors.append({
                    "code": "GALVANOSTATIC_NO_POTENTIAL", "path": "context/electrochemistry/potential_vs_RHE",
                    "message": ("Galvanostatic record with no potential anywhere and no statement about it. If the "
                                "potential was measured, add it (steady_state_potential in V_RHE, or the series). If "
                                "the source reports only the current, say so openly: potential_vs_RHE {value_V: null, "
                                "rhe_basis: 'not_reported'} for a half cell, or 'not_applicable' for a two-electrode "
                                "device (report cell_voltage in V_cell if given). Never fill it with a value from "
                                "another context.")})
    return errors, warnings


# ---------------------------------------------------------------------------
# Measurement or calculation, where it came from, and who produced it (2026-09-27).
#
# Several fields say whether a record is a measurement or a calculation, and they must agree:
# system.domain, system.technique (when it names a computational method), source_type
# 'computation', context.environment, sample.sample_form, sample.material.provenance and
# record_domain 'simulation'. On 2026-09-27, 98 calculations taken from papers (DFT, classical
# MD, microkinetic models) were stored as experiments: technique DFT, but domain experimental,
# environment ex_situ, a physical sample form and a synthesized material, so they answered
# queries for measurements. Where a result comes from is source_type (a paper, a database,
# or the uploader's own lab, facility or computation); who produced it is
# attribution.produced_by, which the server-stamped uploaded_by cannot stand in for. No record
# stated produced_by on 2026-09-27.
# ---------------------------------------------------------------------------
COMPUTATIONAL_TECHNIQUES = {"DFT", "ab_initio_MD", "classical_MD", "kinetic_monte_carlo",
                            "microkinetic_modeling", "machine_learning_potential"}
MODEL_SAMPLE_FORMS = {"slab_model", "cluster_model", "unit_cell", "molecule_model", "continuum_model"}
FUNCTIONAL_FAMILIES = {"DFT", "DFT_U", "hybrid_DFT", "AIMD", "CHE"}
_MEASURED_PROVENANCE = {"commercial", "synthesized", "fabricated", "natural"}
_ORG_PLACEHOLDERS = {v.lower() for v in _vocab_values("System", "system.organization_placeholders")}

_CALCULATION_ENVELOPE = ("system.domain 'computational', context.environment 'in_silico', a model "
                         "sample.sample_form (slab_model, cluster_model, unit_cell, molecule_model, "
                         "continuum_model), sample.material.provenance 'theoretical', and computation.method")


def _calculation_votes(record: dict):
    """(calc, meas): the fields that say 'calculation' and the fields that say 'measurement'."""
    sysb = record.get("system") if isinstance(record.get("system"), dict) else {}
    ctx = record.get("context") if isinstance(record.get("context"), dict) else {}
    smp = record.get("sample") if isinstance(record.get("sample"), dict) else {}
    calc, meas = [], []
    dom = sysb.get("domain")
    (calc if dom == "computational" else meas if dom == "experimental" else []).append(f"system.domain={dom}")
    if sysb.get("technique") in COMPUTATIONAL_TECHNIQUES:
        calc.append(f"system.technique={sysb['technique']}")
    if record.get("source_type") == "computation":
        calc.append("source_type=computation")
    env = ctx.get("environment")
    (calc if env == "in_silico" else meas if env else []).append(f"context.environment={env}")
    form = smp.get("sample_form")
    (calc if form in MODEL_SAMPLE_FORMS else meas if form else []).append(f"sample.sample_form={form}")
    prov = (smp.get("material") or {}).get("provenance") if isinstance(smp.get("material"), dict) else None
    (calc if prov == "theoretical" else meas if prov in _MEASURED_PROVENANCE else []).append(
        f"sample.material.provenance={prov}")
    if record.get("record_domain") == "simulation":
        calc.append("record_domain=simulation")
    return calc, meas


def _has_citation(record: dict) -> bool:
    for a in record.get("assets") or []:
        if not isinstance(a, dict):
            continue
        c = a.get("citation") if isinstance(a.get("citation"), dict) else {}
        if c.get("doi") or (c.get("title") and c.get("year")) or "doi.org/" in str(a.get("uri") or ""):
            return True
    return False


def _origin_errors(record: dict) -> list:
    """Errors for an evidence record that is unclear about what it is, where it came from, or
    who produced it."""
    if record.get("record_type") != "evidence":
        return []
    errors = []
    sysb = record.get("system") if isinstance(record.get("system"), dict) else {}
    st = record.get("source_type")
    if not sysb.get("domain"):
        errors.append({
            "code": "SYSTEM_DOMAIN_MISSING", "path": "system/domain",
            "message": ("Every evidence record says whether it is a measurement or a calculation: system "
                        "{domain: 'experimental' | 'computational', technique}. A result reported in a paper "
                        "keeps the domain of the work the paper did: a measured current is 'experimental', a "
                        "DFT energy is 'computational'.")})

    calc, meas = _calculation_votes(record)
    if calc and meas:
        errors.append({
            "code": "DOMAIN_INCONSISTENT", "path": "system/domain",
            "message": (f"This record says both calculation ({', '.join(calc)}) and measurement "
                        f"({', '.join(meas)}). A calculation (DFT, molecular dynamics, a microkinetic model, a "
                        f"simulated spectrum) has {_CALCULATION_ENVELOPE}. A measurement has system.domain "
                        f"'experimental', a physical sample_form and a physical environment. A calculation "
                        f"reported in a paper is still a calculation: keep source_type 'literature' and set the "
                        f"calculation fields.")})

    is_calc = (sysb.get("domain") == "computational" or sysb.get("technique") in COMPUTATIONAL_TECHNIQUES
               or st == "computation")
    method = (record.get("computation") or {}).get("method") if isinstance(record.get("computation"), dict) else None
    method = method if isinstance(method, dict) else {}
    if is_calc and not method.get("family"):
        errors.append({
            "code": "COMPUTATION_METHOD_MISSING", "path": "computation/method",
            "message": ("A calculation declares its method in computation.method: family (DFT, DFT_U, hybrid_DFT, "
                        "AIMD, CHE, classical_MD, microkinetic, machine_learning, semi_empirical), "
                        "functional_name (PBE, RPBE, BEEF-vdW, HSE06, ...), code and the settings the source "
                        "states. A computed number is comparable with another only next to the method that "
                        "produced it. For a calculation taken from a paper, copy the method the paper states.")})
    elif is_calc and method.get("family") in FUNCTIONAL_FAMILIES and not method.get("functional_name"):
        errors.append({
            "code": "COMPUTATION_METHOD_INCOMPLETE", "path": "computation/method/functional_name",
            "message": (f"computation.method.family '{method.get('family')}' needs functional_name (PBE, RPBE, "
                        f"BEEF-vdW, HSE06, ...): an energy or a barrier is only comparable next to the "
                        f"functional that produced it. If the source does not state it, write "
                        f"functional_name: 'not_reported'.")})

    pb = (record.get("attribution") or {}).get("produced_by") if isinstance(record.get("attribution"), dict) else None
    pb = pb if isinstance(pb, dict) else {}
    org = str(pb.get("organization") or "").strip()
    if not (str(pb.get("group") or "").strip() or (org and org.lower() not in _ORG_PLACEHOLDERS)):
        who = {
            "literature": "the group of the paper's authors (group, organization, people)",
            "database": "the group that produced the database entry; the database itself goes in assets",
            "computation": "the group that ran the calculation: your own group if you ran it",
        }.get(st, "the group that made the measurement: a curator uploading another lab's data names that lab")
        errors.append({
            "code": "PRODUCED_BY_MISSING", "path": "attribution/produced_by",
            "message": (f"State who produced this result in attribution.produced_by {{group, organization, "
                        f"people}}: {who}. uploaded_by records only who deposited the record. The source "
                        f"(source_type '{st}') and the producer together say whether a result is a published "
                        f"calculation, a calculation the uploader ran, or someone else's; two results from one "
                        f"group are not independent evidence. Use the canonical organization names in "
                        f"system.organizations.")})

    if st == "literature" and not _has_citation(record):
        errors.append({
            "code": "LITERATURE_CITATION_MISSING", "path": "assets",
            "message": ("A literature record carries its source: an asset with citation {authors, title, "
                        "journal, year, doi} and uri 'https://doi.org/<doi>' (content_role 'documentation'). "
                        "Without the source a reader cannot check the value or tell whether two records come "
                        "from the same paper.")})
    return errors


# ---------------------------------------------------------------------------
# Warnings tier (2026-06-12) — accepted-but-improvable feedback.
# Warnings NEVER block ingestion; they teach. Three severities in the
# response: errors (block), warnings (educate), info (suggest).
# ---------------------------------------------------------------------------
CANONICAL_UNIT_SET = set()
try:
    for _section in _VOCAB.get("Units", {}).values():
        for _u in _section.get("values", []) if isinstance(_section, dict) else []:
            CANONICAL_UNIT_SET.add(_u)
except Exception:
    pass


# Component-set arithmetic is GENERIC. The validator knows how to sum things and compare
# totals; it knows no chemistry. Which descriptor families are shares of a whole, and which
# descriptors aggregate others, are DATA in data/vocabulary.json — so another domain extends
# them without touching this file, and the wiki regenerates from the same source.
#
# This replaced a hardcoded CO2RR product list that had no business in a schema validator
# serving all of science, and an uncertainty vocabulary that had somehow acquired an "FE_"
# prefix despite having nothing to do with faradaic efficiency.

def _vocab_section(key, field, default):
    try:
        import ontology
        vocab = ontology.load_vocabulary() or {}
    except Exception:
        return default
    for _section in vocab.values():
        if isinstance(_section, dict) and key in _section:
            node = _section[key]
            if isinstance(node, dict) and isinstance(node.get(field), (list, dict)):
                return node[field]
    return default


def component_families():
    """Descriptor prefixes whose dotted members are shares of a whole."""
    return set(_vocab_section("descriptors.component_families", "values", []))


def aggregate_map():
    """aggregate descriptor -> the members it aggregates."""
    m = _vocab_section("descriptors.aggregate_descriptors", "map", {})
    return {k: set(v) for k, v in m.items()} if isinstance(m, dict) else {}


def uncertainty_bases():
    return set(_vocab_section("descriptors.uncertainty_basis", "values",
                              ["reported", "digitization_estimate", "assumed", "propagated",
                               "method", "exact", "not_reported"]))


def _warning_checks(record: dict):
    """Return (warnings, info) lists. Never raises; degrades to empty."""
    warnings, info = [], []
    try:
        domain = record.get("record_domain")
        ec = ((record.get("context") or {}).get("electrochemistry") or {})
        is_perf = domain == "performance" and isinstance(ec, dict) and ec

        if is_perf:
            if ec.get("pH") is None:
                warnings.append({"code": "MISSING_PH", "path": "context/electrochemistry/pH",
                                 "message": "pH (+pH_basis) is recommended on performance records — required for RHE conversion and cross-record comparison."})
            # Physical plausibility: current densities above ~10 A/cm2 are almost
            # always a unit/area-normalization bug (e.g. raw A not divided by the
            # electrode area, or an mA<->A slip). Catches silent converter errors
            # before a bulk ingest. 10 A/cm2 is well above even industrial
            # electrolyzers (~1-6 A/cm2), so legitimate data is not flagged.
            def _check_j(value, path):
                if isinstance(value, (int, float)) and abs(value) > 10000:
                    warnings.append({"code": "IMPLAUSIBLE_CURRENT_DENSITY", "path": path,
                                     "message": f"current density {value} mA/cm2 (= {value/1000:.0f} A/cm2) is "
                                                f"physically implausible — likely a unit/area-normalization bug. "
                                                f"Electrocatalysis is typically 0.1-1000 mA/cm2; even industrial "
                                                f"electrolyzers stay below ~6000."})
            _check_j(ec.get("current_setpoint_mA_cm2"), "context/electrochemistry/current_setpoint_mA_cm2")
            for o in (record.get("descriptors") or {}).get("outputs") or []:
                for d in o.get("descriptors") or [] if isinstance(o, dict) else []:
                    nm = d.get("name") or ""
                    if (nm.startswith("partial_current_density.") or nm == "steady_state_current_density") \
                            and d.get("unit") == "mA/cm2":
                        _check_j(d.get("value"), f"descriptors:{nm}")
            if not (record.get("sample") or {}).get("electrode_type"):
                warnings.append({"code": "MISSING_ELECTRODE_TYPE", "path": "sample/electrode_type",
                                 "message": "sample.electrode_type is recommended (GDE, thin_film, patterned_film, ...)."})
        contribs = (record.get("attribution") or {}).get("contributors") or []
        if record.get("record_type") == "evidence" and not any(
                c.get("role") == "data_owner" for c in contribs if isinstance(c, dict)):
            warnings.append({"code": "NO_DATA_OWNER", "path": "attribution/contributors",
                             "message": "No data_owner declared. Evidence records should credit whose data this is "
                                        "(attribution.contributors, role=data_owner, ideally with ORCID)."})
        if not record.get("links") and not record.get("tags"):
            warnings.append({"code": "NO_LINKS", "path": "links",
                             "message": "Record has no links[] and no tags[]. Group it via a typed link (same_sample_as / derived_from / intended_comparison_target) or a tag."})

        qc = ((record.get("measurement") or {}).get("qc") or {})
        if qc.get("status") == "compromised" and str(qc.get("evidence", "")).strip().upper() in ("", "N/A", "NA", "NONE"):
            warnings.append({"code": "QC_COMPROMISED_NO_EVIDENCE", "path": "measurement/qc/evidence",
                             "message": "qc.status='compromised' requires a concrete evidence sentence (what is compromised and why). 'N/A' defeats the purpose."})

        # A DRIVEN experiment whose descriptors do not say what they represent, or whose
        # static setpoints silently describe only one level of the drive. Generic across
        # domains: the same check covers a modulated potential, chopped illumination, a
        # temperature programme or pulsed dosing. Advisory only.
        mod = (record.get("context") or {}).get("modulation")
        if isinstance(mod, dict) and mod:
            if not mod.get("descriptors_represent") or mod["descriptors_represent"] == "unspecified":
                warnings.append({
                    "code": "MODULATED_DESCRIPTORS_UNSPECIFIED", "path": "context/modulation",
                    "message": (
                        "This record declares a driven (modulated) control variable but does not "
                        "say what its descriptors represent. A cycle-averaged quantity and a "
                        "steady-state quantity of the same name are DIFFERENT QUANTITIES, and a "
                        "consumer cannot tell them apart without this. Set "
                        "context.modulation.descriptors_represent.")})
            if not mod.get("driven_variable"):
                warnings.append({
                    "code": "MODULATION_DRIVEN_VARIABLE_MISSING", "path": "context/modulation",
                    "message": ("context.modulation is present but `driven_variable` is not set, "
                                "so a consumer cannot tell WHICH condition was being driven.")})
            if mod.get("frequency_Hz") and mod.get("period_s"):
                warnings.append({
                    "code": "MODULATION_RATE_OVERSPECIFIED", "path": "context/modulation",
                    "message": ("both frequency_Hz and period_s are given; they can disagree. "
                                "Declare one.")})
        else:
            # The inverse, and the case that motivated this: a record whose modulation survives
            # only in an asset filename or free text reads to every consumer as a static
            # measurement. Detect the words rather than the technique, so it fires for any
            # domain that buries a drive in prose.
            import re as _re
            _hay = " ".join([json.dumps(record.get("assets") or []),
                             str((record.get("system") or {}).get("configuration") or ""),
                             str(((record.get("context") or {}).get("electrochemistry") or {}).get("notes") or ""),
                             str((record.get("sample") or {}).get("notes") or "")])
            if _re.search(r"modulat|pulsed|duty[ _-]?cycle|chopped|square[ _-]?wave", _hay, _re.I):
                info.append({
                    "code": "MODULATION_EVIDENT_BUT_UNDECLARED", "path": "context",
                    "message": (
                        "This record mentions a modulated/pulsed/chopped experiment in its "
                        "assets or notes, but declares no context.modulation block — so every "
                        "machine reading it will treat the measurement as static, and its "
                        "setpoints as the condition of the whole run. Declare "
                        "context.modulation (driven_variable, waveform, frequency, levels and "
                        "descriptors_represent) so the drive is queryable rather than buried in "
                        "a filename.")})

        # Component-set closure, per output block.
        #
        # A descriptor family whose members are shares of a whole can be summed and compared
        # against that whole. Which families those are, and which descriptors aggregate others,
        # come from the controlled vocabulary — this code performs arithmetic and knows no
        # domain. Repository audit 2026-08-08 (1722 records / 4433 descriptors) drove three
        # corrections here: aggregates were being summed together with their own members, so
        # the over-total check fired 10 times and was wrong all 10; the band was asymmetric,
        # tighter on the side where nothing occurs; and nothing looked at under-closure, which
        # is the case that actually happens.
        for oi, o in enumerate((record.get("descriptors") or {}).get("outputs") or []):
            FAMILIES, AGGREGATES = component_families(), aggregate_map()
            leaves, rollups, inline = {}, {}, {}
            for d in o.get("descriptors") or [] if isinstance(o, dict) else []:
                nm = d.get("name") or ""
                v = d.get("value")
                fam = nm.split(".")[0]
                if (fam in FAMILIES and "." in nm and ".ratio" not in nm
                        and isinstance(v, (int, float))):
                    if isinstance(d.get("aggregates"), list) and d["aggregates"]:
                        inline[nm] = set(d["aggregates"])       # the record's own declaration
                        rollups[nm] = v
                    elif nm in AGGREGATES:
                        rollups[nm] = v
                    else:
                        leaves[nm] = v

                # sigma=0 is a CLAIM OF EXACTNESS. Firing only when the depositor already
                # confessed "not reported" in a free-text note caught 32 of 792 such
                # descriptors and was blind to the other 760 — a detector that fires only on
                # the honest depositor. It now fires on any sigma=0 not justified by an
                # explicit uncertainty.basis, and as a warning rather than info.
                unc = d.get("uncertainty") or {}
                if unc.get("sigma") == 0.0:
                    bas = str(unc.get("basis") or "").strip().lower()
                    if bas in ("", "none"):
                        warnings.append({
                            "code": "SIGMA_ZERO_PLACEHOLDER",
                            "path": f"descriptors/outputs/{oi}",
                            "message": (
                                f"Descriptor '{nm}': sigma=0.0 with no uncertainty.basis. To a "
                                f"machine this asserts the value is EXACT, and downstream "
                                f"scoring that divides by a noise scale will treat it as "
                                f"infinitely precise. If the source reported no uncertainty, "
                                f"write sigma: null with basis: 'not_reported'. If it is "
                                f"genuinely exact (a set point, an integer count), say so with "
                                f"basis: 'exact'.")})
                    elif bas not in uncertainty_bases():
                        info.append({
                            "code": "UNCERTAINTY_BASIS_NOT_IN_VOCABULARY",
                            "path": f"descriptors/outputs/{oi}",
                            "message": (
                                f"Descriptor '{nm}': uncertainty.basis '{unc.get('basis')}' is "
                                f"not one of {sorted(uncertainty_bases())}. Free-text bases "
                                f"cannot be filtered or compared across records.")})

            # Closure band, SYMMETRIC at +/-10%. Set from the repository's own distribution
            # (2026-08-08, 133 blocks with >=3 leaf products): 72.2% land in 0.90-1.10, NOTHING
            # anywhere exceeds 1.10, and the entire tail is 0.70-0.90. Quantitative calibration
            # to better than ~10% is hard, and minor or hard-to-detect species routinely go
            # unquantified, so a slate that does not close is NORMAL SCIENCE and must not be
            # nagged at as a defect. The earlier asymmetric band (warn above 1.05, warn below
            # 0.90) was tighter on the side where nothing ever happens and moralising on the
            # side where everything does.
            #
            # What a machine genuinely cannot do is tell an UNDECLARED gap from a measurement
            # that failed to balance. So the check asks for the declaration, and goes quiet the
            # moment the block provides one.
            n_leaf = len(leaves)
            total = sum(leaves.values())
            comp = o.get("completeness") if isinstance(o, dict) else None
            declared = bool(isinstance(comp, dict) and (
                comp.get("quantified") in ("major_components_only", "partial")
                or comp.get("unquantified")))
            whole = 1.0
            if isinstance(comp, dict) and isinstance(comp.get("expected_total"), (int, float)):
                whole = float(comp["expected_total"]) or 1.0

            if n_leaf >= 2 and total > 1.10 * whole:
                warnings.append({"code": "COMPONENT_SET_EXCEEDS_TOTAL", "path": f"descriptors/outputs/{oi}",
                                 "message": f"Sum of {n_leaf} leaf product component values = {total:.2f} against an expected total of {whole:.2f}, more than 10% over. Over-closure has no benign reading the way under-closure does — check for percent encoding or a product counted twice. Roll-up descriptors ({', '.join(sorted(rollups)) or 'none present'}) are excluded from this sum by design."})
            elif n_leaf >= 3 and total < 0.90 * whole and not declared:
                gap = whole - total
                entry = {"path": f"descriptors/outputs/{oi}",
                         "code": "COMPONENT_SET_INCOMPLETE_UNDECLARED",
                         "message": (
                             f"{n_leaf} component values sum to {total:.2f} of an "
                             f"expected {whole:.2f}, leaving {gap:.2f} unaccounted for, and the "
                             f"block does not say why. This is very often fine — minor and "
                             f"hard-to-detect species are routinely not quantified — but said "
                             f"out loud it becomes re-usable evidence instead of a silent hole. "
                             f"Set descriptors.outputs[].completeness: "
                             f"{{quantified: 'major_components_only', unquantified: ['liquid "
                             f"products', ...]}}. If the slate IS meant to be exhaustive, "
                             f"declare quantified: 'all_components' and the gap becomes a real "
                             f"finding worth chasing.")}
                # Under 20% missing is ordinary; beyond that it is worth a depositor's eye.
                (warnings if total < 0.80 * whole else info).append(entry)

            # Roll-ups must equal the leaves they aggregate. This is a free, exact consistency
            # check wherever both are present, and it is what distinguishes a faithfully
            # digitized slate from an assembled one.
            for rn, rv in rollups.items():
                parts = inline.get(rn) or AGGREGATES.get(rn)
                if not parts:
                    continue
                have = {k: v for k, v in leaves.items() if k in parts}
                if len(have) < 2:
                    continue
                got = sum(have.values())
                if abs(got - rv) > 0.02 + 1e-9:
                    warnings.append({"code": "AGGREGATE_DISAGREES_WITH_ITS_MEMBERS", "path": f"descriptors/outputs/{oi}",
                                     "message": f"Roll-up '{rn}' = {rv:.3f} but its components present in this block sum to {got:.3f} ({', '.join(sorted(have))}). One of the two was not read off the same data."})

        # Unknown (non-canonical, non-alias) units — vocabulary growth signal
        if CANONICAL_UNIT_SET:
            seen = set()
            for o in (record.get("descriptors") or {}).get("outputs") or []:
                for d in o.get("descriptors") or [] if isinstance(o, dict) else []:
                    u = d.get("unit")
                    if u and u not in CANONICAL_UNIT_SET and u not in UNIT_ALIASES and u not in seen:
                        seen.add(u)
                        info.append({"code": "UNIT_NOT_IN_VOCABULARY", "path": "descriptors",
                                     "message": f"Unit '{u}' is not in the canonical unit vocabulary (and not a known alias). If legitimate, request a vocabulary addition."})
    except Exception as exc:
        logger.warning("Warning-tier checks degraded: %s", exc)
    return warnings, info


# ---------------------------------------------------------------------------
# Error-message enhancement (2026-06-12): rejection must TEACH.
# additionalProperties rejections name the unknown field, list the allowed
# fields at that location, and say what to do about it.
# ---------------------------------------------------------------------------
import re as _re


def _schema_node_at(path: str):
    """Resolve a jsonschema error path like 'context/electrochemistry' to the schema node."""
    node = ISAAC_SCHEMA
    for part in [p for p in path.split("/") if p and p != "(root)"]:
        props = node.get("properties", {})
        if part in props:
            node = props[part]
        elif part.isdigit() and "items" in node:
            node = node["items"]
        elif "items" in node:
            node = node["items"]
        else:
            return None
        if node.get("type") == "array" and "items" in node:
            pass  # next loop part may be an index
    return node


def _enhance_schema_errors(errors: list) -> list:
    out = []
    for e in errors:
        msg = e.get("message", "")
        m = _re.match(r"Additional properties are not allowed \((.*) (?:was|were) unexpected\)", msg)
        if m:
            fields = m.group(1)
            node = _schema_node_at(e.get("path", ""))
            allowed = sorted((node or {}).get("properties", {}).keys())
            hint = ""
            loc = e.get("path", "(root)")
            if "configuration" not in loc:
                hint = (" Instrument/station-specific settings belong in system.configuration "
                        "(the designated open namespace). If this field genuinely generalizes "
                        "across labs, request a schema addition — do not invent fields.")
            e = dict(e)
            e["message"] = (f"Unknown field(s) {fields} in '{loc}'. "
                            f"Allowed fields here: {allowed}.{hint}")
        out.append(e)
    return out


# ADR-001 (2026-06-13) + Concept Home Matrix enforcement
CATHODIC_REACTIONS = {"CO2RR", "CORR", "HER", "ORR", "NO3RR", "urea_synthesis", "N2RR", "NRR",
                      "H2O2_electrosynthesis"}
CONFIG_DENYLIST = {
    "reference_electrode": "context.electrochemistry.reference_electrode (structured object)",
    "membrane": "context.electrochemistry.membrane",
    "separator": "context.electrochemistry.membrane",
    "anolyte": "context.electrochemistry.anolyte (structured object)",
    "cell_type": "context.electrochemistry.cell_type",
    "potential_conversion": "context.electrochemistry.potential_vs_RHE.conversion",
}


def _adr001_warnings(record):
    """ADR-001 + concept-home checks. SIGN_CONVENTION and WRONG_BLOCK are ERRORS
    since 2026-06-15 (database measured clean after the phase21 convergence sweep);
    FE-trace rules remain warnings."""
    warnings = []
    errors = []
    try:
        ec = ((record.get("context") or {}).get("electrochemistry") or {})
        reaction = _reaction_name(record)
        # Sign convention: cathodic reactions carry negative currents (IUPAC)
        if reaction in CATHODIC_REACTIONS:
            def chk(name, val):
                if isinstance(val, (int, float)) and val > 0:
                    errors.append({"code": "SIGN_CONVENTION", "path": name,
                                     "message": f"{name}={val} is positive but {reaction} is cathodic — IUPAC signed convention (ADR-001): reduction currents are NEGATIVE."})
            chk("context/electrochemistry/current_setpoint_mA_cm2", ec.get("current_setpoint_mA_cm2"))
            for o in (record.get("descriptors") or {}).get("outputs") or []:
                for d in o.get("descriptors") or [] if isinstance(o, dict) else []:
                    nm = d.get("name") or ""
                    if nm.startswith("partial_current_density.") or nm == "steady_state_current_density":
                        chk(f"descriptors:{nm}", d.get("value"))
                    at = d.get("at") if isinstance(d.get("at"), dict) else {}
                    for k in ("current_density_mA_cm2", "current_density_ECSA_mA_cm2"):
                        if k in at:
                            chk(f"descriptors:{nm}/at/{k}", at.get(k))
        # FE-in-series ruling
        fe_descriptor_names = {d.get("name") for o in (record.get("descriptors") or {}).get("outputs") or []
                               for d in (o.get("descriptors") or [] if isinstance(o, dict) else [])}
        for si, s in enumerate((record.get("measurement") or {}).get("series") or []):
            for ch in s.get("channels") or []:
                nm = ch.get("name") or ""
                if nm.startswith("faradaic_efficiency"):
                    vals = ch.get("values") or []
                    if ch.get("role") == "measured_response":
                        warnings.append({"code": "FE_ROLE_VIOLATION", "path": f"measurement/series/{si}",
                                         "message": f"FE channel '{nm}' has role=measured_response. FE is a derived claim (ADR-001) — role must be 'derived_signal'; the measurement is the GC trace and the current."})
                    if len(vals) <= 1 and nm in fe_descriptor_names:
                        warnings.append({"code": "FE_SERIES_DUPLICATE", "path": f"measurement/series/{si}",
                                         "message": f"Single-point series channel '{nm}' duplicates the descriptor of the same name — keep the descriptor, drop the channel (ADR-001)."})
        # Concept-home deny-list for system.configuration
        cfg = (record.get("system") or {}).get("configuration") or {}
        for k, home in CONFIG_DENYLIST.items():
            if k in cfg:
                errors.append({"code": "WRONG_BLOCK", "path": f"system/configuration/{k}",
                                 "message": f"'{k}' belongs in {home}, not system.configuration (Concept Home Matrix)."})
    except Exception as exc:
        logger.warning("ADR-001 checks degraded: %s", exc)
    return warnings, errors


def validate_record_full(record: dict) -> dict:
    """
    Run ALL validation layers against a record dict.

    Returns the canonical result shape (identical to the public
    /portal/api/validate response):

        {
          "valid": bool,
          "schema_valid": bool, "vocabulary_valid": bool, "semantic_valid": bool,
          "schema_errors": [...], "vocabulary_errors": [...],
          "semantic_errors": [...], "errors": [...],
        }

    Vocabulary and semantic layers degrade gracefully (log + empty list)
    on internal failure, matching the API's historical behavior; the JSON
    Schema layer never degrades.
    """
    schema_errors = _enhance_schema_errors([
        {
            "path": "/".join(str(p) for p in err.absolute_path) or "(root)",
            "message": err.message,
        }
        for err in ISAAC_VALIDATOR.iter_errors(record)
    ])

    # FIX (2026-06-11): degradation is no longer invisible. The layers still
    # fail open (fail-closed is a pending policy decision), but the response
    # now carries a `degraded` flag and the degradation reason so callers,
    # logs, and monitors can SEE that a layer did not actually run.
    degraded = []
    try:
        vocabulary_errors = ontology.validate_record_vocabulary(record)
    except Exception as exc:
        logger.error("VOCABULARY VALIDATION DEGRADED — layer did not run: %s", exc)
        vocabulary_errors = []
        degraded.append({"layer": "vocabulary", "reason": str(exc)[:200]})

    # Canonical-form enforcement (Decisions A & B) — deterministic, never
    # degrades, lives in the vocabulary layer of the response.
    vocabulary_errors = vocabulary_errors + _canonical_form_errors(record)
    vocabulary_errors = vocabulary_errors + _potential_contract_errors(record)
    vocabulary_errors = vocabulary_errors + _descriptor_name_errors(record)
    vocabulary_errors = vocabulary_errors + _record_content_errors(record)
    vocabulary_errors = vocabulary_errors + _origin_errors(record)

    try:
        semantic_errors = ontology.validate_semantic_integrity(record)
    except Exception as exc:
        logger.error("SEMANTIC VALIDATION DEGRADED — layer did not run: %s", exc)
        semantic_errors = []
        degraded.append({"layer": "semantic", "reason": str(exc)[:200]})

    errors = schema_errors + vocabulary_errors + semantic_errors
    result = {
        "valid": not errors,
        "schema_valid": not schema_errors,
        "vocabulary_valid": not vocabulary_errors,
        "semantic_valid": not semantic_errors,
        "schema_errors": schema_errors,
        "vocabulary_errors": vocabulary_errors,
        "semantic_errors": semantic_errors,
        "errors": errors,
    }
    warnings, info = _warning_checks(record)
    adr_warnings, adr_errors = _adr001_warnings(record)
    rx_errors, rx_warnings = _reaction_checks(record)
    cell_errors, cell_warnings = _cell_and_potential_checks(record)
    adr_errors = adr_errors + rx_errors + cell_errors
    warnings = warnings + adr_warnings + rx_warnings + cell_warnings
    if adr_errors:
        result["valid"] = False
        result.setdefault("vocabulary_errors", []).extend(adr_errors)
        result["errors"] = (result.get("errors") or []) + adr_errors
    if warnings:
        result["warnings"] = warnings
    if info:
        result["info"] = info
    if degraded:
        result["degraded"] = degraded
    return result


def format_errors_flat(result: dict) -> list:
    """Flatten a validation result into 'path: message' strings for UIs."""
    return [f"{e['path']}: {e['message']}" for e in result.get("errors", [])]
