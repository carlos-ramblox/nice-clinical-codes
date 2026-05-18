# MAX_CANDIDATES cap-sensitivity sweep

**Date:** 2026-05-18 (Wave 1 landed); update timestamp on each regen.
**Scope:** 15-codelist v2 disease benchmark; K=5 paired-comparison protocol.
**Caps:** 100 (production), 500 (sensitivity anchor), 1000 (methods-paper headline), ∞ (supplementary).
**Modes:** *bare* = LLM parser extracts include_descendants from query; *override* = request-level include_descendants=true (T37j convention). Mode-matched aggregates use bare for the 8 non-descendant-closed codelists, override for the 7 descendant-closed.

> **Note on pre-T37i cap=1000.** The methods paper's pre-T37i baseline at cap=1000 is taken to equal post-T37j cap=1000 bare-mode behaviour. T37j_path_a_summary.md established the empirical equivalence at cap=100 (delta-F1 within K=5 σ=0.012 on 7 of 8 bare codelists); the mechanism is cap-independent because the hierarchy expander gate (`include_descendants=False` extracted from bare-name queries) fires before the expander work and the cap is upstream of the expander entirely. No pre-T37i checkout sweep was run.

## Methods-paper headline

| Configuration | n | Mean F1 | Median F1 | Mean P | Mean R | F1 BCa 95 % CI |
|---|---:|---:|---:|---:|---:|---|
| Pre-fix baseline (cap=100, April K=1) | 15 | 0.49 | 0.53 | 0.71 | 0.51 | [0.36, 0.62] |
| Post-fix default (cap=100, April K=1) | 15 | 0.57 | 0.67 | 0.88 | 0.49 | [0.44, 0.68] |
| Post-T37j K=5 (cap=100, mode-matched) | 15 | **0.569** | 0.649 | 0.719 | 0.523 | [+0.414, +0.690] |
| Post-T37j K=5 (cap=1000, mode-matched) | 8/15 | 0.626 (partial) | 0.625 | 0.624 | 0.712 | missing: stroke, asthma_pincer, copd, psychosis_schiz_bipolar, dementia, epilepsy, lung_cancer |
| cap=∞ supplementary K=1 | 0/15 | nan (partial) | nan | nan | nan | missing: heart_failure, diabetes_mellitus, hypertension, mi_icd10, atrial_fib_icd10, stroke, asthma_pincer, copd, depression, psychosis_schiz_bipolar, dementia, epilepsy, lung_cancer, hepatitis_c_chronic, hiv |

*Cap=1000 mode-matched headline is provisional pending the override sweep (Wave 2). Missing codelists: ['stroke', 'asthma_pincer', 'copd', 'psychosis_schiz_bipolar', 'dementia', 'epilepsy', 'lung_cancer'].*

## T37j delta-F1 across caps

The T37j K=5 verification at cap=100 (`T37j_path_a_summary.md`) reported mean delta-F1 +0.106 (BCa CI [+0.049, +0.177]) vs the pre-T37i baseline, mode-matched. The relevant question for the methods paper is whether the same lift holds at cap=1000.

*Mode-matched delta-F1 at cap=1000 is provisional: 8 of 15 codelists complete. Pending the override sweep at cap=1000 (Wave 2) for: stroke, asthma_pincer, copd, psychosis_schiz_bipolar, dementia, epilepsy, lung_cancer.*

## Cap-lift delta-F1 (the structural-bottleneck axis)

This axis compares the SAME code state at different caps, isolating the cap as a variable.

| Comparison | n | Mean delta-F1 | Median | BCa 95 % CI | Verdict |
|---|---:|---:|---:|---|---|
| cap=100 → cap=500 bare (9 large-gold) | 9 | **+0.202** | +0.187 | [+0.075, +0.346] | **F1 LIFT** |
| cap=100 → cap=500 bare (all 15) | 15 | **+0.171** | +0.111 | [+0.054, +0.361] | **F1 LIFT** |
| cap=100 → cap=1000 bare (all 15) | 15 | **+0.177** | +0.096 | [+0.055, +0.362] | **F1 LIFT** |

## Per-codelist K=5 F1 by cap (mode-matched)

| codelist | mode | gold | F1 cap=100 | F1 cap=500 | F1 cap=1000 |
|---|---|---:|---:|---:|---:|
| epilepsy | override | 476 | 0.550 (±0.000) | — | — |
| lung_cancer | override | 363 | 0.402 (±0.054) | — | — |
| dementia | override | 325 | 0.491 (±0.003) | — | — |
| stroke | override | 266 | 0.698 (±0.000) | — | — |
| hiv | bare | 243 | 0.065 (±0.001) | 0.060 (±0.001) | 0.048 (±0.009) |
| psychosis_schiz_bipolar | override | 198 | 0.510 (±0.010) | — | — |
| asthma_pincer | override | 124 | 0.826 (±0.003) | — | — |
| hypertension | bare | 117 | 0.307 (±0.009) | 0.581 (±0.048) | 0.567 (±0.045) |
| depression | bare | 106 | 0.649 (±0.009) | 0.526 (±0.003) | 0.482 (±0.001) |
| diabetes_mellitus | bare | 86 | 0.754 (±0.015) | 0.608 (±0.013) | 0.613 (±0.010) |
| copd | override | 56 | 0.757 (±0.013) | — | — |
| heart_failure | bare | 42 | 0.785 (±0.032) | 0.639 (±0.018) | 0.636 (±0.018) |
| hepatitis_c_chronic | bare | 20 | 0.000 (±0.000) | 0.932 (±0.018) | 0.923 (±0.018) |
| mi_icd10 | bare | 12 | 0.737 (±0.000) | 0.737 (±0.000) | 0.737 (±0.000) |
| atrial_fib_icd10 | bare | 7 | 1.000 (±0.000) | 1.000 (±0.000) | 1.000 (±0.000) |

## Cap diagnostics at cap500

| codelist | gold | pre-cap pool | gold in pre-cap | gold lost | gold final |
|---|---:|---:|---:|---:|---:|
| hiv | 243 | 100 | 9.0 | 0.0 | 9.0 |
| hypertension | 117 | 211 | 117.0 | 0.0 | 59.4 |
| depression | 106 | 493 | 101.0 | 0.0 | 85.6 |
| diabetes_mellitus | 86 | 185 | 86.0 | 0.0 | 77.6 |
| heart_failure | 42 | 132 | 42.0 | 0.0 | 39.8 |
| hepatitis_c_chronic | 20 | 134 | 20.0 | 0.0 | 19.2 |
| mi_icd10 | 12 | 50 | 12.0 | 0.0 | 7.0 |
| atrial_fib_icd10 | 7 | 50 | 7.0 | 0.0 | 7.0 |

## Cap diagnostics at cap1000

| codelist | gold | pre-cap pool | gold in pre-cap | gold lost | gold final |
|---|---:|---:|---:|---:|---:|
| hiv | 243 | 100 | 9.0 | 0.0 | 7.2 |
| hypertension | 117 | 211 | 117.0 | 0.0 | 57.4 |
| depression | 106 | 493 | 101.0 | 0.0 | 83.4 |
| diabetes_mellitus | 86 | 185 | 86.0 | 0.0 | 77.4 |
| heart_failure | 42 | 132 | 42.0 | 0.0 | 39.8 |
| hepatitis_c_chronic | 20 | 134 | 20.0 | 0.0 | 19.2 |
| mi_icd10 | 12 | 50 | 12.0 | 0.0 | 7.0 |
| atrial_fib_icd10 | 7 | 50 | 7.0 | 0.0 | 7.0 |

## Cap diagnostic interpretation

- `pre-cap pool` is the merger's deduplicated candidate count before the cap fires. When this is below the cap value, the cap doesn't fire and post-cap = pre-cap. When above, the cap drops `pre_cap − cap` candidates from the LLM's view.
- `gold in pre-cap` is the K=5 mean of gold-set codes present in the pre-cap pool. The merger's joint retriever coverage on the query sets an absolute ceiling on this column independent of cap.
- `gold lost` is the K=5 mean of gold-set codes that were in the pre-cap pool but did not survive both caps (merger + UMLS). Values close to zero indicate the cap is no longer the binding constraint.
- `gold final` is the K=5 mean of gold-set codes in the final LLM-included output. The gap between `gold in pre-cap` and `gold final` decomposes into (a) cap-induced loss, and (b) LLM-induced loss (gold codes scored `exclude`/`uncertain`).

## Coverage gaps

- **cap=500 override sweep on the 7 descendant-closed codelists** is not yet run (Wave 2). Without it, the cap=500 line of the per-codelist table uses bare-mode for those codelists, which understates their F1 under the T37j convention.
- **cap=1000 override sweep on the 7 descendant-closed codelists** is not yet run (Wave 2). The cap=1000 mode-matched headline and the cap=1000 T37j delta-F1 row are provisional until this lands.
- **cap=∞ supplementary K=1** is not yet run (Wave 3, optional). The methods-paper discussion section would benefit from the absolute-ceiling reference but the two-anchor sensitivity curve (cap=100, cap=1000) suffices for the headline claim.
- **Pre-T37i cap=1000 checkout** was deliberately skipped per the project-memory equivalence argument; see the note at the top.

## Files

- Per-run envelopes: `_cap_sensitivity/cap_{500,1000}_{bare,override}/{short}.result_runK_{1..5}.json` (gitignored)
- Aggregate JSON: `_cap_sensitivity/compare_cap_sensitivity.json`
- Sweep log: `_cap_sensitivity/sweep.log` (Wave 1) — Wave 2 + 3 logs land alongside
- Diagnostics JSON: `_cap_sensitivity/diagnose_depression_hiv.json`
- Orchestrator: `backend/app/evaluation/run_cap_sensitivity.py`
- Aggregator: `backend/bench/compare_cap_sensitivity.py`
- Diagnostic script: `backend/bench/diagnose_depression_hiv.py`
