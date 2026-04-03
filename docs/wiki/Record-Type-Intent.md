# Record Type: Intent

## Purpose

An **intent** record declares a planned measurement, calculation, or synthesis that has not yet been executed. It captures the full specification — sample, system, context, computation method — so that an agent or human can execute the work and produce a corresponding **evidence** record linked via `derived_from`.

Intent records enable:

1. **Pre-registration of computational experiments** — specify all parameters before execution, preventing post-hoc rationalization.
2. **Calculation request queues** — an AI agent can create intent records that a compute cluster picks up and executes.
3. **Reproducibility auditing** — compare the intent (what was planned) against the evidence (what was done) to detect deviations.

## Validation Rules

Intent records follow all standard validation rules with these relaxations:

| Rule | Evidence | Intent |
|------|----------|--------|
| `descriptors` block required | **Yes** | **No** — nothing has been measured/computed yet |
| `measurement.qc.status` | `valid` / `compromised` / `failed` | `pending` |
| `timestamps.acquired_*` | Required | Optional (not yet acquired) |
| All other blocks | Standard rules | Standard rules |

The schema enforces this via conditional validation:

```json
"allOf": [{
  "if": { "properties": { "record_type": { "const": "evidence" } } },
  "then": { "required": ["descriptors"] }
}]
```

Intent records **may** omit `descriptors` entirely. If descriptors are present on an intent record, they represent **target values** or **expected ranges**, not measured results.

## Lifecycle

```
Intent Record                    Evidence Record
(planned)                        (executed)
     │                                │
     │  agent/human executes          │
     │  the specified work            │
     │                                │
     └──────── derived_from ──────────┘
```

### Preferred: Separate records

Create a new evidence record with `links: [{"rel": "derived_from", "target": "<intent_ULID>", "basis": "matched_computational_method"}]`. The intent record remains immutable.

### When the plan changes

If the computation method is revised before execution, create a **new** intent record linked to the original via `follows`:

```json
{
  "rel": "follows",
  "target": "<original_intent_ULID>",
  "basis": "unspecified",
  "notes": "Revised plan: changed k-points from 1x1x1 to 5x3x1 per referee suggestion."
}
```

## Agent Reasoning Contract

- **Never treat intent descriptor values as established facts.** If an intent record contains target values, they are aspirational, not measured.
- **Filter by `record_type`** when aggregating descriptors across records. Exclude `"intent"` from statistical analyses.
- **Compare intent vs evidence** to detect methodology drift: if the evidence record's `computation.method` differs from the intent, flag for review.

## Example

See `examples/dft_neb_intent_record.json` for a DFT CI-NEB calculation request specifying the full computational setup for an OCCO coupling barrier on a Cu-Ag bilayer junction, including method, slab model, potential treatment, and output quantity specification.
