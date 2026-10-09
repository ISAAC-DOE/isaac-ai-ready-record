# 2026-10-06-07 The modulation check stops reading cited titles

- **Product:** repository. **Kind:** validator (an info-tier check narrowed) and the steward battery.
  **Owner:** D. Sokaras (PI).
- **Found by:** the agent benchmark baseline, 2026-10-06: the agents writing records from an enzyme-kinetics paper each
  reported that MODULATION_EVIDENT_BUT_UNDECLARED fired on every record because of the word "modulate" in the
  paper's title.

## Problem

MODULATION_EVIDENT_BUT_UNDECLARED (info) searches a record's assets, configuration and notes for words such as
"modulat" or "pulsed" when no `context.modulation` block is declared. It was written for records whose
modulation survived only in a data file name. It also read the `citation` object of each asset, including the
cited paper's title.

On the 2,994 stored records (live snapshot 2026-10-06T192153Z), 46 fired:

| Where it matched | Records | Uploader | Right? |
|---|---|---|---|
| Asset file name, e.g. `Pure_Cu_electrodes_record_50nm_modulation_1p3Hz_13Hz.xlsx` | 25 | haochen_slac | yes: modulated potential at 1.3 and 13 Hz |
| Cited paper title, e.g. "Electronic modulation of metal-support interactions ..." | 21 | changzhiai | no: no experiment was driven |

In the benchmark, all 106 enzyme-kinetics records written by three agents fired on the title "Phosphate ions
modulate enzyme activity ...". None of those experiments was modulated.

## Change

- The check leaves each asset's `citation` object out of the text it searches. Asset URIs, notes and figure
  text, the configuration and the notes are searched as before.
- Validation-Rules description (after review): "No context.modulation block is declared, and a word for
  periodic driving (modulat, pulsed, chopped, duty cycle, square wave) appears in an asset's URI, notes or
  figure text, in system.configuration, in sample.notes or in context.electrochemistry.notes. Titles of cited
  works are not searched. Declare context.modulation when this record's measurement was driven periodically:
  without it, every machine reading the record treats each setpoint as the condition of the whole run. If the
  word describes a preparation step (pulsed laser deposition), an instrument part (an optical chopper) or
  another work, keep the record as it is." The message a record receives says the same.
- The steward battery now counts info-tier codes and reports, for records present in both runs, each code
  gained or lost. Before this, an info-tier change was invisible to it (gate 1 first showed no difference here).

## Flip list (declared before the battery ran)

- Stored records: the 21 title-only records lose the info code; the 25 file-name records keep it; no other
  code and no outcome changes.
- Look-alikes that must still fire: a modulation file name (existing tests); a modulation file name next to a
  citation whose title also matches (new test).
- Cases that must stop firing: the two titles above as the only match (new test).
- Benchmark: the 106 enzyme-kinetics records lose it.

## Gate 1: battery

Replay of 2026-10-06T192153Z, main's validator then this change, both measured with the new battery:
2,994 records, 0 outcome changes, `codes_changed` = MODULATION_EVIDENT_BUT_UNDECLARED lost on 21, gained on 0.
The 21 are all changzhiai and all title-only; the 25 still firing are all haochen_slac and all carry a
modulation file name. Benchmark: 0 of 177 records fire after the change (106 before). Tests: 910 passed.

## Gate 2: review

Codex, Grok and Gemini, blind, on the design, the measurements and the public sentence.

- **All three:** leave cited titles unread. A narrower title vocabulary ("modulation excitation", "pulsed
  electrolysis") still fires when a static record cites a paper about that technique, and misses chopped
  light, square-wave voltammetry and duty cycles. The accepted miss: a periodic drive stated only in a cited
  title, with opaque file names. None of the 21 stored title firings or the 106 benchmark firings was one.
- **Remaining false cases** (pulsed laser deposition in sample notes, an optical chopper or a photoelastic
  modulator in the configuration, "allosteric modulation" in notes): none fires on the stored records. All
  three advise leaving the regex alone until a stored record shows one.
- **Codex and Gemini:** the description must tell an uploader what to do when the word does not describe
  this measurement. Gemini called "titles are not read" implementation detail; Codex and Grok kept it. Kept,
  because it tells an uploader which text counts.
- **Grok:** assets have no `description` field (the searched asset text is the URI, notes and figure text),
  and "mention a modulated experiment" states a conclusion the regex never reached.
- **Codex** could not open the diff: the review runner copied only the prompt into each reviewer's folder.
  The runner now copies every file of the packet.

What changed: the description and the message name the fields searched, call the words a cue, and say when
to declare the block and when to keep the record as it is.

## Ship and watch

- Shipped in #268 (portal v0.0.350, 2026-10-06), with the regenerated Validation-Rules and Constraint-Matrix
  pages. The first main CI run failed: the Constraint-Matrix page had not been regenerated. `tools/ship.sh`
  now checks the published wiki before merging and waits for the CI run of the merge commit itself.
- Live check: the validator stays silent on a record whose only match is a cited title and fires on a
  modulation file name. A live battery (2026-10-06T195904Z, 2,994 records) finds the advisory on 25 records,
  all haochen_slac, all with a modulation file name.
- Watch: the daily battery counts the advisory by uploader; a new firing on another uploader's records is
  read before any further change to the regex.
