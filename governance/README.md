# How the ISAAC repository changes

Adopted by the PI on 2026-10-06. This governs the **repository of knowledge**: its schema, vocabulary,
validator, contract, wiki and stored records. The discovery platform is a separate product with its own
database and its own rules; it appears here only where it reads records.

Every change is recorded in [`changes/`](changes/README.md), one file per change, before it ships.

## Two products

- The repository is general-purpose. Its rules and documentation must stand on their own for any
  science, and state every rule by what it means for the record.
- How the discovery platform scores, groups or cites records belongs to the platform's own documentation.
- Science-specific rules belong in domain profiles on top of a generic core; catalysis is the first profile.

## Gates

| Gate | What it requires | Blocks shipping? |
|---|---|---|
| 0. Proposal | The problem with counts and examples; the product it touches; the owner of the rule; the **flip list**: which stored records, look-alike cases and adversarial cases must change outcome or messages, and how. | yes, if missing |
| 1. Battery | Run [`tools/steward/battery.py`](../tools/steward/battery.py) on a full snapshot before and after. The changes must equal the flip list; every must-pass look-alike passes; every must-catch adversarial case is caught; example records stay clean; graph invariants hold; every new rule sentence maps to a check. | yes |
| 2. Review | Three external models, blind, on the design and every public sentence; Edison (PaperQA3) when a scientific convention is at stake. Each entry records what each reviewer said and what changed. | yes, until answered |
| 3. Agents | For documentation changes: fresh agents from several vendors write records from test kits with the old and the new documentation. The measure is how often their records disagree with the source on conditions, kind and method. The change ships only if the worst vendor does not get worse. | yes, for documentation |
| 4. Ship | New checks start as warnings. Verify live against the flip list. | |
| 5. Watch | The steward tracks each new check on new uploads, by uploader and science. | |
| 6. Promote | A warning becomes a hold or an error only with at least 98% precision on adjudicated live cases, evidence that uploaders can fix what it flags, and the PI's sign-off. | |

Gates are proportional so the process does not freeze work: a wording fix needs gates 0 to 2; a new
warning needs 0 to 5; a schema change also needs a migration plan, a version and a rollback; a change to
stored records (a data change) needs 0, 1, 2 and 4, with the previous versions kept.

## Benchmarks

1. **Corpus battery**, daily and before and after every change: every record's outcome and codes by
   uploader, naming, identity, provenance, conditions, links and sample-group integrity, with a diff.
2. **Agent benchmark**, for documentation changes: test kits from several sciences and formats (papers,
   lab tables, simulation output, and a review as a trap), with answer keys written by someone who does not
   edit the documentation, a hidden tier never used to edit it, three vendor families and two runs each.
3. **Source audit**, monthly: about 40 new records by uploader and science, checked against their sources.
   This is the outcome measure: on 2026-10-02 conditions were wrong in 38% of checked records, record kind
   in 28%, method in 27%.

## Schema evolution

- Promote an open-namespace key to a structured field when at least 3 independent uploaders use it in at
  least 30 records over at least 4 weeks, at least 95% of its values parse to one type, a check can be
  written for it, and the repository itself needs it. A field only the platform wants stays in the
  platform; a field only one science wants goes to that science's profile.
- Deprecate a field when fewer than 1% of new records use it over 90 days or another field supersedes it:
  warn for one release, stop new writes, keep the stored values, publish the mapping.
- About three promotions to the core per quarter; the open namespace absorbs the rest.
