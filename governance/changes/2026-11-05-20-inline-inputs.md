# 2026-11-05-20 A calculation carries what it takes to rerun it: inline inputs

- **Product:** repository (schema, validator, wiki, contract). **Kind:** schema (additive field), three warnings,
  contract 2026-11-05.2. **Owner:** D. Sokaras (PI). **Decision:** PI, 2026-11-05: inline input in the record.

## Problem

The PI asked whether someone holding only an ISAAC record of an OCEAN calculation could rerun it. They could not:
the structure, the full OCEAN input, the starting magnetic moments and the parent VASP inputs were files on NERSC
paths only their producer can open; the checksums proved which files were used, and no one else could read them.
Across the repository, 221 of 261 input-structure and input-file assets on calculation records pointed to private
paths (file://, cluster paths, private schemes), one to a web address. The schema had no place for a file's text,
and the Assets page forbade it ("file contents must not be stored in the Asset block").

## Change

- `assets[].content`: the file's text inline (up to 100 kB of UTF-8 text); `sha256` is then the hash of that text;
  `uri` keeps the original location. Binary files and bulk outputs stay at their `uri`.
- Warnings: `INPUT_NOT_REACHABLE` (a record with `source_type: computation` whose `input_structure` or
  `workflow_recipe` asset is at a non-public location with no inline copy, or that names no input files at all);
  `INLINE_CONTENT_CHECKSUM_MISMATCH` (content without the SHA-256 of its text); `INLINE_CONTENT_TOO_LARGE`. Public:
  https or a DOI. Database records point to their entry and literature records cite their paper; neither is checked.
- Documentation: Assets (the field, the rule, an example; the prohibition now covers binary and bulk data only;
  the simulation note), Computation (a calculation you ran can be rerun from its record), contract step 10,
  generated pages. Examples: inline structure and input text on the three computed examples, hashed.

## Declared flips (gate 0) and battery (gate 1, 2026-11-05, 4,796 records)

- Expected: `INPUT_NOT_REACHABLE` on calculation records whose inputs are private paths or absent: the
  co-designer's OCEAN and VASP records (NERSC), our own records (NERSC paths, or no inputs), and records already
  rejected for other reasons.
- Measured (final scope): 0 outcome changes; `INPUT_NOT_REACHABLE` gained on 104 records (dsokaras 28, unowned
  ours 19, pbasera 39, oparedes 17, changzhiai 1); no code lost. Equals the flip list. (The first scope, which
  included database records and only private paths, flagged 113: mahajan's 38 CatHub-derived records instead of
  our 28 without inputs.)
- Tests: `tests/test_inline_inputs.py` (9): the example's inline text and hash; a private input (file or a
  private scheme) caught; a public copy and a literature calculation pass; a database record is not asked; a
  calculation with no inputs is caught; an output at a private path is not an input; a mismatched or missing hash
  and an oversized inline file caught. Suite 1012 passed; six wiki checks pass.

## Review (gate 2)

codex, grok and gemini, blind. All three: "structure and input files" is not the whole rerun; the examples'
"illustrative excerpts" taught truncation; content without a hash slipped through; licensed pseudopotentials and
credentials must never be inlined. grok and gemini: database records were not run by their uploader; repo:// was
listed as a resolvable scheme. grok: a calculation with no input assets escaped the check.

Adopted: contract step 10 and the Assets page now list the rerun in full (every small text file the code read,
copied unchanged and never retyped, completed or cut; the code and version and each pseudopotential's library,
version and label in `computation.method`; a parent calculation linked with `derived_from` and carrying its own
inputs; the post-processing named in `measurement.processing`; licensed files named, never copied; no
credentials); content requires its hash; database records are out of scope; a calculation with no inputs is
flagged; the `uri` text says only https and DOIs are public and ISAAC resolves no other scheme; the examples carry
complete files (an 18-atom Cu(111)+CO slab and its full pw.x input; the POSCAR typed as text).

Not adopted: hard rejection, a dependency manifest and fetching URIs at validation (codex): new checks start as
warnings, and the validator does not reach the network; a repository resolver for repo:// (gemini): none exists.
Inline copies are byte-exact, so paths and account names in an input are published with it; the Assets page says
so instead of asking uploaders to edit files (which would break the hash).

## Ship and watch

Shipped in #298 (portal v0.0.381, contract 2026-11-05.2), wiki first. Live: the computed examples publish with no
input warning; a stored OCEAN record of the co-designer draws `INPUT_NOT_REACHABLE` (its energy axis is already
filled, from the skill's PR 6). Then: the co-designer's skill inlines structures and input files and updates its records; our 19
records and the CatHub-derived ones need their inputs inline or a public link (owners told through the warning).
