# Provenance and Independent Evidence

## Purpose

Two results corroborate each other only to the extent that they could have failed **for
different reasons**. Two measurements on the same specimen, in the same sitting, on the same
instrument are wrong *together* — they are one determination written down twice, however
separately they are stored. The same is true of two calculations at the same functional.

This page describes the fields that let a machine tell those cases apart, and what the ISAAC
discovery engine does with them.

**None of this is required.** Every field here is optional and additive; a record that omits all
of them validates exactly as before. What changes is how much *independent* support a downstream
consumer will credit it with.

## The fields

| Block | Field | What it says |
|---|---|---|
| `system` | `session.session_id` | The measurement **sitting** — one continuous period under one calibration on one instrument. |
| `system` | `session.calibration_ref` | Which calibration was in force. |
| `system` | `session.started_utc` | When the sitting began. |
| `attribution` | `produced_by.group` | Who **produced** the result — the group, not the depositor. |
| `attribution` | `produced_by.organization` | Their institution. Use a canonical name from the controlled vocabulary. |
| `attribution` | `produced_by.people` | Named contributors, where the source gives them. |
| `links` | `rel: same_sample_as` | This record and its target measure **the same specimen**. |
| `links` | `rel: replica_of` | This record is a deliberate **repeat** of its target. |

`produced_by` is deliberately distinct from `attribution.uploaded_by`. A curator depositing a
hundred records from twenty groups is **one uploader and twenty sources**, and only the second
number tells you anything about independence.

**For computed records there is no session field to fill.** The equivalent shared cause is the
calculational setup, and it already lives in `computation.method` — `code`, `code_version`,
`functional_name`, `pseudopotential`. Declare those and the engine reads them. See
[Computation DFT Method Specification](Computation-DFT-Method-Specification).

## How the discovery engine reads them

For projects created at `policy_version` ≥ 65, the engine groups cited evidence by **shared
cause of error** rather than by record identifier:

| Shared | Treated as |
|---|---|
| The same record | Not evidence twice — already collapsed |
| The same specimen (`same_sample_as`, `replica_of`) | **Correlated** — attenuated, does not raise `n_decisive` |
| The same instrument **and** session | **Correlated** |
| The same code + version + functional | **Correlated** |
| The same organization or group | **Robustness** — counts, but not as independence |
| The same functional, code unspecified | **Robustness** — shared approximation, weaker claim |
| Nothing above | **Independent** |

`n_decisive` — the count that decides whether a hypothesis reads `reliable` — counts **distinct
shared-cause clusters**, not distinct records.

### Silence is not correlation

A record with no `session`, no `produced_by` and no links yields **no signature**, and its
evidence stays **independent**. The engine will not infer that two records share a cause because
neither says. A missing field is not a claim.

The practical consequence runs the other way, though: **un-recorded provenance is scored as
independence you may not have earned.** If your two supporting records came off one bench in one
afternoon, and you do not say so, the engine will credit them as two independent confirmations.
Recording provenance makes your evidence *honest*, not weaker.

### Varying the method is not repeating yourself

Two calculations at **different** functionals share no key here, by construction. Varying the
functional is the standard robustness check in computational science and is scored as such —
it hardens a conclusion rather than duplicating it.

## Identifying institutions

Organization names are normalized through the controlled vocabulary, because `SLAC` and
`SLAC National Accelerator Laboratory` are one laboratory and scoring them as two silently
inflates how independent a body of evidence looks.

- `system.organizations` — canonical names, each carrying its **[ROR](https://ror.org) id**, the
  same registry funders and publishers use.
- `system.organization_aliases` — abbreviations mapped to canonical names.
- `system.organization_placeholders` — strings that occupy the organization slot and name no
  institution (`not_specified_in_source`, `literature`, `TBD`). These are treated as **absent**,
  never as a shared key: two records both saying `not_specified_in_source` are not thereby the
  same lab.

To add an institution, look it up at [ror.org](https://ror.org) and add the canonical name with
its id. **Do not invent an id.** An organization not in the vocabulary keeps its own key, so an
unlisted spelling reduces measured correlation rather than inventing it.

## Worked example

Two records supporting the same claim, same laboratory, same week:

```json
{ "record_id": "01...A",
  "system": { "instrument": { "instrument_name": "Gamry_G_300" },
              "facility":   { "organization": "SLAC National Accelerator Laboratory" },
              "session":    { "session_id": "2026-03-11-run7",
                              "calibration_ref": "cal-2026-03-10" } },
  "attribution": { "uploaded_by": "curator",
                   "produced_by": { "group": "Electrocatalysis", "organization": "SLAC National Accelerator Laboratory" } },
  "links": [ { "rel": "same_sample_as", "target": "01...B" } ] }
```

Because these declare one specimen and one session, a hypothesis citing both gets **one**
independent confirmation, not two. Omit the `session` and the `same_sample_as` link and it would
get two — which is the outcome this page exists to prevent.

## Related

- [System](System) — instrument, facility, technique
- [Links](Links) — typed record-to-record relationships
- [Attribution](Attribution) — who deposited, who produced
- [Computation DFT Method Specification](Computation-DFT-Method-Specification) — the computed equivalent of a session
- [Controlled Vocabulary](Controlled-Vocabulary) — canonical organizations and aliases
