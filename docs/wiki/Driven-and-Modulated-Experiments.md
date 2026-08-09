# Driven and Modulated Experiments

## Purpose

Some experiments do not hold their conditions constant. A potential is switched between two
levels, a light source is chopped, a temperature is ramped and cycled, a reactant is dosed in
pulses, a pressure is oscillated for modulation-excitation spectroscopy.

`context.modulation` records **which control variable is being driven, how, and — most
importantly — what the record's descriptors mean relative to that driving.**

This block is deliberately **not** part of `context.electrochemistry`. A modulated potential and
a chopped beam are the same structure with a different driven variable, and nesting it under one
technique would make it unusable for the others.

**Optional.** Omit it entirely for a static experiment; nothing changes for existing records.

## Why it matters more than it looks

A faradaic efficiency, a conversion or a rate measured **under modulation is an average over a
cycle**. It is a *different physical quantity* from the same descriptor measured while the
condition is held constant, even though it has the same name and unit.

Without a declaration, both look identical to a machine. A query for "CO₂RR performance at
−1.3 V" silently mixes steady-state runs with the average of an experiment that spent half its
time somewhere else entirely.

## The fields

| Field | Meaning |
|---|---|
| `driven_variable` | What is being driven — `potential`, `temperature`, `illumination`, `pressure`, `reactant_concentration`, `flow_rate`, `magnetic_field`, `electric_field`, `strain`, `pH`, `composition`. See the [Controlled Vocabulary](Controlled-Vocabulary). |
| `waveform` | `square`, `pulse_train`, `sine`, `triangle`, `sawtooth`, `step`, `ramp`, `arbitrary` |
| `frequency_Hz` **or** `period_s` | The rate. Give **one**, not both — they can disagree. |
| `duty_cycle` | Fraction of each cycle spent at the first level. |
| `levels[]` | The values the variable alternates between, each `{value, unit, hold_s?, label?}`. Two for a square wave, more for a multi-step programme. |
| `amplitude`, `mean` | `{value, unit}`. Peak-to-peak unless `notes` says otherwise; `mean` is the cycle time-average. |
| `n_cycles` | How many cycles the record covers. |
| **`descriptors_represent`** | **The load-bearing field — see below.** |
| `phase_deg` | For phase-resolved descriptors, the phase within the cycle. |
| `settling` | `{reached_periodic_steady_state, equilibration_cycles, basis}` — was a periodic steady state reached before data were taken? |
| `notes` | Free text. |

### `descriptors_represent`

| Value | Meaning |
|---|---|
| `cycle_averaged` | Averaged over whole cycles. **Not comparable to a static measurement of the same descriptor without saying so.** |
| `phase_resolved` | Resolved within the cycle; give `phase_deg`. |
| `at_a_single_level` | Measured while held at one level — a static value obtained inside a driven experiment. |
| `transient` | During the excursion itself. |
| `unspecified` | Honest, but limits what a consumer may do with the record. |

## Examples across domains

**Modulated electrode potential**

```json
"modulation": {
  "driven_variable": "potential", "waveform": "square", "frequency_Hz": 1.3,
  "levels": [{"value": -1.3, "unit": "V_RHE", "hold_s": 0.38},
             {"value": -0.6, "unit": "V_RHE", "hold_s": 0.38}],
  "descriptors_represent": "cycle_averaged",
  "settling": {"reached_periodic_steady_state": true, "equilibration_cycles": 50}
}
```

**Chopped illumination, phase-resolved**

```json
"modulation": {
  "driven_variable": "illumination", "waveform": "pulse_train",
  "frequency_Hz": 10, "duty_cycle": 0.5,
  "levels": [{"value": 100, "unit": "mW_cm2"}, {"value": 0, "unit": "mW_cm2"}],
  "descriptors_represent": "phase_resolved", "phase_deg": 90
}
```

**Pulsed reactant dosing**

```json
"modulation": {
  "driven_variable": "reactant_concentration", "waveform": "pulse_train",
  "period_s": 4.0, "duty_cycle": 0.05, "descriptors_represent": "cycle_averaged"
}
```

## Advisories

All advisory. **None can reject a record.**

| Code | When | Severity |
|---|---|---|
| `MODULATED_DESCRIPTORS_UNSPECIFIED` | the block is declared but `descriptors_represent` is unset | warning |
| `MODULATION_DRIVEN_VARIABLE_MISSING` | the block is declared but `driven_variable` is unset | warning |
| `MODULATION_RATE_OVERSPECIFIED` | both `frequency_Hz` and `period_s` given | warning |
| `MODULATION_EVIDENT_BUT_UNDECLARED` | assets or notes mention modulated/pulsed/chopped/square-wave, but no block is declared | info |

That last one exists because it happened: a batch of records arrived whose modulation survived
only inside asset filenames of the form `..._m1p3V_1p3Hz_13Hz.xlsx`. They validated, they were
stored, and **every machine reading them saw a static measurement at a single setpoint.** The
advisory fires on the words rather than on any technique, so it catches the same burial in any
domain.

## Related

- [Context](Context) — environment, temperature, electrochemistry, transport
- [Controlled Vocabulary](Controlled-Vocabulary) — driven variables, waveforms, descriptor semantics
- [Descriptors](Descriptors) — what the reported quantities are
- [Validation Rules](Validation-Rules) — the full advisory list
