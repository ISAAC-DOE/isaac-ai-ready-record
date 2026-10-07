# 2026-10-06-14 A quoted baseline is not this work's result

- **Product:** repository. **Kind:** record contract (version 2026-10-06.3), step 5. **Owner:** D. Sokaras (PI).
- **Found by:** gate 3 of change 12 (see that entry).

## Problem

An enzyme-kinetics paper reports mutants "relative to the wild type"; for two of three substrates its table quotes
the wild-type rows from the authors' earlier paper. Step 5 (change 11) says the record that uses an assigned
baseline carries `intended_comparison_target` to it. One agent created wild-type records from the quoted rows,
filed as this paper's results, so its mutant records had a baseline to link to: in both runs after change 11 and
in none of its three runs before. Another agent did the same under the older step 5, which also prescribed
comparison links. Step 2 already says a value quoted from other work is built from that work.

## Change

Step 5 gains: "A baseline this work measured is its own record, and the link to it stays. A baseline value this
work quotes from other work is never this work's result: build that record from the work that measured it (step 2)
and link to it; if you cannot build it, omit that link. Either way, the samples this work measured keep their
records."

## Gate 1

No check changes. Tests: 943 passed.

## Gate 2: review

Codex, Grok and Gemini, on a first sentence ("the baseline is a record of that other work: link to it if it
exists"). All three: it closes the case. Codex and Grok: "if it exists" weakens step 2, which says to build the
record from the measuring work. Grok and Gemini: it never says the mutant records stay, so a strict agent could
drop them, or mint a target. Grok: in a mixed case (one substrate's wild type measured in this paper) the
sentence could move the whole baseline. The shipped wording answers each point.

## Gate 3

Measured by the next runs of the enzyme kit: the quoted wild-type rows must not appear as this paper's results,
the mutant records must stay, and the wild-type record measured in this paper keeps its links.
