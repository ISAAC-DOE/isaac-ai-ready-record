# Computation Block — DFT Method Specification

## Overview

The `computation` block declares the theory level and numerical methodology used to produce a simulation record. For DFT calculations, it captures five structured property groups:

```
computation
├── method          # Theory level: functional, basis, cutoff, k-points, spin
├── slab_model      # Surface slab parameters (when sample_form = slab_model)
├── potential_method # Electrochemical potential treatment
├── output_quantity  # What quantity is computed and which corrections are applied
└── transition_state # Transition state search parameters (for barrier calculations)
```

All sub-blocks are optional. The `computation` block remains a permissive `"type": "object"` without `additionalProperties: false`, so existing records with additional fields continue to validate.

## 1. Method

Declares the core electronic structure settings.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `family` | enum | No | `DFT`, `DFT_U`, `hybrid_DFT`, `AIMD`, `classical_MD`, `CHE`, `microkinetic`, `machine_learning`, `semi_empirical` |
| `functional_class` | enum | No | `LDA`, `GGA`, `meta_GGA`, `hybrid`, `double_hybrid`, `RPA` |
| `functional_name` | string | No | Specific functional: `PBE`, `RPBE`, `BEEF-vdW`, `HSE06`, `SCAN`, etc. |
| `basis_type` | enum | No | `planewave`, `LCAO`, `real_space`, `mixed` |
| `pseudopotential` | string | No | `PAW`, `ultrasoft`, `norm_conserving`, `GBRV`, `SG15` |
| `cutoff_eV` | number | No | Planewave kinetic energy cutoff |
| `spin_treatment` | enum | No | `none`, `collinear`, `noncollinear`, `SOC` |
| `dispersion` | enum | No | `none`, `D2`, `D3`, `D3BJ`, `TS`, `MBD`, `dDsC`, `vdW_DF`, `vdW_DF2`, `rVV10` |
| `relativistic` | enum | No | `none`, `scalar`, `full` |
| `kpoints` | string | No | Brillouin zone sampling, e.g., `"3x3x1 Monkhorst-Pack"`, `"Gamma-only"` |
| `smearing` | object | No | `method` (enum) + `width_eV` (number) |
| `convergence` | object | No | `energy_eV`, `force_eV_per_A`, `stress_kbar` |

### Boundary with Context

**Litmus test**: *If I changed this parameter, would the physical system change?*

| Parameter | Block | Rationale |
|-----------|-------|-----------|
| Solvation model (implicit/explicit) | `context.simulation_assumptions` | Changes the physical environment |
| Temperature | `context.temperature_K` | Changes the physical state |
| Applied potential (-1.6V SHE) | `context.electrochemistry` | Changes the physical operating condition |
| Functional (PBE vs HSE06) | `computation.method` | Numerical choice, same physical system |
| Cutoff (400 vs 600 eV) | `computation.method` | Numerical convergence parameter |
| K-points (3x3x1 vs Gamma) | `computation.method` | Numerical convergence parameter |
| Spin (collinear vs none) | `computation.method` | Numerical treatment of same physics |

**Prohibition**: Never put cutoffs, k-point meshes, or smearing widths in `context.simulation_assumptions`.

## 2. Slab Model

Parameters specific to periodic surface slab calculations. Only relevant when `sample.sample_form = "slab_model"`.

| Property | Type | Description |
|----------|------|-------------|
| `surface_facet` | string | `"111"`, `"100"`, `"110"`, `"211"` |
| `supercell` | string | `"4x4"`, `"4x3"`, `"3x3"` |
| `layers` | integer | Number of atomic layers |
| `fixed_layers` | string | `"bottom 2"`, `"bottom 1"` |
| `vacuum_A` | number | Vacuum thickness in Angstroms |
| `lattice_constant_A` | number | In-plane lattice parameter |
| `dipole_correction` | boolean | Whether dipole correction is applied |

## 3. Potential Method

How the electrochemical potential is treated. This is the single most important field for valid comparison between electrochemical DFT studies.

| Property | Type | Description |
|----------|------|-------------|
| `type` | enum | **Required for electrochemical simulations.** See vocabulary below. |
| `target_potential_V_SHE` | number | Target electrode potential vs SHE |
| `SHE_work_function_eV` | number | Assumed SHE work function (typically 4.4–4.8 eV) |
| `solvent_model` | string | Implicit solvent implementation: `VASPsol`, `GLSSA13`, `CANDLE`, `ENVIRON` |
| `dielectric_constant` | number | Solvent dielectric constant |

### Potential Method Vocabulary

| Value | Description | Electron count |
|-------|-------------|---------------|
| `vacuum` | No solvent, no potential | Fixed |
| `CHE` | Computational Hydrogen Electrode (post-processing correction only) | Fixed |
| `implicit_solvent_PZC` | Implicit solvent at potential of zero charge | Fixed |
| `fixed_NELECT` | Fixed electron count approximation to target potential | Fixed (user-chosen) |
| `grand_canonical` | Duan method: NELECT optimized to minimize grand canonical energy at target potential | Variable (optimized) |
| `constant_potential` | Self-consistent constant-potential method (e.g., SJM, FHI-aims CP) | Variable (self-consistent) |
| `joint_DFT` | Joint density functional theory (e.g., JDFTx) | Variable |

**Agent reasoning**: Two records can only be directly compared if they use the same `potential_method.type`. A `fixed_NELECT` barrier is **not** equivalent to a `grand_canonical` free energy barrier — even on the same slab at the same nominal potential.

## 4. Output Quantity

Specifies what thermodynamic quantity the descriptors represent. **This field prevents apples-to-oranges comparisons.**

| Property | Type | Description |
|----------|------|-------------|
| `quantity` | enum | The thermodynamic quantity reported in descriptors |
| `corrections_applied` | object | Boolean flags for each correction |

### Quantity Vocabulary

| Value | Description | Typical corrections |
|-------|-------------|-------------------|
| `E_DFT` | Raw DFT total energy | None |
| `E_DFT_plus_ZPE` | DFT energy + zero-point energy | ZPE |
| `delta_E` | Energy difference (e.g., adsorption energy, barrier) | Solvation, dispersion |
| `delta_G_CHE` | Free energy with CHE potential correction | ZPE, entropy, CHE |
| `delta_G_grand_canonical` | Grand canonical free energy | ZPE, entropy, thermal, GC, solvation |
| `activation_energy_raw` | NEB/dimer barrier, raw DFT | Solvation, dispersion |
| `activation_energy_ZPE` | NEB barrier + ZPE correction | ZPE, solvation, dispersion |
| `activation_free_energy` | Full free energy barrier | All |

### Corrections Applied Flags

| Flag | Description |
|------|-------------|
| `zero_point_energy` | ZPE = sum(h*nu/2) from frequency analysis |
| `entropy` | Entropic correction T*delta_S |
| `thermal` | Heat capacity integration int(Cp dT) |
| `solvation` | Implicit or explicit solvent |
| `dispersion` | DFT-D3 or similar van der Waals correction |
| `grand_canonical` | Grand canonical correction E_GC = E_DFT + delta_n * mu_e |
| `PCET` | Proton-coupled electron transfer correction at applied potential |

**Critical rule**: When comparing a descriptor from Record A against Record B, both must have the same `output_quantity.quantity`. If they differ, the comparison is methodologically invalid and must not be used to draw scientific conclusions about barrier differences.

## 5. Transition State

Parameters for transition state search calculations.

| Property | Type | Description |
|----------|------|-------------|
| `method` | enum | `NEB`, `CI-NEB`, `dimer`, `string`, `IRC`, `growing_string` |
| `images` | integer | Number of NEB images |
| `reaction` | string | Reaction equation, e.g., `"CO* + CO* → OCCO*"` |
| `n_electrons_transferred` | integer | 0 for chemical steps, 1+ for PCET steps |

## Examples

- **Intent**: `examples/dft_neb_intent_record.json` — CI-NEB request for OCCO barrier
- **Evidence**: `examples/dft_neb_evidence_record.json` — Computed barrier with raw DFT energy
- **Simulation XAS**: `examples/simulation_xas_record.json` — Existing example using `computation.method`
