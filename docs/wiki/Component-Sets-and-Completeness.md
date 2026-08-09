# Component Sets and Completeness

## Purpose

Some descriptors are **shares of a whole**: a product distribution, a selectivity slate, a phase
or composition breakdown, a set of branching ratios, isotope abundances. Their members are
expected to sum to a known total — usually 1.

Two things then become checkable, and both are optional to declare:

1. Whether a descriptor is an **aggregate** of others in the same block, so it is not
   double-counted against its own members.
2. Whether the set is **complete**, so a machine can tell an undeclared gap from a measurement
   that failed to balance.

**This applies only to descriptor families that genuinely are shares of a whole.** An adsorption
energy, a band gap, a barrier or an MLIP force has no total to close against, and nothing on
this page concerns them.

## Aggregates

A descriptor that sums others in the same output block declares them:

```json
{ "name": "faradaic_efficiency.C1", "kind": "absolute", "source": "imported",
  "value": 0.25, "uncertainty": { "sigma": 0.01 },
  "aggregates": ["faradaic_efficiency.CH4",
                 "faradaic_efficiency.CO",
                 "faradaic_efficiency.HCOO"] }
```

Two consequences, both arithmetic:

- The aggregate is **left out of any closure sum**, so it is not counted alongside the members
  it already contains.
- Where the members are also present, **their sum is checked against the aggregate's value**.
  A disagreement means one of the two was not read off the same data.

For common families, the aggregation is already known to the platform and needs no declaration —
see `descriptors.aggregate_descriptors` in the [Controlled Vocabulary](Controlled-Vocabulary).
A record's own `aggregates` takes precedence over that table, so a novel grouping needs no
vocabulary change.

## Completeness

Quantitative calibration to better than about 10% is difficult, and minor or hard-to-detect
species routinely go unquantified. **A slate that does not close is normal science.** What a
machine cannot do is tell an undeclared gap from a measurement that failed to balance.

So say which:

```json
"completeness": {
  "quantified": "major_components_only",
  "unquantified": ["liquid products", "carbonate crossover"],
  "expected_total": 1.0,
  "notes": "GC did not resolve C3+ alcohols"
}
```

| Field | Meaning |
|---|---|
| `quantified` | `all_components` — the set is intended to be exhaustive<br>`major_components_only` — minor/trace species not quantified<br>`partial` — a known subset only<br>`unknown` — not established |
| `unquantified` | What is believed present but not measured. **Naming it is what makes the gap re-usable** by the next reader. |
| `expected_total` | The whole the members are shares of. Defaults to 1.0 for fractions. |
| `notes` | Free text. |

A slate summing to 0.85 **with that stated** is better evidence than one summing to 0.85 in
silence. Declaring an incomplete set is not an admission of a bad measurement.

## What the validator does

All advisory. **None of it can reject a record** — acceptance remains schema + vocabulary +
semantic.

| Code | When | Severity |
|---|---|---|
| `COMPONENT_SET_EXCEEDS_TOTAL` | leaf members sum >110% of the expected total | warning |
| `COMPONENT_SET_INCOMPLETE_UNDECLARED` | members sum <90% and no `completeness` given | info (<20% missing) / warning (>20%) |
| `AGGREGATE_DISAGREES_WITH_ITS_MEMBERS` | an aggregate and its members are both present and disagree by >0.02 | warning |
| `SIGMA_ZERO_PLACEHOLDER` | `uncertainty.sigma: 0` with no `uncertainty.basis` | warning |
| `UNCERTAINTY_BASIS_NOT_IN_VOCABULARY` | a `basis` outside the canonical set | info |

The band is **symmetric at ±10%**, and nothing inside 0.90–1.10 is flagged. Declaring
`completeness` silences the under-closure advisory entirely. Over-closure still warns, because
unlike under-closure it has no benign reading.

## Uncertainty, and why `sigma: 0` is not "not reported"

To a machine, `sigma: 0` asserts the value is **exact** — and downstream scoring that divides by
a noise scale will treat it as infinitely precise. If the source reported no uncertainty, say
so:

```json
"uncertainty": { "sigma": null, "basis": "not_reported" }
```

Canonical `basis` values: `reported`, `digitization_estimate`, `assumed`, `propagated`,
`method`, `exact`, `not_reported`. Use `exact` for a genuine set point or an integer count —
that is what a legitimate zero looks like.

## Related

- [Descriptors](Descriptors) — naming, units, kinds
- [Controlled Vocabulary](Controlled-Vocabulary) — component families, aggregate map, uncertainty bases
- [Validation Rules](Validation-Rules) — the full advisory list
