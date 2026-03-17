# ISAAC Data Collection for CO₂RR Performance

## What is this?

Two files that capture everything needed to create ISAAC AI-ready records from your electrochemical measurements at the Molecular Foundry.

## Files

### 1. `facility_setup.json` — Fill once
Everything about your lab setup that stays the same between experiments:
- Instruments (potentiostat model, GC model, NMR)
- Cell setup (already pre-filled from your slides — just confirm)
- Electrode fabrication details
- Where raw data files are stored

**Action needed:** Open the file, fill in the blanks, correct anything wrong.

### 2. `run_log.csv` — One row per experiment
Open in Excel or Google Sheets. Each row = one CA hold at one potential on one electrode.

**Pre-filled:** All 21 existing FE measurements (15 primary + 6 repeats). The FE data is already entered.

**Columns to fill in (yellow = missing):**
| Column | What to enter | Example |
|--------|--------------|---------|
| `run_id` | Your lab notebook ID or run number | MF-2024-042 |
| `date` | Date of measurement (YYYY-MM-DD) | 2024-03-15 |
| `experimenter` | Who ran it | Haoyi Li |
| `electrode_id` | Electrode batch/sample ID | CuAu-batch3-chip7 |
| `current_density_mA_cm2` | Average total j during CA hold | -3.2 |
| `FE_C2H5OH_pct` | Ethanol FE (%) — 0 if not detected | 0 |
| `FE_acetate_pct` | Acetate FE (%) — 0 if not detected | 0 |
| `FE_other_pct` | Any other products | 0 |
| `FE_total_pct` | Sum of all FEs (sanity check) | 100.6 |

**For new experiments:** Just add new rows at the bottom with the same columns.

## What happens next?

Once filled, run the converter:
```bash
python convert_to_isaac.py
```

This reads both files and generates one ISAAC record per row in `run_log.csv`, ready for upload to the ISAAC portal.

## Questions?

Contact Dimitrios Sokaras (dsokaras@stanford.edu)
