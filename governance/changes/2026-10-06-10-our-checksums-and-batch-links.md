# 2026-10-06-10 Our checksums and our shared-batch links

- **Product:** repository. **Kind:** data (our own records). **Owner:** D. Sokaras (PI).
- **Found by:** the measurements behind change 09, which flagged 74 of our records for their checksums and 42
  for `same_sample_as` on a shared basis.

## Part A: checksums (done)

74 of our records (71 early uploads without an owner, 3 recent) carried zeros, "pending" or
"generated_at_extraction_time" in 120 asset checksums: LiSA electrode data files on LBNL storage, glob-pattern
outputs of a literature pipeline, wiki pages, DOIs, and calculation files on the SLAC cluster. We hold none of
these files here.

- **Change:** a cited paper (an asset with a citation or a DOI URI) takes `not_available_literature_source`
  (13 assets); every other asset takes `not_available` (107). One versioned edit per record, made with
  `tools/data_change/runner.py` in the working repository (fetch, back up, transform, validate locally, PUT).
- **Declared:** CHECKSUM_NOT_SHA256 lost on 74 records; no outcome changes; no other code changes.
- **Battery** (2026-10-06T203426Z to 2026-10-06T203510Z): 74 edited, 0 outcome changes; CHECKSUM_NOT_SHA256
  lost on 74, gained on 0.
- **Gate 2:** not run separately. The rule is change 09's reviewed warning, and the edit is mechanical.
- **Later:** the calculation files still exist on the SLAC cluster; their real SHA-256 can replace
  `not_available` when hashed there. Twenty assets point at glob patterns of a local pipeline
  (`output/opendataloader/*.md`), which no reader can resolve.

## Part B: 42 shared-batch links (done)

Our 42 `same_sample_as` links on basis `shared_material_batch` came from three papers; most notes said "same
catalyst/sample family". A source-reading agent decided each against its paper, with the rule of change 05: keep
with basis `unspecified` and the establishing passage when the paper says both records measured one electrode;
remove otherwise.

- **Decisions:** all 42 removed. Lum and Ager 2018, ESI p. S6: "Catalysis measurements were typically carried out
  3 times with fresh electrolyte and electrodes". Luo et al. 2023 and Xu et al. 2026 never say whether an
  electrode was reused between the two records' tests.
- **Declared:** SAME_SAMPLE_ON_A_SHARED_BASIS lost on 42; no outcome changes.
- **Battery** (to 2026-10-06T205208Z): 42 edited, 0 outcome changes; SAME_SAMPLE_ON_A_SHARED_BASIS lost on 42.

## Part C: 118 comparison links (done)

All 118 of our `intended_comparison_target` links (50 of them declared both ways) were decided against their
sources with the rule of change 11: keep, once, on the record that uses a control, reference or baseline the
source explicitly assigns; retype where another relation is stated; remove otherwise.

| Source | Kept | Retyped | Removed |
|---|---|---|---|
| Luo et al., Nat. Catal. 2023 | 9 | 1 | 31 |
| Lum and Ager, EES 2018 | 9 | 0 | 1 |
| Zhang et al., ChemElectroChem 2025 | 1 | 0 | 1 |
| ACS Electrochem. 2025 (three cell designs) | 0 | 0 | 14 |
| JACS 2024 | 0 | 0 | 20 |
| Angew. Chem. 2024 | 0 | 0 | 14 |
| Our DFT records (clean-slab reference) | 0 | 12 | 0 |
| Our XAS records (IrO2 standard) | 5 | 0 | 0 |

- **Kept**, with the passages: Luo, Fig. 2a, hybrids "compared to their single-component counterparts" (each
  hybrid links to its own two metals; a strict reading of "counterparts" would remove these 10 as well); Lum and
  Ager, "Cu foil data is shown as a reference"; ChemElectroChem, "A freshly electropolished Cu foil (Cu) serves
  as a reference"; the IrO2 reference standard.
- **Retyped:** the 12 adsorption-energy records now carry `derived_from` to the clean slab their energies are
  computed from; one Luo link moved to the hybrid record that uses the single metal as its baseline.
- **Removed:** side-by-side comparisons, and the 34 links of the JACS and Angewandte papers, whose main texts
  could not be read (the open supporting information shows the data only side by side). Earlier versions keep
  them, to restore against the papers if a stated reference turns up.
- **Result:** our comparison links 118 to 25, none declared both ways, each with its passage (one kept link had
  no notes and received its passage); `derived_from` 30 to 42. 67 records edited, 0 outcome changes, no code
  changes.
