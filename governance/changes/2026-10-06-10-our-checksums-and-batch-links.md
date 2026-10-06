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

## Part B: 42 shared-batch links

Our 42 `same_sample_as` links on basis `shared_material_batch` come from three papers (Lum and Ager 2018, Luo
et al. 2023, Xu et al. 2026); most notes say "same catalyst/sample family". Each is decided against its source:
kept with basis `unspecified` and the establishing passage when the paper says both records measured one
electrode, removed otherwise. Pending.

## Part C: 50 reciprocal comparison links

Our 50 `intended_comparison_target` links declared in both directions follow the old contract step 5 (change
11). Each is decided against its source: kept, on one record, where the source assigns the reference; removed
otherwise. Pending.
