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


def _warn_codes(record):
    return {w["code"] for w in (validation.validate_record_full(record).get("warnings") or [])}


def test_computation_method_completeness_nudge():
    """A computation record must declare computation.method (functional + code) so its
    result is comparable across functionals — else an advisory warning fires. Records
    that DO tag their method (the XAS/microkinetic convention) are not nagged."""
    # no computation block at all -> MISSING
    no_method = {"record_type": "evidence", "record_domain": "simulation",
                 "source_type": "computation", "sample": {}}
    assert "COMPUTATION_METHOD_MISSING" in _warn_codes(no_method)
    # method present but no functional_name -> INCOMPLETE
    partial = {"record_type": "evidence", "source_type": "computation",
               "computation": {"method": {"family": "DFT"}}}
    assert "COMPUTATION_METHOD_INCOMPLETE" in _warn_codes(partial)
    # fully tagged (family + functional_name) -> no method nag
    tagged = {"record_type": "evidence", "source_type": "computation",
              "computation": {"method": {"family": "DFT", "functional_name": "RPBE",
                                         "functional_class": "GGA", "code": "VASP"}}}
    codes = _warn_codes(tagged)
    assert "COMPUTATION_METHOD_MISSING" not in codes
    assert "COMPUTATION_METHOD_INCOMPLETE" not in codes
    # a non-computation (performance) record is never subject to this check
    perf = {"record_type": "evidence", "record_domain": "performance", "source_type": "experiment"}
    assert "COMPUTATION_METHOD_MISSING" not in _warn_codes(perf)


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
    assert any(w["code"] == "COMPONENT_SET_EXCEEDS_TOTAL" for w in res.get("warnings", []))

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

    # No-links warning fires on linkless, untagged record
    r = json.loads(json.dumps(base))
    r["links"] = []
    r.pop("tags", None)  # tags also satisfy the grouping nudge — clear to test NO_LINKS alone
    res = validation.validate_record_full(r)
    assert any(w["code"] == "NO_LINKS" for w in res.get("warnings", []))

    # A tag alone suppresses NO_LINKS (a tagged record is grouped)
    r["tags"] = ["some-campaign"]
    res = validation.validate_record_full(r)
    assert not any(w["code"] == "NO_LINKS" for w in res.get("warnings", []))


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


def test_record_tags():
    """Free-form grouping tags: a tagged record is valid and not NO_LINKS-nagged."""
    base = json.loads((REPO / "examples" / "co2rr_performance_record.json").read_text())
    r = json.loads(json.dumps(base)); r["tags"] = ["jcap-hte", "nifecoce-oer-screen"]
    res = validation.validate_record_full(r)
    assert res["valid"]
    assert not any(w["code"] == "NO_LINKS" for w in res.get("warnings", [])), \
        "a tagged record is grouped (by tag) and must not trigger NO_LINKS"
    # hygiene: whitespace-padded and duplicate tags rejected
    r2 = json.loads(json.dumps(base)); r2["tags"] = ["  pad"]
    assert not validation.validate_record_full(r2)["valid"]
    r3 = json.loads(json.dumps(base)); r3["tags"] = ["x", "x"]
    assert not validation.validate_record_full(r3)["valid"]


class TestFEChargeBalanceAndSigmaZero:
    """Repository audit 2026-08-08, all 1722 records / 4433 descriptors.

    Two advisory checks were measurably one-sided:

    * `FE_SUM_EXCEEDS_UNITY` summed roll-up descriptors together with their own components, so
      it double-counted by construction. It fired on 10 output blocks and **all 10 were false
      positives**; blocks genuinely over unity numbered ZERO. Meanwhile 37 blocks close below
      0.90, down to 0.73, and nothing looked in that direction.
    * `SIGMA_ZERO_PLACEHOLDER` fired only when the depositor had already written "not" into a
      free-text note. 792 descriptors carry sigma=0.0; it caught 32 and was blind to 760. A
      detector that fires only on the honest depositor is not a detector.
    """

    def _block(self, *pairs, **unc):
        return {"descriptors": {"outputs": [{"descriptors": [
            {"name": n, "kind": "absolute", "source": "imported", "value": v,
             "uncertainty": dict(unc) if unc else {"sigma": 0.01}}
            for n, v in pairs]}]}}

    def _codes(self, rec):
        w, i = validation._warning_checks(rec)[:2]
        return [x["code"] for x in list(w) + list(i)]

    def test_a_rollup_beside_its_components_is_not_double_counted(self):
        """The exact shape of all 10 historical false positives."""
        rec = self._block(("faradaic_efficiency.CH4", 0.15), ("faradaic_efficiency.CO", 0.05),
                          ("faradaic_efficiency.HCOO", 0.05), ("faradaic_efficiency.C1", 0.25),
                          ("faradaic_efficiency.C2H4", 0.27), ("faradaic_efficiency.C2H5OH", 0.10),
                          ("faradaic_efficiency.CH3COO", 0.05), ("faradaic_efficiency.n_C3H7OH", 0.03),
                          ("faradaic_efficiency.C2plus", 0.45), ("faradaic_efficiency.H2", 0.07))
        assert "COMPONENT_SET_EXCEEDS_TOTAL" not in self._codes(rec)

    def test_a_genuine_percent_encoding_still_fires(self):
        rec = self._block(("faradaic_efficiency.CH4", 40.0), ("faradaic_efficiency.H2", 55.0))
        assert "COMPONENT_SET_EXCEEDS_TOTAL" in self._codes(rec)

    def test_the_band_is_symmetric_at_ten_percent(self):
        """Repository distribution: 72.2% of blocks land in 0.90-1.10 and NOTHING exceeds 1.10.
        Calibration to better than ~10% is hard; a slate at 0.93 or 1.07 is ordinary science."""
        for tot in (0.93, 1.07):
            rec = self._block(("faradaic_efficiency.CH4", tot - 0.5),
                              ("faradaic_efficiency.C2H4", 0.3), ("faradaic_efficiency.H2", 0.2))
            codes = self._codes(rec)
            assert "COMPONENT_SET_INCOMPLETE_UNDECLARED" not in codes
            assert "COMPONENT_SET_EXCEEDS_TOTAL" not in codes

    def test_an_undeclared_gap_asks_for_a_declaration(self):
        rec = self._block(("faradaic_efficiency.CH4", 0.15), ("faradaic_efficiency.CO", 0.05),
                          ("faradaic_efficiency.C2H4", 0.27), ("faradaic_efficiency.H2", 0.07))
        assert "COMPONENT_SET_INCOMPLETE_UNDECLARED" in self._codes(rec)

    def test_a_declared_gap_is_silent(self):
        """Unquantified minor products are normal. Said out loud, there is nothing to flag."""
        rec = self._block(("faradaic_efficiency.CH4", 0.15), ("faradaic_efficiency.CO", 0.05),
                          ("faradaic_efficiency.C2H4", 0.27), ("faradaic_efficiency.H2", 0.07))
        rec["descriptors"]["outputs"][0]["completeness"] = {
            "quantified": "major_components_only", "unquantified": ["liquid products"]}
        assert "COMPONENT_SET_INCOMPLETE_UNDECLARED" not in self._codes(rec)

    def test_a_declared_slate_survives_schema_validation(self):
        """The new field is optional and additive; additionalProperties is false on the block."""
        rec = self._block(("faradaic_efficiency.CH4", 0.5), ("faradaic_efficiency.H2", 0.4))
        rec["descriptors"]["outputs"][0]["completeness"] = {
            "quantified": "major_components_only", "unquantified": ["C3+"],
            "expected_total": 1.0, "notes": "GC did not resolve liquids"}
        errs = [e for e in validation.validate_record_full(rec)["schema_errors"]
                if "completeness" in str(e)]
        assert not errs, errs

    def test_a_modest_gap_is_gentler_than_a_large_one(self):
        """0.85 is plausible unquantified minors; 0.75 deserves a depositor's eye."""
        modest = self._block(("faradaic_efficiency.CH4", 0.40), ("faradaic_efficiency.C2H4", 0.30),
                             ("faradaic_efficiency.H2", 0.15))
        large = self._block(("faradaic_efficiency.CH4", 0.35), ("faradaic_efficiency.C2H4", 0.25),
                            ("faradaic_efficiency.H2", 0.15))
        w_m, i_m = validation._warning_checks(modest)[:2]
        w_l, i_l = validation._warning_checks(large)[:2]
        assert any(x["code"] == "COMPONENT_SET_INCOMPLETE_UNDECLARED" for x in i_m)
        assert any(x["code"] == "COMPONENT_SET_INCOMPLETE_UNDECLARED" for x in w_l)

    def test_over_closure_still_warns_because_it_has_no_benign_reading(self):
        rec = self._block(("faradaic_efficiency.CH4", 0.70), ("faradaic_efficiency.C2H4", 0.30),
                          ("faradaic_efficiency.H2", 0.20))
        assert "COMPONENT_SET_EXCEEDS_TOTAL" in self._codes(rec)

    def test_a_rollup_that_disagrees_with_its_components_is_flagged(self):
        rec = self._block(("faradaic_efficiency.CH4", 0.15), ("faradaic_efficiency.CO", 0.05),
                          ("faradaic_efficiency.HCOO", 0.05), ("faradaic_efficiency.C1", 0.40))
        assert "AGGREGATE_DISAGREES_WITH_ITS_MEMBERS" in self._codes(rec)

    def test_a_consistent_rollup_is_silent(self):
        rec = self._block(("faradaic_efficiency.CH4", 0.15), ("faradaic_efficiency.CO", 0.05),
                          ("faradaic_efficiency.HCOO", 0.05), ("faradaic_efficiency.C1", 0.25))
        assert "AGGREGATE_DISAGREES_WITH_ITS_MEMBERS" not in self._codes(rec)

    def test_sigma_zero_without_a_basis_is_caught_without_a_confession(self):
        """The 760 the old detector could not see: no note, no basis, just sigma 0."""
        rec = self._block(("faradaic_efficiency.H2", 0.07), sigma=0.0, unit="fraction")
        assert "SIGMA_ZERO_PLACEHOLDER" in self._codes(rec)

    def test_sigma_zero_declared_exact_is_accepted(self):
        """A set point genuinely has no scatter. The check is about the SILENT zero."""
        rec = self._block(("faradaic_efficiency.H2", 0.07), sigma=0.0, basis="exact")
        assert "SIGMA_ZERO_PLACEHOLDER" not in self._codes(rec)

    def test_not_reported_is_expressible_without_lying_about_precision(self):
        rec = self._block(("faradaic_efficiency.H2", 0.07), sigma=None, basis="not_reported")
        assert "SIGMA_ZERO_PLACEHOLDER" not in self._codes(rec)

    def test_a_free_text_basis_is_a_vocabulary_signal_not_an_error(self):
        rec = self._block(("faradaic_efficiency.H2", 0.07), sigma=0.0,
                          basis="not estimated from source figure data")
        codes = self._codes(rec)
        assert "UNCERTAINTY_BASIS_NOT_IN_VOCABULARY" in codes
        assert "SIGMA_ZERO_PLACEHOLDER" not in codes

    def test_none_of_this_can_reject_a_record(self):
        """These are advisories. Acceptance is schema + vocabulary + semantic, and all 1722
        records in the repository remain valid after the change."""
        rec = self._block(("faradaic_efficiency.CH4", 0.15), ("faradaic_efficiency.H2", 0.07),
                          sigma=0.0)
        res = validation.validate_record_full(rec)
        assert not any(e for e in res["errors"] if "SIGMA_ZERO" in str(e) or "FE_SUM" in str(e))

    def test_the_validator_carries_no_chemistry(self):
        """The reason this class exists in this shape: a schema validator serving all of
        science must not hardcode one reaction's product list. Families and aggregates are
        DATA in data/vocabulary.json; the code only sums and compares."""
        import inspect
        src = inspect.getsource(validation._warning_checks)
        for token in ("faradaic", "CH4", "C2H4", "HCOO", "C2plus", "CO2"):
            assert token not in src, "chemistry leaked back into the validator: %s" % token

    def test_a_record_may_declare_its_own_aggregation_inline(self):
        """Generic path: no vocabulary entry needed, the record says what aggregates what."""
        rec = self._block(("selectivity.a", 0.3), ("selectivity.b", 0.2), ("selectivity.total", 0.9))
        ds = rec["descriptors"]["outputs"][0]["descriptors"]
        ds[-1]["aggregates"] = ["selectivity.a", "selectivity.b"]
        assert "AGGREGATE_DISAGREES_WITH_ITS_MEMBERS" in self._codes(rec)
        ds[-1] = dict(ds[-1], value=0.5)
        assert "AGGREGATE_DISAGREES_WITH_ITS_MEMBERS" not in self._codes(rec)
