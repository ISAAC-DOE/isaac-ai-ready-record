"""
ISAAC validation regression battery.

Two guarantees, enforced in CI on every PR:

1. The canonical example records ALWAYS validate (no rule change may break
   the documented templates).
2. The adversarial probes in tests/adversarial/ are rejected — or, where a
   probe targets a rule we have consciously not yet implemented, it is
   marked xfail with the workstream that will close it. The xfail list is
   therefore a living TODO: when a new rule lands, its probe flips from
   xfail to a hard assertion by deleting one line here.

Baseline at creation (2026-06-11, post wave-1 step 2): 8 of 15 probes
rejected (was 2 of 15 before the validator structural fixes).
"""

import json
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parent.parent / "portal"
sys.path.insert(0, str(PORTAL))

import validation  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
ADVERSARIAL = sorted((REPO / "tests" / "adversarial").glob("P*.json"))
EXAMPLES = sorted((REPO / "examples").glob("*.json"))

# Probes whose rules are consciously NOT yet implemented.
# Maps probe stem -> (workstream that will implement it, reason)
KNOWN_GAPS = {
    "P02_fe_sum_1p40": ("warning-tier FE_SUM_EXCEEDS_UNITY fires (verified below); hard error pending policy", "warning != rejection by design"),
    "P03_negative_ecsa": ("WS2 schema: numeric bounds per descriptor class", "wave 2"),
    "P04_value_as_dict_and_string": ("WS2 schema: kind-conditional value types", "wave 2"),
    "P05_series_condition_smuggling": ("WS3 semantic: series.conditions vs context consistency", "wave 2"),
    "P06_qc_compromised_evidence_na": ("WS2 schema: qc.status enum + conditional evidence", "wave 2"),
    "P07_qc_invented_status": ("WS2 schema: qc.status enum", "wave 2"),
    "P08_epoch_1970_reversed_times": ("WS3 semantic: created_utc >= acquired, plausible-era check", "warning tier, wave 2"),
    "P09_rhe_5V_co2rr": ("WS3 semantic: potential plausibility per scale/reaction", "wave 2"),
    "P10_duplicate_descriptor": ("WS3 semantic: unique descriptor names per block", "wave 2"),
    "P12_ragged_and_missing_values": ("WS3 semantic: series channel length consistency", "wave 2"),
    "P13_negative_T_conc_pH19": ("WS2 schema: physical bounds (T>0, conc>=0, pH range)", "wave 2"),
    "P14_fake_ulid_self_link": ("WS3 semantic: link target existence + self-link rejection", "wave 2 (needs DB)"),
}


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_canonical_examples_pass(path):
    """Documented example records must always validate."""
    record = json.loads(path.read_text())
    result = validation.validate_record_full(record)
    assert result["valid"], (
        f"Canonical example {path.name} fails validation: "
        f"{result['errors'][:3]}"
    )


@pytest.mark.parametrize("path", ADVERSARIAL, ids=[p.stem for p in ADVERSARIAL])
def test_adversarial_probes_rejected(path):
    """Adversarial records must be rejected (or xfail with a workstream tag)."""
    record = json.loads(path.read_text())
    result = validation.validate_record_full(record)

    if path.stem == "P00_control_valid":
        assert result["valid"], f"Control probe must PASS but failed: {result['errors'][:3]}"
        return

    if path.stem in KNOWN_GAPS and result["valid"]:
        workstream, reason = KNOWN_GAPS[path.stem]
        pytest.xfail(f"known gap — {workstream} ({reason})")

    assert not result["valid"], (
        f"Adversarial probe {path.name} was ACCEPTED — a validation rule "
        f"has regressed or the probe needs updating."
    )


def test_degraded_flag_surfaces():
    """If a validation layer throws, the result must say so visibly."""
    import ontology
    original = ontology.validate_record_vocabulary
    ontology.validate_record_vocabulary = lambda r: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        result = validation.validate_record_full({"record_type": "evidence"})
        assert "degraded" in result, "degraded flag missing from result"
        assert result["degraded"][0]["layer"] == "vocabulary"
    finally:
        ontology.validate_record_vocabulary = original


def test_format_checker_active():
    """date-time format must actually be enforced (rfc3339-validator present)."""
    record = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())
    record["timestamps"]["created_utc"] = "2014-02-14 02:04:54+00:00"  # space, not T
    result = validation.validate_record_full(record)
    assert not result["valid"], "space-separated timestamp must be rejected"
    record["timestamps"]["created_utc"] = ""
    result = validation.validate_record_full(record)
    assert not result["valid"], "empty timestamp must be rejected"


def test_canonical_forms_enforced():
    """Decisions A & B: alias units and product tokens are rejected."""
    base = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())

    r = json.loads(json.dumps(base))
    r["descriptors"]["outputs"][0]["descriptors"][0]["unit"] = "mA_cm-2"
    assert not validation.validate_record_full(r)["valid"]

    r = json.loads(json.dumps(base))
    r["descriptors"]["outputs"][0]["descriptors"][1]["name"] = "faradaic_efficiency.acetate"
    assert not validation.validate_record_full(r)["valid"]

    r = json.loads(json.dumps(base))
    r["descriptors"]["outputs"][0]["descriptors"][1]["name"] = "faradaic_efficiency.banana"
    assert not validation.validate_record_full(r)["valid"], "unknown product token must be rejected"

    r = json.loads(json.dumps(base))
    r["descriptors"]["outputs"][0]["descriptors"][1]["name"] = "faradaic_efficiency.CH3COO"
    assert validation.validate_record_full(r)["valid"], "canonical token must pass"


def test_fe_range_check():
    """Percent-encoded FE in a fraction field is caught."""
    base = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())
    base["descriptors"]["outputs"][0]["descriptors"][1]["value"] = 91.0
    assert not validation.validate_record_full(base)["valid"]


def test_vocabulary_list_leaves_checked():
    """The list-leaf walker bug stays fixed: bad processing.steps rejected."""
    base = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())
    base["measurement"]["processing"]["steps"] = ["gc_analysis", "made_up_step_xyz"]
    result = validation.validate_record_full(base)
    assert not result["valid"], "non-vocabulary processing step must be rejected"


def test_potential_contract():
    """Potential Contract: ref-scale needs structured reference; derived values must recompute."""
    base = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())

    # Physical-reference scale without structured reference_electrode -> error
    r = json.loads(json.dumps(base))
    r["context"]["electrochemistry"]["potential_scale"] = "Ag/AgCl"
    del r["context"]["electrochemistry"]["reference_electrode"]
    assert not validation.validate_record_full(r)["valid"]

    # Derived value that contradicts its own conversion inputs -> error
    r = json.loads(json.dumps(base))
    ec = r["context"]["electrochemistry"]
    ec["potential_vs_RHE"] = {
        "value_V": 5.0,  # wrong on purpose
        "rhe_basis": "derived_nominal",
        "ir_corrected": "no",
        "conversion": {"offset_V_vs_SHE_used": 0.210, "pH_used": 6.8,
                        "formula": "E_RHE = E_meas + offset_V_vs_SHE + 0.0591*pH"},
    }
    assert not validation.validate_record_full(r)["valid"]

    # Honest null: not-convertible with explicit reason -> passes
    r = json.loads(json.dumps(base))
    r["context"]["electrochemistry"]["potential_vs_RHE"] = {
        "value_V": None, "rhe_basis": "not_convertible_no_pH"}
    assert validation.validate_record_full(r)["valid"]

    # Null value with a value-bearing basis -> schema rejects
    r = json.loads(json.dumps(base))
    r["context"]["electrochemistry"]["potential_vs_RHE"] = {
        "value_V": None, "rhe_basis": "derived_nominal"}
    assert not validation.validate_record_full(r)["valid"]


def test_warnings_tier():
    """Warnings never block; the right codes fire on the right gaps."""
    base = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())

    # FE sum > 1.05 -> accepted WITH warning
    r = json.loads(json.dumps(base))
    for d in r["descriptors"]["outputs"][0]["descriptors"]:
        if d["name"].startswith("faradaic_efficiency."):
            d["value"] = 0.6
    res = validation.validate_record_full(r)
    assert res["valid"], "FE-sum is a warning, must not block"
    assert any(w["code"] == "FE_SUM_EXCEEDS_UNITY" for w in res.get("warnings", []))

    # Galvanostatic with no potential -> GALVANOSTATIC_NO_POTENTIAL warning
    r = json.loads(json.dumps(base))
    ec = r["context"]["electrochemistry"]
    ec["control_mode"] = "galvanostatic"
    ec["current_setpoint_mA_cm2"] = -200
    del ec["potential_setpoint_V"]
    del ec["potential_vs_RHE"]
    # strip potential-named descriptors/channels for the test
    r["measurement"]["series"] = []
    res = validation.validate_record_full(r)
    assert res["valid"]
    assert any(w["code"] == "GALVANOSTATIC_NO_POTENTIAL" for w in res.get("warnings", []))

    # And the honest not_reported marker silences it
    ec["potential_vs_RHE"] = {"value_V": None, "rhe_basis": "not_reported"}
    res = validation.validate_record_full(r)
    assert not any(w["code"] == "GALVANOSTATIC_NO_POTENTIAL" for w in res.get("warnings", []))

    # No-links warning fires on linkless record
    r = json.loads(json.dumps(base))
    r["links"] = []
    res = validation.validate_record_full(r)
    assert any(w["code"] == "NO_LINKS" for w in res.get("warnings", []))


def test_wave2_locks_and_teaching_errors():
    """Wave-2: locked blocks reject unknown fields with TEACHING messages."""
    base = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())

    r = json.loads(json.dumps(base))
    r["context"]["electrochemistry"]["scale_is_converted"] = False  # the JCAP stray
    res = validation.validate_record_full(r)
    assert not res["valid"]
    msg = res["schema_errors"][0]["message"]
    assert "Allowed fields here" in msg, "rejection must list allowed fields"
    assert "request a schema addition" in msg, "rejection must teach the process"

    r = json.loads(json.dumps(base))
    r["system"]["stray_field"] = "x"
    assert not validation.validate_record_full(r)["valid"]

    # The designated open namespace still accepts anything (string values)
    r = json.loads(json.dumps(base))
    r["system"].setdefault("configuration", {})["my_beamline_quirk_setting"] = "42"
    assert validation.validate_record_full(r)["valid"]


def test_adr001_conventions():
    """ADR-001: sign convention, FE-as-claim, concept-home deny-list (warning tier)."""
    base = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())

    # Positive partial current under CO2RR -> SIGN_CONVENTION warning
    r = json.loads(json.dumps(base))
    template = json.loads(json.dumps(r["descriptors"]["outputs"][0]["descriptors"][0]))
    template.update({"name": "partial_current_density.C2H4", "value": 45.0, "unit": "mA/cm2"})
    r["descriptors"]["outputs"][0]["descriptors"].append(template)
    res = validation.validate_record_full(r)
    assert not res["valid"], "positive cathodic current is an ERROR since 2026-06-15"
    assert any(e.get("code") == "SIGN_CONVENTION" for e in res.get("errors", []))

    # Negative value -> no warning
    r["descriptors"]["outputs"][0]["descriptors"][-1]["value"] = -45.0
    res = validation.validate_record_full(r)
    assert res["valid"]

    # FE channel with measured_response role -> FE_ROLE_VIOLATION
    r = json.loads(json.dumps(base))
    r["measurement"]["series"].append({"series_id": "fe_trace", "channels": [
        {"name": "faradaic_efficiency.C2H4", "role": "measured_response", "unit": "fraction",
         "values": [0.3, 0.32, 0.31]}]})
    res = validation.validate_record_full(r)
    assert any(w["code"] == "FE_ROLE_VIOLATION" for w in res.get("warnings", []))

    # reference_electrode in configuration -> WRONG_BLOCK
    r = json.loads(json.dumps(base))
    r["system"].setdefault("configuration", {})["reference_electrode"] = "Ag/AgCl"
    res = validation.validate_record_full(r)
    assert not res["valid"], "misplaced cell-hardware key is an ERROR since 2026-06-15"
    assert any(e.get("code") == "WRONG_BLOCK" for e in res.get("errors", []))


def test_attribution_block():
    """Attribution: schema accepts the block; missing data_owner warns; bad role rejected."""
    base = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())
    res = validation.validate_record_full(base)
    assert res["valid"]
    assert not any(w["code"] == "NO_DATA_OWNER" for w in res.get("warnings", []))

    r = json.loads(json.dumps(base))
    del r["attribution"]
    res = validation.validate_record_full(r)
    assert res["valid"], "attribution is optional"
    assert any(w["code"] == "NO_DATA_OWNER" for w in res.get("warnings", []))

    r = json.loads(json.dumps(base))
    r["attribution"]["contributors"][0]["role"] = "boss"
    assert not validation.validate_record_full(r)["valid"], "unknown role must be rejected"


def test_calibrated_rhe_conversion():
    """Calibrated single-constant path: additive convention, recompute-checked."""
    base = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())
    r = json.loads(json.dumps(base))
    ec = r["context"]["electrochemistry"]
    ec["potential_setpoint_V"] = 0.1494614
    ec["potential_scale"] = "Ag/AgCl"
    ec["reference_electrode"] = {"type": "Ag/AgCl", "filling_solution": "3.0 M KCl",
                                  "offset_V_vs_SHE": 0.210, "offset_basis": "calibrated"}
    ec["potential_vs_RHE"] = {"value_V": round(0.1494614 + 1.04, 4), "rhe_basis": "derived_calibrated",
        "ir_corrected": "no",
        "conversion": {"input_path": "context.electrochemistry.potential_setpoint_V", "from_scale": "Ag/AgCl",
                       "formula": "E_RHE = E_measured + rhe_conversion_offset_V",
                       "rhe_conversion_offset_V": 1.04, "converted_by": "test"}}
    assert validation.validate_record_full(r)["valid"], "additive calibrated path must pass"

    # Caltech subtractive convention (negative offset + subtractive formula) must PASS
    # — the recompute follows the stated formula's sign (raw-data preservation).
    r2 = json.loads(json.dumps(r))
    c2 = r2["context"]["electrochemistry"]["potential_vs_RHE"]
    c2["conversion"]["rhe_conversion_offset_V"] = -1.04
    c2["conversion"]["formula"] = "E_RHE = E_measured - rhe_conversion_offset_V"
    c2["value_V"] = round(0.1494614 - (-1.04), 4)
    assert validation.validate_record_full(r2)["valid"], "Caltech subtractive form must pass"
    # A value that contradicts its own formula+offset is still caught
    c2["value_V"] = 0.5
    assert not validation.validate_record_full(r2)["valid"], "value/formula mismatch must be rejected"


def test_electrolyzer_voltage_optional():
    """Full-cell electrolyzer records: cell_voltage not half-cell potential;
    voltage may be absent; no inappropriate GALVANOSTATIC_NO_POTENTIAL nag."""
    rec = json.loads((REPO / "examples" / "electrolyzer_durability_record.json").read_text())
    res = validation.validate_record_full(rec)
    assert res["valid"], f"electrolyzer example must validate: {res['errors'][:3]}"
    assert not any(w["code"] == "GALVANOSTATIC_NO_POTENTIAL" for w in res.get("warnings", [])), \
        "full-cell electrolyzer (rhe_basis not_applicable + cell_voltage) must not be nagged"

    # Pure literature case: only current density + duration, no voltage, no measurement
    bare = json.loads(json.dumps(rec))
    bare.pop("measurement", None)
    bare["context"]["electrochemistry"]["potential_vs_RHE"] = {"value_V": None, "rhe_basis": "not_applicable"}
    bare["descriptors"]["outputs"][0]["descriptors"] = [
        {"name": "steady_state_current_density", "kind": "absolute", "source": "imported",
         "value": 1000.0, "unit": "mA/cm2", "uncertainty": {"sigma": None, "unit": "mA/cm2", "basis": "reported"}}]
    res = validation.validate_record_full(bare)
    assert res["valid"], "current-density-only electrolyzer must validate"
    assert not any(w["code"] == "GALVANOSTATIC_NO_POTENTIAL" for w in res.get("warnings", []))

    # not_applicable requires value_V null (Potential Contract invariant holds)
    bad = json.loads(json.dumps(rec))
    bad["context"]["electrochemistry"]["potential_vs_RHE"] = {"value_V": 1.8, "rhe_basis": "not_applicable"}
    assert not validation.validate_record_full(bad)["valid"], "not_applicable must force value_V null"
