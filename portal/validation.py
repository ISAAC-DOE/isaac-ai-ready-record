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
    # Seconds need two digits, a decimal or 'sec': '_1s', '_2s' are core levels (C_1s, O_1s).
    ("a time", re.compile(
        r"(?:^|[._])(?:\d{2,}(?:p\d+)?|\d+p\d+)_?s(?:$|[._])"
        r"|(?:^|[._])\d+(?:p\d+)?_?(?:sec|min|h|hr|hours?)(?:$|[._])", re.I)),
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

            # The same quantity at another condition is a second value, told apart by its `at`.
            key = (name, json.dumps(d.get("at") or {}, sort_keys=True))
            if key in seen:
                errors.append({
                    "code": "DUPLICATE_DESCRIPTOR_NAME", "path": f"{path}/name",
                    "message": (f"Descriptor '{name}' appears twice in output block {oi} (positions "
                                f"{seen[key]} and {di}) with the same `at`. One name, one value per condition: "
                                f"a second value of the same quantity is a different condition (state it in "
                                f"`at`) or a different record, never a duplicate.")})
            seen.setdefault(key, di)

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
        # A calculation has no cell: its potential treatment is computation.potential_method and
        # context.simulation_assumptions, so only a measurement must state the electrochemistry.
        if drive in ELECTROCHEMICAL_DRIVES and not ec and not _claims_calculation(record):
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
# A record is one result (2026-09-27).
#
# One sample or one model, one measurement or one calculation, one set of conditions, the values
# it produced, one source (Record-Granularity wiki, "What an ISAAC record is"). On 2026-09-27, 436
# records built by an agent from papers through a question-answering search tool broke this in
# ways no rule saw: a survey, a review or a series of samples as the "sample"; a value the source
# quoted from another paper; catalysts told apart inside descriptor names; sentences, series and
# factors stored as values; reactor tests stored ex_situ; and 124 notes admitting that a wrong
# vocabulary term had been submitted. Each check below was run on every stored record from other
# uploaders, the wiki examples and the blind-test records with no false alarm, and shipped as an
# error. A day later 26 of 31 legitimate look-alikes (a particle 'size range of 5-10 nm', 'Au
# islands across the Cu surface', 'n.d.' in a table, a '(10-10)' facet, 'mixed rutile and
# anatase', a paper plus its SI) were rejected, so these checks are WARNINGS: they teach and do not
# block. The stored corpus is too narrow to prove a word-based check safe. A check becomes an error
# only when tests/test_one_result.py LOOKALIKES, a corpus of legitimate records built from other
# subfields, raises no false alarm, and the live warning log shows it firing only on real faults.
# ---------------------------------------------------------------------------
_COLLECTION_NAME = re.compile(
    r"\b(?:survey(?:ed)?|review(?:ed)?|meta-?analysis|compilation"
    r"|literature[\s-](?:survey|cited|values?|data|review|compilation)"
    r"|benchmarking\s+(?:practice|study|survey|protocol)s?|sensitivity\s+analysis|varied"
    r"|across\s+(?:\d+|several|multiple|many|different|various)\b"
    r"|across\s+(?:[\w-]+\s+){0,2}(?:conditions|samples|catalysts|materials|loadings|compositions|temperatures"
    r"|potentials|pressures|studies|papers)"
    r"|(?:multiple|several|many|different|various|\d+)\s+(?:catalysts|materials|compositions|studies|papers|systems))\b",
    re.I)
# Case-sensitive: a surname is a capital and lowercase letters, so "(sample 2047)", "(SRM 1976b)" and
# "(MaTeck, 2019)" are not citations.
# A year after a lot, batch or run word, a month or a season ("Cu foil (Batch 2023)", "(May 2024)") labels the
# object. Requiring a comma before the year would also spare "(Evonik 2021)", but on 2026-10-01 it lost 9
# of the 10 live citations in sample names ("Pt/C (Xue 2020)"), so a supplier and year in brackets warns.
_NAME_CITATION = re.compile(
    r"\((?!(?:Batch|Lot|Run|Series|Set|Sample|Campaign|Plate|Wafer|Cycle|Version|Revision|Grade|Edition"
    r"|Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?"
    r"|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|Spring|Summer|Fall|Autumn|Winter)\b)"
    r"[A-Z][a-z]+(?:[-'\u2019][A-Z]?[a-z]+)?(?:\s+et\s+al\.?)?,?\s+(?:19|20)\d\d[a-z]?\)"
    r"|\bet\s+al\.?,?\s+(?:19|20)\d\d")
# One object whose property varies across it (a wedge, a gradient, a composition spread) is one sample.
_ONE_OBJECT_VARYING = re.compile(
    r"\b(?:composition[- ]spread|(?:composition|thickness|dopant|loading|potential|temperature)[- ](?:gradient|graded)"
    r"|(?:wedge|gradient|graded)[- ]?(?:shaped\s+)?(?:film|layer|wafer|crystal|electrode|foil|coating|disk|disc|sample))\b"
    r"|\b(?:film|layer|wafer|crystal|electrode|foil|coating|disk|disc|sample)\s+(?:of|with)\s+(?:varied|varying)\b",
    re.I)
# Only notes that speak of the vocabulary: 'used as a proxy', 'no exact value' and 'submitted as' are
# ordinary science and curation.
_SUBSTITUTION = re.compile(
    r"vocabulary\s+substitution|not\s+a\s+value\s+in\s+the\s+.{0,40}vocabulary"
    r"|not\s+(?:in|part\s+of|available\s+in)\s+the\s+(?:isaac\s+)?(?:closed\s+|controlled\s+)?vocabulary"
    r"|closest\s+(?:available|allowed|matching|permitted|valid)\s+(?:vocabulary\s+)?(?:term|token|technique)", re.I)
_LOWER_WORD = re.compile(r"\b[a-z]{2,}\b")
# A formula, optionally isotope-labelled (13CO), or formula lumps joined by underscores (C2_C4).
_FORMULA_TOKEN = re.compile(r"^(?:\d{1,3})?(?:[A-Z][a-z]?\d*)+(?:plus)?(?:_(?:[A-Z][a-z]?\d*)+(?:plus)?)*$")
_SPECIES_CLASSES = set(_vocab_values("Descriptors", "descriptors.catalytic_performance"))
_PRODUCT_TOKENS = set(_vocab_values("Descriptors", "descriptors.faradaic_efficiency_products"))
for _agg, _members in _vocab_map("Descriptors", "descriptors.aggregate_descriptors").items():
    _PRODUCT_TOKENS.update(n.split(".", 1)[1] for n in [_agg] + list(_members or []) if "." in n)
_ONE_RESULT = "What an ISAAC record is: Record-Granularity wiki."


def _normalized_doi(value) -> str:
    v = str(value or "").strip().lower()
    v = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", v)
    return v.rstrip(".,;)")


def _source_citations(record: dict) -> list:
    """Distinct works cited with relation 'source'. A DOI that extends another cited DOI (a paper's
    SI, '10.x/abc.s001' under '10.x/abc') is the same work."""
    keys = []
    for a in _assets(record):
        c = a.get("citation") if isinstance(a.get("citation"), dict) else None
        if not c or c.get("relation") != "source":
            continue
        doi = _normalized_doi(c.get("doi"))
        key = doi or (f"{c.get('title')}|{c.get('year')}" if c.get("title") else str(a.get("uri") or ""))
        if key and key not in keys:
            keys.append(key)
    dois = [k for k in keys if k.startswith("10.")]
    return [k for k in keys
            if not any(k != d and k.startswith(d) and k[len(d)] in "./-_" for d in dois)]


def _one_result_warnings(record: dict) -> list:
    """Warnings for an evidence record that may hold more, or less, than one result."""
    if record.get("record_type") != "evidence":
        return []
    warnings = []
    name = str(((record.get("sample") or {}).get("material") or {}).get("name") or "")
    m = _COLLECTION_NAME.search(name)
    if m and m.group(0).lower() == "varied" and _ONE_OBJECT_VARYING.search(name):
        m = None
    if m:
        warnings.append({
            "code": "SAMPLE_NOT_ONE_MATERIAL", "path": "sample/material/name",
            "message": (f"sample.material.name '{name}' names a collection ('{m.group(0)}'), not one material. A "
                        f"record is one result on one sample or one model. A survey, a review, a compilation or "
                        f"a series of samples (varied loading, several catalysts) is many results: make one record "
                        f"per sample, built from the paper that measured or computed it, and link the records "
                        f"with intended_comparison_target. {_ONE_RESULT}")})
    m = _NAME_CITATION.search(name)
    if m:
        warnings.append({
            "code": "SAMPLE_NAME_CITES_A_PAPER", "path": "sample/material/name",
            "message": (f"sample.material.name '{name}' cites a paper ('{m.group(0)}'). A value the source quotes "
                        f"from another paper is that paper's result: build its record from the paper that "
                        f"measured it, with that paper as the source. The name names the material only. "
                        f"{_ONE_RESULT}")})
    sources = _source_citations(record)
    if len(sources) > 1:
        shown = ", ".join(str(x) for x in sources[:3]) + (f" and {len(sources) - 3} more" if len(sources) > 3 else "")
        warnings.append({
            "code": "MULTIPLE_SOURCES", "path": "assets",
            "message": (f"This record names {len(sources)} sources ({shown}). One result comes from one work, and "
                        f"results from different papers are different records. If the same work also appears "
                        f"elsewhere (its dataset, a thesis, an erratum or correction), cite that with citation.relation 'reports_this_work'; "
                        f"a paper cited for context has relation 'reference'. {_ONE_RESULT}")})
    ctx = record.get("context") if isinstance(record.get("context"), dict) else {}
    if record.get("record_domain") == "performance" and ctx.get("environment") == "ex_situ":
        warnings.append({
            "code": "PERFORMANCE_EX_SITU", "path": "context/environment",
            "message": ("A performance record measures a catalyst while the reaction runs, so context.environment "
                        "is 'in_situ' or 'operando'; this one says 'ex_situ'. A measurement on a catalyst outside "
                        f"the reaction is a characterization record (record_domain 'characterization'). {_ONE_RESULT}")})
    for oi, o in enumerate((record.get("descriptors") or {}).get("outputs") or []):
        for di, d in enumerate(o.get("descriptors") or [] if isinstance(o, dict) else []):
            if not isinstance(d, dict):
                continue
            dname = str(d.get("name") or "")
            path = f"descriptors/outputs/{oi}/descriptors/{di}"
            stem, _, qual = dname.partition(".")
            if qual and stem in _SPECIES_CLASSES and qual not in _PRODUCT_TOKENS and not _FORMULA_TOKEN.match(qual):
                warnings.append({
                    "code": "QUALIFIER_NOT_A_PRODUCT", "path": f"{path}/name",
                    "message": (f"Descriptor '{dname}': the part after the dot names a species, as a chemical "
                                f"formula (selectivity.C3H6, conversion.CO2) or a product token from the vocabulary "
                                f"(Descriptors wiki). '{qual}' is neither. A second catalyst is a second record, and "
                                f"a condition goes in `at`. {_ONE_RESULT}")})
            v = d.get("value")
            if not isinstance(v, str):
                continue
            if d.get("kind") in ("absolute", "differential"):
                warnings.append({
                    "code": "NUMBER_AS_TEXT", "path": f"{path}/value",
                    "message": (f"Descriptor '{dname}' is kind '{d.get('kind')}' and holds text ('{v[:60]}'). A "
                                f"value is one number with its unit; a label is kind 'categorical'. Several values "
                                f"are a series (measurement.series), or separate records when they come from "
                                f"separate measurements; the spread of one value is its uncertainty; a factor "
                                f"relative to another sample ('sixfold') is each sample's own value, in its own "
                                f"record. A value below a detection limit has no number form yet: omit it and say "
                                f"so in the definition of a related value or in qc.notes. {_ONE_RESULT}")})
            elif len(_LOWER_WORD.findall(v)) >= 6:
                warnings.append({
                    "code": "SENTENCE_AS_VALUE", "path": f"{path}/value",
                    "message": (f"Descriptor '{dname}' holds a sentence ('{v[:80]}'). A value is a number with a "
                                f"unit, or a short category label. A statement about results (a trend, an "
                                f"observation, a conclusion) interprets them: store the values it rests on, each "
                                f"in its record, and leave the statement to whatever reasons over the records. "
                                f"{_ONE_RESULT}")})
    for path, text in _curated_text_fields(record):
        m = _SUBSTITUTION.search(text)
        if m:
            excerpt = text[max(0, m.start() - 40): m.end() + 60].replace("\n", " ")
            warnings.append({
                "code": "VOCABULARY_SUBSTITUTION", "path": path,
                "message": (f"This field says a vocabulary term was substituted ('...{excerpt}...'). A substituted "
                            f"term is a wrong term, and every query on that field returns this record by mistake. "
                            f"Use the exact term. If the vocabulary lacks it, propose it (POST "
                            f"/portal/api/vocabulary/proposals) and store the record once it exists. If no measurement or "
                            f"calculation stands behind the value (a literature survey, a model's conclusion), it is "
                            f"not an ISAAC record. {_ONE_RESULT}")})
            break
    return warnings

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
# H2 is also molecular hydrogen ("favours H2 evolution", "stable against H2 reduction"), so a bare H2
# counts as a hypothesis label only with the word hypothesis; H1 and H3-H9 do not collide with
# chemistry. A benchmark identifier is the case-and-item form (case_10-LIT-0038), not any "case_3".
_REASONING_PATTERNS = (
    ("a hypothesis label", re.compile(
        r"\bhypothes[ie]s\s+H[1-9]\b|\bH[1-9]\s+hypothesis\b|\b(?:mechanism|explanation)\s+H[13-9]\b"
        r"|\bH[13-9]\s*(?:['’]s\b|is\s+(?:supported|refuted|favou?red|disfavou?red|scored|decisive)|would\b|predicts?\b)"
        r"|\b(?:against|supports?|refutes?|contradicts?|favou?rs?|disfavou?rs?|keeps?|scored\s+as)\s+H[13-9]\b", re.I)),
    ("a reference to a competing hypothesis", re.compile(
        r"\bthe\s+residual(?:['’]s)?\s+(?:hypothesis|claim|mechanism|explanation|central)\b"
        r"|\bresidual\s+hypothesis\b", re.I)),
    ("benchmark machinery", re.compile(
        r"\b(?:frozen[- ]set|cold[- ]seat|answer[- ]key|gold[- ](?:set|verdict|key)|benchmark\s+(?:case|item|question)"
        r"|wave[- ]\d+\s+(?:seat|run|agent)s?)\b", re.I)),
    ("a benchmark case or item identifier", re.compile(r"\bcase_\d+(?:-LIT-\d+|['’]s)\b|\bLIT-\d{3,4}\b", re.I)),
    ("a verdict", re.compile(r"\bdecisive\s+(?:against|for|record|test|evidence)\b", re.I)),
    ("the purpose the record serves", re.compile(
        r"\b(?:this|the)\s+record\s+(?:tests|supports|refutes|contradicts|is\s+decisive|matters\s+because"
        r"|(?:was|is)\s+(?:chosen|included|selected)\s+(?:because|to|as|for))\b", re.I)),
)
_WORKFLOW_TAG = re.compile(
    r"^(?:case[_-]?\d+.*|.*\blit-\d{3,}.*|.*hypothes.*|.*frozen[-_ ]?set.*|.*answer[-_]key.*|seat[-_][a-f])$", re.I)


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


# A competing hypothesis named in a curator's note ("Supports the INTERFACE rival", "Metal-interface
# rival.", "Baseline for the vacancy-count rival"): 14 live records carried such text on 2026-10-01 and
# REASONING_IN_RECORD missed it. Chemistry and engineering also say "rival" for a competing material or
# technology ("activity rivaling Pt", "a Pt-free rival to IrO2", "rival technologies", "the Ni-Fe rival
# showed"), so a match needs a name built like a label: an all-capitals word of six or more letters, or a
# hyphenated compound that is not a material description (-free, -based, -doped, low-cost, an element
# chain such as Ni-Fe or Fe-N-C). Warn first: no other uploader's records match.
_RIVAL_VERB = (r"(?i:support(?:s|ed|ing)?|favou?r(?:s|ed|ing)?|refut(?:e|es|ed|ing)|contradict(?:s|ed|ing)?"
               r"|rul(?:e|es|ed|ing)\s+out|(?:in)?consistent\s+with|baseline\s+for|decisive\s+(?:for|against))")
_RIVAL_LABEL = (r"(?:[A-Z][A-Z0-9]{5,}"
                r"|(?!(?i:[a-z0-9]+-(?:free|based|rich|poor|doped|loaded|supported|containing|like|derived"
                r"|modified|coated|known|called)\b))"
                r"(?!(?i:(?:low|high|next|best|state|non|well|top|cost|world|industry)-))"
                r"(?![A-Z][a-z]?(?:-[A-Z][a-z]?)+\b)"
                r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+){1,3})")
_RIVAL_AFTER = (r"(?!\s+(?i:to|of|for|with|in|catalysts?|anodes?|cathodes?|electrodes?|materials?|oxides?"
                r"|metals?|technolog(?:y|ies)|systems?|devices?|process(?:es)?|routes?|pathways?)\b)")
_RIVAL_PATTERNS = (
    re.compile(rf"\b{_RIVAL_VERB}\s+(?:(?i:the)\s+)?{_RIVAL_LABEL}\s+(?i:rival)\b{_RIVAL_AFTER}"),
    re.compile(rf"\b{_RIVAL_LABEL}\s+(?i:rival)\b(?=\s*(?:[.;:)\]]|$))"),
)


# A value the curator labels as quoted from a review ("SECOND-HAND (review table)", "this value is quoted in
# a review article"). The review is then a reference; the record's source is the paper that measured the
# value. "A second-hand potentiostat" is equipment, so the label must sit on a value or name a review.
_SH_DATA = (r"(?:value|values|data|number|numbers|result|results|figure|figures|conversion|selectivity|yield"
            r"|activity|rate|rates)")
_SH_REVIEW = r"(?:a|the)\s+review(?:\s+(?:table|article|paper))?\b"
_SECOND_HAND = re.compile(
    rf"\bsecond[- ]hand\s*(?:[:(]\s*(?:[\w'-]+\s+){{0,6}}?(?:review|{_SH_DATA}|quoted)\b|{_SH_DATA}\b)"
    rf"|\b{_SH_DATA}\b(?:\s+[\w'-]+){{0,5}}\s+(?:quoted\s+(?:in|from)|(?:taken|read|copied|extracted|transcribed"
    rf"|obtained)\s+from|from)\s+{_SH_REVIEW}"
    rf"|\breview[- ]derived\s+{_SH_DATA}\b", re.I)
# A measurement condition in the sample name: a number with a temperature, pressure, potential or time
# unit ("300 C at 50 bar", "at 1.2 V", "for 10 h"). A condition of how the material was made ("calcined
# at 500 C") names the material and is left alone. C, K and V count only after "at", "for", "to", "over",
# "between", a comma or a bracket, or in a range ("2.5-4.3 V"), so "3C-SiC", "Fe 3 C", "Cabot 300C" and
# "Ti-6Al-4V" are names.
_NAME_CONDITION = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)?\s*(?:°\s*C|degrees?\s+C|bar|mbar|atm|MPa|kPa|psi|torr|Torr|mV|h|hr|hrs|hours?|min)\b(?!\w)"
    r"|(?:\bat|\bfor|\bafter|\bunder|\bto|\bover|\bbetween|\band|@|[,(])\s*-?\d+(?:\.\d+)?\s*(?:C|K|V)\b(?![\w-])"
    r"|(?<![\w.])\d+(?:\.\d+)?\s*[-\u2013]\s*\d+(?:\.\d+)?\s*(?:°\s*C|C|K|V|bar|h)\b(?![\w-])")
_MADE_BY = re.compile(
    r"\b(?:calcin\w*|anneal\w*|reduced|sinter\w*|pyroly\w*|treated|dried|aged|synthesi[sz]ed|prepared"
    r"|heated|grown|deposited|activated|cured|baked|quenched|oxidi[sz]ed|nitrided|sulfided|carburi[sz]ed"
    r"|hydrothermal\w*|solvothermal\w*)\s+(?:[\w-]+\s+){0,3}(?:at|in|for|under|to)?\s*$", re.I)


def _second_hand_and_name_condition_warnings(record: dict) -> list:
    """A value labelled as quoted from a review, and a measurement condition in the sample name."""
    if record.get("record_type") != "evidence":
        return []
    warnings = []
    for path, text in _curated_text_fields(record):
        m = _SECOND_HAND.search(text)
        if m:
            excerpt = text[max(0, m.start() - 30): m.end() + 40].replace("\n", " ")
            warnings.append({
                "code": "SECOND_HAND_SOURCE", "path": path,
                "message": (f"This field says the value was taken from a review ('...{excerpt}...'). A value a "
                            f"review quotes is the result of the paper that measured it: build the record from "
                            f"that paper, cite it with citation.relation 'source', and cite the review with "
                            f"relation 'reference'; or remove the value. {_ONE_RESULT}")})
            break
    name = str(((record.get("sample") or {}).get("material") or {}).get("name") or "")
    for m in _NAME_CONDITION.finditer(name):
        if _MADE_BY.search(name[:m.start()]):
            continue
        warnings.append({
            "code": "CONDITIONS_IN_SAMPLE_NAME", "path": "sample/material/name",
            "message": (f"sample.material.name '{name}' carries a measurement condition ('{m.group(0)}'). The "
                        f"name names the material. Temperature, pressure, potential and duration of the "
                        f"measurement go in context (temperature_K, pressure, potential_vs_RHE) or in a "
                        f"descriptor's `at`. A condition of how the material was made, such as a calcination "
                        f"temperature, belongs with the name or in sample.material.notes. {_ONE_RESULT}")})
        break
    return warnings


def _competing_hypothesis_warnings(record: dict) -> list:
    """A curator-written field that names a competing hypothesis (warn first; see _RIVAL_PATTERNS)."""
    warnings = []
    for path, text in _curated_text_fields(record):
        for rx in _RIVAL_PATTERNS:
            m = rx.search(text)
            if m:
                excerpt = text[max(0, m.start() - 30): m.end() + 30].replace("\n", " ")
                warnings.append({
                    "code": "COMPETING_HYPOTHESIS_LANGUAGE", "path": path,
                    "message": (f"This field names a competing hypothesis ('...{excerpt}...'). A record states "
                                f"what was measured, computed or reported. Which explanation the data supports "
                                f"belongs to the discovery platform that uses the record. If the source itself "
                                f"draws this conclusion, quote it as a source excerpt in assets; otherwise "
                                f"describe the data only.")})
                break
    return warnings


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
# Which field values say "calculation" and which say "measurement" is data (vocabulary
# system.domain_signals, rendered on the System wiki page), and so is what the method
# declaration of a calculation must contain (computation.method_requirements and
# computation.method_requirements_by_family). The rules below name no technique, method or
# code: a new one is covered by a vocabulary proposal alone.
#
# On 2026-09-27: 98 calculations taken from papers were stored as experiments (technique DFT;
# domain, environment, sample form and provenance all saying measurement); no record stated who
# produced its result; 13 records built from a paper's public data were typed as facility
# measurements; the code of 130 calculations sat in system.instrument, where the discovery
# engine never reads it; and no database record identified the entry it came from. A declared
# computation.method is not a domain signal: an experimental record may carry the method that
# analysed it (an EXAFS fit, a Rietveld refinement), so it draws the warning
# COMPUTATION_ON_MEASUREMENT instead (made an error on 2026-09-27, reverted a day later).
# ---------------------------------------------------------------------------
DOMAIN_SIGNALS = _vocab_map("System", "system.domain_signals")
_SIGNAL_FIELDS = sorted({k.split("=", 1)[0] for k in DOMAIN_SIGNALS})
_CLAIM_FIELDS = ("system.domain", "system.technique", "source_type")
METHOD_REQUIRED = _vocab_values("Computation", "computation.method_requirements")
METHOD_REQUIRED_BY_FAMILY = _vocab_map("Computation", "computation.method_requirements_by_family")
PUBLICATION_EXTRACTION_STEPS = set(_vocab_values("Measurement", "measurement.processing.publication_extraction_steps"))
_ORG_PLACEHOLDERS = {v.lower() for v in _vocab_values("System", "system.organization_placeholders")}
_FIGURE_ASSET_FIELDS = ("caption_verbatim", "caption_highlights", "figure_label", "paper_conclusions_about_figure",
                        "page")
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+")
_CODE_KEY = re.compile(r"(?:\w+_)?code(?:_version)?")


def _signal_values(field: str, says: str) -> list:
    return [k.split("=", 1)[1] for k, v in sorted(DOMAIN_SIGNALS.items()) if k.split("=", 1)[0] == field and v == says]


MODEL_SAMPLE_FORMS = set(_signal_values("sample.sample_form", "calculation"))
_CALCULATION_ENVELOPE = (
    "system.domain 'computational', context.environment "
    + " or ".join(f"'{x}'" for x in _signal_values("context.environment", "calculation"))
    + ", a model sample.sample_form (" + ", ".join(_signal_values("sample.sample_form", "calculation"))
    + "), sample.material.provenance "
    + " or ".join(f"'{x}'" for x in _signal_values("sample.material.provenance", "calculation"))
    + ", and computation.method")


def _value_at(record: dict, dotted: str):
    node = record
    for part in dotted.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node if isinstance(node, str) else None


def _calculation_votes(record: dict):
    """(calc, meas): the 'field=value' signals that say calculation and those that say measurement."""
    calc, meas = [], []
    for field in _SIGNAL_FIELDS:
        value = _value_at(record, field)
        says = DOMAIN_SIGNALS.get(f"{field}={value}") if value is not None else None
        (calc if says == "calculation" else meas if says == "measurement" else []).append(f"{field}={value}")
    return calc, meas


# Classes stored as a non-negative magnitude (vocabulary data). On 2026-09-30 agents building
# records from one HER paper stored its 38 mV overpotential as +0.038 V and as -0.038 V; nothing said
# which, and no stored record had a negative value of these classes.
MAGNITUDE_CLASSES = set(_vocab_values("Descriptors", "descriptors.magnitude_classes"))


def _magnitude_warnings(record: dict) -> list:
    """A negative value of a class stored as a magnitude (descriptors.magnitude_classes)."""
    out = []
    for oi, o in enumerate((record.get("descriptors") or {}).get("outputs") or []):
        for di, d in enumerate(o.get("descriptors") or [] if isinstance(o, dict) else []):
            if not isinstance(d, dict):
                continue
            v, stem = d.get("value"), str(d.get("name") or "").split(".")[0]
            if stem in MAGNITUDE_CLASSES and isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0:
                out.append({
                    "code": "NEGATIVE_MAGNITUDE", "path": f"descriptors/outputs/{oi}/descriptors/{di}/value",
                    "message": (f"Descriptor '{d.get('name')}' is {v}, and class '{stem}' is stored as a non-negative "
                                f"magnitude (descriptors.magnitude_classes). The direction is carried by the reaction "
                                f"and by the sign of the current density at which the value is read: store {abs(v)}.")})
    return out


def _producer_warnings(record: dict) -> list:
    """A placeholder producer outside the literature: accepted, and asked to name the producer if known."""
    if record.get("record_type") != "evidence" or record.get("source_type") == "literature":
        return []
    pb = (record.get("attribution") or {}).get("produced_by") if isinstance(record.get("attribution"), dict) else None
    pb = pb if isinstance(pb, dict) else {}
    group, org = str(pb.get("group") or "").strip(), str(pb.get("organization") or "").strip()
    if group and not (_names_someone(group) or _names_someone(org)):
        return [{
            "code": "PRODUCED_BY_UNNAMED", "path": "attribution/produced_by",
            "message": (f"'{group}' names no producer. If the producer is known, name the group (the PI's "
                        f"name as printed, plus 'group') or the organization: two results from one producer are "
                        f"not independent evidence. If it cannot be named (anonymized or industrial data), keep "
                        f"'not_reported'; the discovery engine treats it as absent, never as a shared producer.")}]
    return []


# --- Conditions only in prose, and producers named as authors (2026-10-01) ------------------------
# A rebuilt literature pipeline gave a turnover frequency's temperature only in its definition ("at 400 C")
# while context.temperature_K said not_reported, and named every producer "<first author> et al.". Three
# blind reviews: the prose rule warns, because C-rates, eV, ramp rates, reference values and fit ranges read
# like conditions in some science; "et al." in a producer group holds, because no group is named that way.
_PREP_WORDS = (r"(?:calcined|annealed|reduced|sputter-deposited|pretreated|pre-treated|dried|aged|quenched|refluxed"
               r"|sintered|pyrolyzed|pyrolysed|impregnated|heat-treated|degassed|oxidized|oxidised|passivated|purged"
               r"|sealed|stored|refrigerated|frozen|lyophilized|cultured|grown|fermented|synthesized|synthesised"
               r"|prepared|deposited|activated)")
_COND_NUM = r"(?:~|\u2248|ca\.\s*|approximately\s+)?(?P<num>[+\u2212-]?\d+(?:\.\d+)?)(?:\s*\u00b1\s*\d+(?:\.\d+)?)?"
_COND_CUE = (r"(?:\bat\s+(?:a\s+)?(?:temperature|pressure|potential)\s+of\s+|\bat\s+|@\s*"
             r"|\b(?:temperature|(?<![A-Za-z\u0394\u03b4])T)\s*[=:\u2248]\s*|\bunder\s+)")
_COND_UNIT = (r"(?P<unit>\u00b0\s*C|\u2103|\u00baC|degrees?\s+C(?:elsius)?|Celsius|mbar|kPa|MPa|bara|barg|bar|atm"
              r"|torr|Torr|(?<![A-Za-z])m?V|K|C)")
_CONDITION = re.compile(
    rf"(?P<cue>{_COND_CUE}){_COND_NUM}\s*{_COND_UNIT}(?![A-Za-z0-9])"
    r"(?!\s*(?:/|\u00b7|\u22c5)|\s*(?:min|s|sec|h|hr|dec)\b|\s*\^)"
    r"(?!\s*(?:-|\u2013|to)\s*[+\u2212-]?\d)"
    r"(?P<ref>\s*(?:vs\.?|versus)\s*(?P<scale>[A-Za-z0-9/+]+))?")
_COND_REFERENCE_BEFORE = re.compile(
    r"(?:relative\s+to|compared\s+(?:with|to)|normali[sz]ed\s+to|referenced\s+to|with\s+respect\s+to)"
    r"(?:\s+the\s+(?:value|rate|current|signal|response))?\s*$", re.I)
_COND_PREP_BEFORE = re.compile(rf"\b{_PREP_WORDS}\b(?:\W+\w+){{0,7}}\W*$", re.I)
_COND_BATTERY_BEFORE = re.compile(r"capacit|cycl|charg|discharg|capabilit", re.I)
_TEMPERATURE_UNITS = {"k", "celsius", "degc", "c", "\u00b0c", "kelvin"}
_PRESSURE_UNITS = {"bar", "mbar", "kpa", "mpa", "pa", "atm", "torr"}
_POTENTIAL_UNITS = {"v", "mv", "v_rhe", "v vs rhe"}


def _condition_dimension(m, before: str):
    """'temperature', 'pressure', 'potential' or None for one match of _CONDITION."""
    unit = re.sub(r"\s+", "", m.group("unit"))
    cue = m.group("cue").strip().lower()
    num = float(m.group("num").replace("\u2212", "-"))
    if unit in ("mbar", "kPa", "MPa", "bara", "barg", "bar", "atm", "torr", "Torr"):
        return "pressure"
    if cue.startswith("under"):
        return None
    if unit in ("V", "mV"):
        return "potential" if re.fullmatch(r"(?i)rhe", m.group("scale") or "") else None
    if unit == "K":
        return None if num == 0 else "temperature"
    if unit == "C":
        return "temperature" if num >= 50 and not _COND_BATTERY_BEFORE.search(before[-40:]) else None
    return "temperature"


def _structured(record: dict, at: dict, dimension: str) -> bool:
    ctx = record.get("context") if isinstance(record.get("context"), dict) else {}
    at = at if isinstance(at, dict) else {}
    if dimension == "temperature":
        return at.get("temperature_K") is not None or ctx.get("temperature_K") is not None
    if dimension == "pressure":
        feed = ((ctx.get("transport") or {}).get("feed") or {}) if isinstance(ctx.get("transport"), dict) else {}
        thermo = ctx.get("thermodynamics") if isinstance(ctx.get("thermodynamics"), dict) else {}
        return any(v is not None for v in (at.get("pressure_bar"), feed.get("pressure_bar"), thermo.get("pressure_Pa")))
    ec = ctx.get("electrochemistry") if isinstance(ctx.get("electrochemistry"), dict) else {}
    rhe = ec.get("potential_vs_RHE") if isinstance(ec.get("potential_vs_RHE"), dict) else {}
    return any(v is not None for v in (at.get("potential_V_RHE"), rhe.get("value_V"), ec.get("potential_setpoint_V")))


def _measures(unit: str, dimension: str) -> bool:
    """The descriptor's own quantity is of this dimension, so the number is the measurand."""
    u = str(unit or "").strip().lower().replace(" ", "")
    return ((dimension == "temperature" and u in _TEMPERATURE_UNITS)
            or (dimension == "pressure" and u in _PRESSURE_UNITS)
            or (dimension == "potential" and u in _POTENTIAL_UNITS))


def _prose_condition_warnings(record: dict) -> list:
    """A descriptor definition that states the condition its value was read at, while no structured field
    carries that quantity (warning)."""
    if record.get("record_type") != "evidence":
        return []
    warnings = []
    for oi, o in enumerate((record.get("descriptors") or {}).get("outputs") or []):
        for di, d in enumerate(o.get("descriptors") or [] if isinstance(o, dict) else []):
            if not isinstance(d, dict) or not isinstance(d.get("definition"), str):
                continue
            text = d["definition"]
            for m in _CONDITION.finditer(text):
                before = text[:m.start()]
                if _COND_REFERENCE_BEFORE.search(before) or _COND_PREP_BEFORE.search(before):
                    continue
                dim = _condition_dimension(m, before)
                if dim is None or _measures(d.get("unit"), dim) or _structured(record, d.get("at"), dim):
                    continue
                field = {"temperature": "`at.temperature_K` (or context.temperature_K with temperature_basis "
                                        "'stated')",
                         "pressure": "`at.pressure_bar` (or context.transport.feed.pressure_bar)",
                         "potential": "`at.potential_V_RHE` (or context.electrochemistry.potential_vs_RHE)"}[dim]
                warnings.append({
                    "code": "CONDITION_ONLY_IN_PROSE", "path": f"descriptors/outputs/{oi}/descriptors/{di}/definition",
                    "message": (f"The definition of '{d.get('name')}' states the {dim} it was read at "
                                f"('{m.group(0).strip()}'), and no structured field carries it. Put it in {field}. "
                                f"An agent filtering by {dim} finds only what the structured fields hold.")})
                break
    return warnings


_AUTHOR_LIST_GROUP = re.compile(r"(?i)\bet\.?\s*al\b\.?|\band\s+co-?workers\b|\band\s+colleagues\b")
_GROUP_WORDS = re.compile(r"(?i)\b(?:groups?|labs?|laborator(?:y|ies)|institutes?|cent(?:er|re)s?|consortium|team"
                          r"|facilit(?:y|ies)|collaboration|initiative|program(?:me)?|project)\b")
_SEMICOLON_AUTHORS = re.compile(r"[A-Z][A-Za-z'\u2019\-]+,\s*[A-Z][^;]{0,60};\s*[A-Z]")
_AND_AUTHORS = re.compile(r"^\s*[A-Z][\w'\u2019.\- ]{0,40}?\s+(?:and|&)\s+[A-Z][\w'\u2019.\- ]{0,40}\s*$")


def _producer_name_warnings(record: dict) -> list:
    """A producer group written as a list of authors: 'Huihuang Fang et al.' holds; author-list shapes
    without a group word ('Smith, J.; Lee, K.', 'Smith and Jones') warn."""
    if record.get("record_type") != "evidence":
        return []
    pb = (record.get("attribution") or {}).get("produced_by") if isinstance(record.get("attribution"), dict) else None
    group = str((pb or {}).get("group") or "").strip() if isinstance(pb, dict) else ""
    if not group:
        return []
    fix = ("Name the group that produced the result by its principal investigator as the byline prints it, "
           "'<PI name> group' (two PIs: 'A and B groups'), and add produced_by.organization. The discovery engine "
           "matches this field exactly to judge whether two results are independent, so one group must be "
           "written the same way in every record.")
    if _AUTHOR_LIST_GROUP.search(group):
        return [{"code": "PRODUCER_AS_AUTHOR_LIST", "path": "attribution/produced_by/group",
                 "message": f"produced_by.group '{group}' is a list of authors. {fix}"}]
    if not _GROUP_WORDS.search(group) and (_SEMICOLON_AUTHORS.search(group) or _AND_AUTHORS.match(group)):
        return [{"code": "PRODUCER_NAME_FORMAT", "path": "attribution/produced_by/group",
                 "message": f"produced_by.group '{group}' reads like author names. {fix}"}]
    return []


def link_target_warnings(record: dict, known_ids) -> list:
    """Links whose target is no record the repository knows: not published, and not held by this uploader.
    The validator cannot see the repository; the API calls this with the ids it found."""
    known = {str(k).strip() for k in known_ids or ()}
    out = []
    for i, link in enumerate(record.get("links") if isinstance(record.get("links"), list) else []):
        target = str((link or {}).get("target") or "").strip() if isinstance(link, dict) else ""
        if target and target not in known:
            out.append({"code": "LINK_TARGET_NOT_FOUND", "path": f"links/{i}/target",
                        "message": (f"No record {target} exists in the repository yet. A link names a record that "
                                    f"exists; within a batch, upload the target first or link from the later "
                                    f"record. Check the id.")})
    return out


def _computation_role_warnings(record: dict) -> list:
    """A computation.method on a record whose fields say measurement: the fit of this measurement,
    or a computed result that is a record of its own. Only the record says which, so this warns."""
    if record.get("record_type") != "evidence":
        return []
    comp = record.get("computation") if isinstance(record.get("computation"), dict) else {}
    if not (isinstance(comp.get("method"), dict) and comp.get("method")):
        return []
    calc, meas = _calculation_votes(record)
    if calc or not meas:
        return []
    return [{
        "code": "COMPUTATION_ON_MEASUREMENT", "path": "computation/method",
        "message": ("This record declares computation.method while its other fields say it is a measurement "
                    f"({', '.join(meas)}). A method that analysed this measurement (an EXAFS fit with a "
                    "scattering code, a Rietveld refinement, an equivalent-circuit fit) is part of the measurement: "
                    "name the step in "
                    "measurement.processing.steps and point recipe_link to the analysis files. A method that "
                    "computed a result of its own (a DFT energy, a microkinetic rate) makes that result a separate "
                    "calculation record, linked to this one with derived_from or validates.")}]


def _claims_calculation(record: dict) -> bool:
    """system.domain, system.technique or source_type says the record is a calculation."""
    calc, _ = _calculation_votes(record)
    return any(v.split("=", 1)[0] in _CLAIM_FIELDS for v in calc)


def _dois_in(node) -> set:
    found = set()
    if isinstance(node, dict):
        for v in node.values():
            found |= _dois_in(v)
    elif isinstance(node, list):
        for v in node:
            found |= _dois_in(v)
    elif isinstance(node, str):
        found |= {m.rstrip(".,;:)]}").lower() for m in _DOI.findall(node)}
    return found


def _assets(record: dict) -> list:
    return [a for a in record.get("assets") or [] if isinstance(a, dict)] if isinstance(record.get("assets"), list) else []


def _citations(record: dict) -> list:
    """(relation, dois, citation) for every asset that carries a citation object."""
    return [(a["citation"].get("relation"), _dois_in(a["citation"].get("doi")) | _dois_in(a.get("uri")), a["citation"])
            for a in _assets(record) if isinstance(a.get("citation"), dict)]


def _cites_a_source(record: dict) -> bool:
    for a in _assets(record):
        c = a.get("citation") if isinstance(a.get("citation"), dict) else None
        doi_uri = "doi.org/" in str(a.get("uri") or "")
        if c is None and doi_uri:
            return True
        if c is not None and c.get("relation") in (None, "source") and (
                c.get("doi") or (c.get("title") and c.get("year")) or doi_uri):
            return True
    return False


def _publication_signs(record: dict) -> list:
    """What in a record says its numbers were taken from a publication."""
    signs = []
    meas_b = record.get("measurement") if isinstance(record.get("measurement"), dict) else {}
    proc = meas_b.get("processing") if isinstance(meas_b.get("processing"), dict) else {}
    hit = sorted(PUBLICATION_EXTRACTION_STEPS & {x for x in proc.get("steps") or [] if isinstance(x, str)})
    if hit:
        signs.append(f"the processing step '{hit[0]}'")
    desc = record.get("descriptors") if isinstance(record.get("descriptors"), dict) else {}
    blocks = [b for b in (meas_b.get("series") or []) + (desc.get("outputs") or []) if isinstance(b, dict)]
    if any(b.get("source_figure_ref") or b.get("source_figure_refs") for b in blocks):
        signs.append("references to the source's figures")
    if any(any(a.get(f) for f in _FIGURE_ASSET_FIELDS) for a in _assets(record)):
        signs.append("figure captions or page numbers of a publication on an asset")
    if any(rel == "source" for rel, _, _ in _citations(record)):
        signs.append("a citation with relation 'source'")
    return signs


def _code_home_errors(record: dict) -> list:
    """A calculation of any record type states its code once, in computation.method.code."""
    sysb = record.get("system") if isinstance(record.get("system"), dict) else {}
    if not _claims_calculation(record):
        return []
    cfg = sysb.get("configuration") if isinstance(sysb.get("configuration"), dict) else {}
    where = (["system.instrument"] if sysb.get("instrument") else []) + [
        f"system.configuration.{k}" for k in cfg if _CODE_KEY.fullmatch(k)]
    if not where:
        return []
    return [{
        "code": "CODE_OUTSIDE_METHOD", "path": "system/instrument" if sysb.get("instrument") else "system/configuration",
        "message": (f"A calculation states its code in one place, computation.method.code (with code_version "
                    f"when known); this record also uses {', '.join(where)}. system.instrument describes "
                    f"measurement hardware and stays empty for a calculation. Move the code to "
                    f"computation.method.code and name the computer in system.facility (facility_name, "
                    f"organization, cluster).")}]


_AUTHORS_PLACEHOLDER = re.compile(
    r"^(?:the\s+)?(?:(?:paper|publication|study|source|article)['\u2019]?s?\s+|original\s+)?authors?"
    r"(?:\s+of\s+the\s+(?:paper|publication|study|source|article))?$", re.I)


def _names_someone(value: str) -> bool:
    """A producer group or organization that names a group or an institution, not a placeholder."""
    v = (value or "").strip()
    return bool(v) and v.lower() not in _ORG_PLACEHOLDERS and not _AUTHORS_PLACEHOLDER.match(v)


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
                        "computed energy is 'computational'.")})

    calc, meas = _calculation_votes(record)
    if calc and meas:
        errors.append({
            "code": "DOMAIN_INCONSISTENT", "path": "system/domain",
            "message": (f"This record says both calculation ({', '.join(calc)}) and measurement "
                        f"({', '.join(meas)}). A calculation (any simulation or computed quantity, including a "
                        f"simulated spectrum) has {_CALCULATION_ENVELOPE}. A measurement has system.domain "
                        f"'experimental', a physical sample_form and a physical environment. A calculation "
                        f"reported in a paper is still a calculation: keep source_type 'literature' and set the "
                        f"calculation fields (System wiki, system.domain_signals).")})

    is_calc = _claims_calculation(record)
    comp = record.get("computation") if isinstance(record.get("computation"), dict) else {}
    method = comp.get("method") if isinstance(comp.get("method"), dict) else {}
    if is_calc and not method.get("family"):
        errors.append({
            "code": "COMPUTATION_METHOD_MISSING", "path": "computation/method",
            "message": (f"A calculation declares its method in computation.method: at least "
                        f"{', '.join(METHOD_REQUIRED)}, plus what its family requires (Computation wiki, method "
                        f"requirements). A computed number is comparable with another only next to the method "
                        f"that produced it. For a calculation taken from a paper, copy the method the paper "
                        f"states and write 'not_reported' for what it leaves out.")})
    elif is_calc:
        family = method.get("family")
        need = list(dict.fromkeys([f for f in METHOD_REQUIRED if f != "family"]
                                  + list(METHOD_REQUIRED_BY_FAMILY.get(family) or [])))
        missing = [f for f in need if not str(method.get(f) or "").strip()]
        if missing:
            errors.append({
                "code": "COMPUTATION_METHOD_INCOMPLETE", "path": "computation/method",
                "message": (f"computation.method lacks {', '.join(missing)}. A '{family}' calculation declares "
                            f"{', '.join(need)}: they make a computed number comparable with another. Write "
                            f"'not_reported' for a value the source does not state (Computation wiki, method "
                            f"requirements).")})

    pb = (record.get("attribution") or {}).get("produced_by") if isinstance(record.get("attribution"), dict) else None
    pb = pb if isinstance(pb, dict) else {}
    org = str(pb.get("organization") or "").strip()
    group = str(pb.get("group") or "").strip()
    # A paper always names its authors, so a placeholder group on a literature record is an error.
    # Outside the literature a producer may honestly be unnameable (anonymized or industrial data): a
    # placeholder group there is accepted with PRODUCED_BY_UNNAMED, and the engine reads it as absent.
    unnameable = st != "literature" and bool(group)
    if not (_names_someone(group) or _names_someone(org) or unnameable):
        who = {
            "literature": "the group of the paper's authors (group, organization, people)",
            "database": "the group that produced the database entry; the database itself goes in assets",
            "computation": "the group that ran the calculation: your own group if you ran it",
        }.get(st, "the group that made the measurement: a curator uploading another lab's data names that lab")
        errors.append({
            "code": "PRODUCED_BY_MISSING", "path": "attribution/produced_by",
            "message": ((f"'{group or org}' names no one. " if (group or org) else "")
                        + f"State who produced this result in attribution.produced_by {{group, organization, "
                        f"people}}: {who}. uploaded_by records only who deposited the record. The source "
                        f"(source_type '{st}') and the producer together say whether a result is a published "
                        f"calculation, a calculation the uploader ran, or someone else's; two results from one "
                        f"group are not independent evidence. Use the canonical organization names in "
                        f"system.organizations.")})

    if st not in ("literature", "database"):
        signs = _publication_signs(record)
        if signs:
            errors.append({
                "code": "LITERATURE_SOURCE_UNDECLARED", "path": "source_type",
                "message": (f"This record takes its numbers from a publication ({'; '.join(signs)}), so its "
                            f"source_type is 'literature', not '{st}'. Set source_type 'literature', keep "
                            f"system.domain for the kind of work the paper did (a calculation stays "
                            f"'computational'), cite the paper with relation 'source' and name its authors' group "
                            f"in attribution.produced_by (Record-Overview wiki, origins).")})
        identifiers = ((record.get("sample") or {}).get("material") or {}).get("identifiers") \
            if isinstance(record.get("sample"), dict) and isinstance(record["sample"].get("material"), dict) else None
        declared = set().union(*(dois for rel, dois, _ in _citations(record) if rel))
        undeclared = sorted(_dois_in(record) - _dois_in(identifiers) - declared)
        if undeclared:
            errors.append({
                "code": "CITATION_RELATION_UNDECLARED", "path": "assets",
                "message": (f"This record mentions {len(undeclared)} publication(s) ({', '.join(undeclared[:3])}) "
                            f"without saying how each relates to it. Give each an asset with citation {{doi, "
                            f"relation}} (content_role 'documentation'). relation 'source': the record's numbers "
                            f"were taken from it, its figures, tables, supplementary information or data "
                            f"repository, and the record is then source_type 'literature'. 'reports_this_work': "
                            f"it reports the same measurement or calculation and the numbers come from the "
                            f"producer's own data. 'reference': related work, such as a method paper or the "
                            f"experiment a calculation models.")})

    if st == "literature" and not _cites_a_source(record):
        errors.append({
            "code": "LITERATURE_CITATION_MISSING", "path": "assets",
            "message": ("A literature record carries its source: an asset with citation {authors, title, "
                        "journal, year, doi, relation: 'source'} and uri 'https://doi.org/<doi>' (content_role "
                        "'documentation'). Without the source a reader cannot check the value or tell whether two "
                        "records come from the same paper.")})

    if st == "database" and not any(
            isinstance(a.get("database_entry"), dict) and a["database_entry"].get("database")
            and a["database_entry"].get("entry_id") for a in _assets(record)):
        errors.append({
            "code": "DATABASE_ENTRY_MISSING", "path": "assets",
            "message": ("A database record names the entry it was taken from: an asset with database_entry "
                        "{database, entry_id, collection, version} and a uri that opens the entry, or its "
                        "collection when the database has no page per entry. entry_id is the database's own "
                        "identifier for that entry; collection is the dataset or publication the database files "
                        "it under.")})
    return errors


# ---------------------------------------------------------------------------
# Stored records under the current rules (2026-09-27).
#
# The rules tighten over time and a stored record is never rejected retroactively, so an
# owner needs to see which of their records no longer meet the current contract, and why,
# without anyone writing to them. The portal and GET /records/attention both use this.
# ---------------------------------------------------------------------------
def current_contract_report(records: list) -> dict:
    """Which of these stored records fail the current rules, with each error."""
    from collections import Counter
    needing, by_code = [], Counter()
    for record in records:
        if not isinstance(record, dict):
            continue
        res = validate_record_full(record)
        if res.get("valid"):
            continue
        errors = []
        for layer, fallback in (("schema_errors", "SCHEMA"), ("vocabulary_errors", "VOCABULARY"),
                                ("semantic_errors", "SEMANTIC")):
            for e in res.get(layer) or []:
                errors.append({"code": e.get("code") or fallback, "path": e.get("path"),
                               "message": e.get("message")})
        by_code.update({e["code"] for e in errors})
        needing.append({"record_id": str(record.get("record_id") or "").strip(),
                        "record_domain": record.get("record_domain"), "errors": errors})
    return {"checked": len(records), "needing_update": len(needing),
            "by_code": dict(by_code.most_common()), "records": needing}


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


# Warnings that HOLD a record (2026-10-01). A record that passes every hard rule but carries one of these is
# stored privately and published only once it is fixed: it is never public, never searched, never read by
# the discovery engine. On 2026-09-29 one pipeline uploaded 147 records, each with a warning that named its
# problem, and published all of them: an agent reads a success response as success. Each code here has a
# remedy by edit and, on the live repository, fired only on that pipeline's records. NUMBER_AS_TEXT and
# COMPUTATION_ON_MEASUREMENT stay warnings: a value below a detection limit has no number form yet, and a
# refinement may sit on a measurement legitimately.
HOLD_CODES = frozenset({
    "SAMPLE_NOT_ONE_MATERIAL", "SAMPLE_NAME_CITES_A_PAPER", "MULTIPLE_SOURCES", "QUALIFIER_NOT_A_PRODUCT",
    "SENTENCE_AS_VALUE", "PERFORMANCE_EX_SITU", "VOCABULARY_SUBSTITUTION", "PRODUCED_BY_UNNAMED",
    "COMPETING_HYPOTHESIS_LANGUAGE", "SECOND_HAND_SOURCE", "CONDITIONS_IN_SAMPLE_NAME",
    "PRODUCER_AS_AUTHOR_LIST",
})


def outcome(result: dict) -> tuple:
    """('reject' | 'hold' | 'publish', sorted hold codes) for a validation result."""
    if not result.get("valid"):
        return "reject", []
    held = sorted({w.get("code") for w in result.get("warnings") or []} & HOLD_CODES)
    return ("hold" if held else "publish"), held


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
    vocabulary_errors = vocabulary_errors + _origin_errors(record) + _code_home_errors(record)

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
    warnings = (warnings + adr_warnings + rx_warnings + cell_warnings + _one_result_warnings(record)
                + _computation_role_warnings(record) + _producer_warnings(record)
                + _magnitude_warnings(record) + _competing_hypothesis_warnings(record)
                + _second_hand_and_name_condition_warnings(record) + _prose_condition_warnings(record)
                + _producer_name_warnings(record))
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
    result["outcome"], hold = outcome(result)
    if hold:
        result["hold"] = hold
    return result


def format_errors_flat(result: dict) -> list:
    """Flatten a validation result into 'path: message' strings for UIs."""
    return [f"{e['path']}: {e['message']}" for e in result.get("errors", [])]
