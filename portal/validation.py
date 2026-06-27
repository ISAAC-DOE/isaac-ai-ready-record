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
            # Galvanostatic with no potential anywhere and no honest marker.
            # Full-cell electrolyzers (mea_cell/zero_gap_cell) report CELL VOLTAGE,
            # not a half-cell RHE potential — for them the half-cell projection is
            # not merely unreported but INAPPLICABLE, so this must not nag.
            pvr = ec.get("potential_vs_RHE") or {}
            full_cell = ec.get("cell_type") in ("mea_cell", "zero_gap_cell")
            voltage_accounted = (
                pvr.get("rhe_basis") in ("not_reported", "not_applicable")
                or full_cell
            )
            if ec.get("control_mode") == "galvanostatic" and not voltage_accounted:
                has_pot = False
                for o in (record.get("descriptors") or {}).get("outputs") or []:
                    for d in o.get("descriptors") or [] if isinstance(o, dict) else []:
                        nm = (d.get("name") or "").lower()
                        if "potential" in nm or "cell_voltage" in nm:
                            has_pot = True
                for s in (record.get("measurement") or {}).get("series") or []:
                    for ch in (s.get("channels") or []) + (s.get("independent_variables") or []):
                        nm = (ch.get("name") or "").lower()
                        if "potential" in nm or "cell_voltage" in nm or ch.get("unit") == "V_cell":
                            has_pot = True
                if not has_pot:
                    warnings.append({"code": "GALVANOSTATIC_NO_POTENTIAL", "path": "context/electrochemistry/potential_vs_RHE",
                                     "message": "Galvanostatic record carries no measured voltage anywhere. Half-cell study: "
                                                "add steady_state_potential (V_RHE) or declare potential_vs_RHE {value_V: null, "
                                                "rhe_basis: 'not_reported'}. Full-cell electrolyzer: report cell_voltage (V_cell) "
                                                "or declare potential_vs_RHE {value_V: null, rhe_basis: 'not_applicable'} — the "
                                                "half-cell potential does not exist for a 2-electrode device."})

        contribs = (record.get("attribution") or {}).get("contributors") or []
        if record.get("record_type") == "evidence" and not any(
                c.get("role") == "data_owner" for c in contribs if isinstance(c, dict)):
            warnings.append({"code": "NO_DATA_OWNER", "path": "attribution/contributors",
                             "message": "No data_owner declared. Evidence records should credit whose data this is "
                                        "(attribution.contributors, role=data_owner, ideally with ORCID)."})
        if not record.get("links") and not record.get("tags"):
            warnings.append({"code": "NO_LINKS", "path": "links",
                             "message": "Record has no links[] and no tags[]. Group it via a typed link (same_sample_as / derived_from / intended_comparison_target) or a tag."})

        # Computation records must DECLARE THEIR METHOD in computation.method (the
        # schema slots exist but are optional). Without functional + code, a computed
        # number is not comparable across records: a PBE adsorption energy is not the
        # same quantity as an RPBE or BEEF-vdW one, and the next agent cannot filter
        # for a database-consistent value. (The method belongs in computation.method,
        # NOT buried in free-text system/notes.)
        if record.get("source_type") == "computation" or domain == "simulation":
            method = ((record.get("computation") or {}).get("method") or {})
            if not method:
                warnings.append({"code": "COMPUTATION_METHOD_MISSING", "path": "computation/method",
                                 "message": "Computation record has no computation.method block. Declare at least "
                                            "family (DFT / microkinetic / AIMD / ...), functional_name (PBE, RPBE, "
                                            "BEEF-vdW, HSE06, ...), functional_class, basis_type, code + code_version. "
                                            "Results are NOT comparable across functionals/codes without it — and a "
                                            "method recorded only in free-text (system.notes) cannot be filtered or "
                                            "trusted for comparison."})
            else:
                miss = [f for f in ("family", "functional_name") if not method.get(f)]
                if miss:
                    warnings.append({"code": "COMPUTATION_METHOD_INCOMPLETE", "path": "computation/method",
                                     "message": f"computation.method is missing {miss}. functional_name "
                                                "(PBE / RPBE / BEEF-vdW / ...) and family are the comparability keys — "
                                                "an adsorption energy or barrier is only meaningful next to the "
                                                "functional that produced it."})

        qc = ((record.get("measurement") or {}).get("qc") or {})
        if qc.get("status") == "compromised" and str(qc.get("evidence", "")).strip().upper() in ("", "N/A", "NA", "NONE"):
            warnings.append({"code": "QC_COMPROMISED_NO_EVIDENCE", "path": "measurement/qc/evidence",
                             "message": "qc.status='compromised' requires a concrete evidence sentence (what is compromised and why). 'N/A' defeats the purpose."})

        # FE physics: per-output-block product sum
        for oi, o in enumerate((record.get("descriptors") or {}).get("outputs") or []):
            total = 0.0
            n_fe = 0
            for d in o.get("descriptors") or [] if isinstance(o, dict) else []:
                nm = d.get("name") or ""
                if nm.startswith("faradaic_efficiency.") and not nm.startswith("faradaic_efficiency.ratio"):
                    v = d.get("value")
                    if isinstance(v, (int, float)):
                        total += v
                        n_fe += 1
                # sigma=0 placeholder anti-pattern
                unc = d.get("uncertainty") or {}
                if unc.get("sigma") == 0.0 and "not" in str(unc.get("notes", "")).lower():
                    info.append({"code": "SIGMA_ZERO_PLACEHOLDER", "path": f"descriptors/outputs/{oi}",
                                 "message": f"Descriptor '{nm}': sigma=0.0 with a 'not reported' note reads as ZERO uncertainty to a machine. Prefer an explicit basis note without a numeric 0."})
            if n_fe >= 2 and total > 1.05:
                warnings.append({"code": "FE_SUM_EXCEEDS_UNITY", "path": f"descriptors/outputs/{oi}",
                                 "message": f"Sum of {n_fe} product Faradaic efficiencies = {total:.2f} > 1.05 in one output block — check for double counting or percent encoding."})

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
CATHODIC_REACTIONS = {"CO2RR", "CORR", "HER", "ORR", "NO3RR", "urea_synthesis"}
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
        reaction = ec.get("reaction")
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
    warnings = warnings + adr_warnings
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
