# 2026-10-06-01 Links are stated, not inferred

- **Product:** repository. **Kind:** validator and wiki. **Owner:** D. Sokaras (PI); prepared by Claude.

## Problem

Every record without links or tags received the warning NO_LINKS: "Group it via a typed link
(same_sample_as / derived_from / intended_comparison_target) or a tag." Having no link is the usual correct
state, because records from one source are grouped by its DOI and records of one specimen by a shared
`sample_id`. The warning pushed in the wrong direction:
- an automated linker in our own uploads wrote 7,418 `same_sample_as` links from "same paper, same material
  name";
- a literature pipeline used `same_sample_as` for "same study" in 569 links.

The Links page defined only three of its eight relations, and it explained a repository rule by what the
discovery platform does with links.

## Change

- **Validator:** NO_LINKS is removed at every tier, together with its registry and scope entries.
- **Links page, section 2.4:** defines all eight relations by what the source must state. It says that no
  link is required, and that a shared paper, material, method or instrument never establishes a relation.
  `same_sample_as` is reserved for one specimen that two records label differently.
- **Links page, platform text:** the section on how the discovery platform reads links moves to the
  platform's manifest, which already states it. A neutral paragraph stays.
- **Other pages:** Controlled-Vocabulary and Record-Overview no longer mention NO_LINKS.

## Declared flips (gate 0)

- No stored record changes outcome or errors.
- NO_LINKS disappears from every record that carried it; no other warning changes.

## Battery (gate 1)

Run on the snapshot of 2026-10-06 (3,004 records), with the main validator against this branch:
- 0 outcome flips and 0 error changes;
- NO_LINKS removed from 216 records;
- nothing added.

Tests: 904 passed, 14 xfailed. The six wiki checks and the manifest check pass.

## Reviews (gate 2)

The first round (Codex, Grok, Gemini, blind) reviewed a draft that kept NO_LINKS as an info note with new
wording.
- **Codex:** keep it as info.
- **Gemini and Grok:** remove it. A note on every unlinked record "is what an agent treats as work", and
  having no links is the usual correct state. Taken: the rule moves to the Links page.
- **Gemini:** define all eight relations or none. Taken.
- **Grok:** proposed the core of the `same_sample_as` and `replica_of` definitions. Taken.

The second round reviewed the drafted Links text (Codex, Grok, Gemini, blind). Every change below was taken:
- **Direction:** every relation now states its direction; `same_sample_as` is symmetric (all three).
- **`replica_of`:** limited to technical replicates on the same specimen. A biological replicate is a new
  specimen (Gemini, Grok).
- **`same_sample_as`:** requires identity that the source establishes. An identifier the source gives goes
  in `sample_id`, and none is ever invented (Codex, Grok).
- **Tighter definitions** for `follows`, `derived_from`, `intended_comparison_target`, `calibration_of` and
  `validates`/`invalidates` (Grok's wording, with Codex's direction for `calibration_of`).
- **What is not a `sample_id`:** a material name, cell line, strain or batch code (Grok).
- **`basis`:** points to its allowed values (Grok).
- **Platform sentence deleted:** the last sentence about how the discovery platform reads links described a
  downstream application, so it was removed (all three).

## Agent benchmark (gate 3)

Not applicable: the current kit does not exercise links. Recorded as a gap in the kit pool.

## Ship and watch

The steward tracks `same_sample_as` links whose basis the records do not support. A warning for them is
proposed as a separate change.
