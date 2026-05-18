# MAX_CANDIDATES cap-sensitivity sweep (cap=100 vs cap=500, bare mode)

**Date:** 2026-05-18
**Scope:** the 9 benchmark codelists whose gold size > 100 codes (where the `MAX_CANDIDATES=100` ceiling is mathematically reachable).
**Comparison:** K=5 paired means at `MAX_CANDIDATES=100` (reused from `_postT37j_bare/`) vs `MAX_CANDIDATES=500` (new `_cap_sensitivity/cap_500_bare/`). Bare mode (no override) throughout, matching the bare-mode subset of the T37j K=5 sweep.
**Cap reduction:** the OMOPHub monthly quota was critically low at run time, so the planned 4-cap × bare+override matrix was trimmed to the headline binary (cap=100 vs cap=500) on the 9 large-gold codelists in bare mode. cap=300, cap=1000, and the override subsweep are deferred to a future run; see *Coverage gaps* below.

## Verdict

- Mean ΔF1 (cap=500 − cap=100) across 9 large-gold codelists: **+0.2023**
- Median ΔF1: +0.1874
- BCa 95 % CI (1 000 resamples, seed 7): [+0.0723, +0.3432]
- σ budget (per T37j convention): 0.012
- **Verdict:** F1 LIFT at cap=500

## Per-codelist

| codelist | gold | F1 cap=100 (±std) | F1 cap=500 (±std) | ΔF1 | mean pre-cap pool | mean gold pre-cap | mean gold lost | mean gold final | R cap=100 | R cap=500 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| epilepsy | 476 | 0.170 (±0.000) | 0.229 (±0.004) | **+0.058** | 3645 | 200.0 | 131.0 | 64.0 | 0.097 | 0.135 |
| lung_cancer | 363 | 0.242 (±0.007) | 0.782 (±0.009) | **+0.540** | 421 | 363.0 | 0.0 | 257.6 | 0.142 | 0.710 |
| dementia | 325 | 0.211 (±0.006) | 0.702 (±0.008) | **+0.491** | 301 | 229.0 | 0.0 | 206.8 | 0.126 | 0.636 |
| stroke | 266 | 0.522 (±0.000) | 0.803 (±0.004) | **+0.281** | 377 | 266.0 | 0.0 | 244.4 | 0.353 | 0.919 |
| hiv | 243 | 0.065 (±0.001) | 0.060 (±0.001) | **-0.005** | 100 | 9.0 | 0.0 | 9.0 | 0.037 | 0.037 |
| psychosis_schiz_bipolar | 198 | 0.308 (±0.000) | 0.495 (±0.025) | **+0.187** | 2721 | 198.0 | 2.0 | 101.0 | 0.182 | 0.510 |
| asthma_pincer | 124 | 0.662 (±0.003) | 0.779 (±0.002) | **+0.117** | 1544 | 114.0 | 2.0 | 109.8 | 0.586 | 0.885 |
| hypertension | 117 | 0.307 (±0.009) | 0.581 (±0.048) | **+0.274** | 211 | 117.0 | 0.0 | 59.4 | 0.181 | 0.508 |
| depression | 106 | 0.649 (±0.009) | 0.526 (±0.003) | **-0.123** | 493 | 101.0 | 0.0 | 85.6 | 0.545 | 0.808 |

## Headline: does the T37j +0.106 ΔF1 (BCa CI [+0.049, +0.177]) survive at cap=500?

The T37j +0.106 ΔF1 was computed against the pre-T37i baseline on all 15 codelists in mixed mode (bare for 8, override for 7); see `T37j_path_a_summary.md`. This sweep is a different comparison axis (cap=100 vs cap=500 *within* bare mode on the 9 large-gold codelists), so the two ΔF1 numbers are not on the same axis and cannot be directly subtracted.

What this sweep does say is that **`MAX_CANDIDATES=100` is a structural bottleneck on bare-mode F1 for these codelists**: lifting the cap to 500 produces a mean ΔF1 of **+0.202** (BCa CI [+0.072, +0.343]). For context, T37j's lift was +0.106 on a different axis.

Top lifts: `lung_cancer` +0.540 (0.242 → 0.782), `dementia` +0.491 (0.211 → 0.702), `stroke` +0.281 (0.522 → 0.803), `hypertension` +0.274 (0.307 → 0.581).

Regressions at cap=500: `hiv` -0.005 (0.065 → 0.060), `depression` -0.123 (0.649 → 0.526). These are codelists where the cap was not the binding constraint at cap=100 (the larger pre-cap pool either surfaces non-gold candidates that the LLM scores `include`, hurting precision, or the upstream retrievers miss most of the gold regardless of cap).

## Cap diagnostic interpretation

- `mean pre-cap pool` is the merger's deduplicated candidate count before the cap fires. At cap=500 the cap fires only when this exceeds 500; otherwise the post-cap count equals pre-cap.
- `mean gold pre-cap` is the K=5 mean of gold-set codes present in the pre-cap pool. The merger's joint retriever coverage on the query sets an absolute ceiling on this column independent of cap.
- `mean gold lost` is the K=5 mean of gold-set codes that were in the pre-cap pool but did not survive both caps (merger + UMLS). At cap=500 this is the *residual* loss after lifting the cap to 500; values close to zero indicate cap=500 is no longer the binding constraint.
- `mean gold final` is the K=5 mean of gold-set codes in the final LLM-included output. The gap between `mean gold pre-cap` and `mean gold final` decomposes into (a) cap-induced loss (`mean gold lost`), and (b) LLM-induced loss (gold codes scored `exclude`/`uncertain` by the scorer). The latter is what hierarchy expansion partially recovers post-LLM.

## Coverage gaps

- **cap=300 and cap=1000** were dropped from the sweep matrix due to the OMOPHub quota constraint. The two-point comparison (cap=100 vs cap=500) is sufficient to detect whether the cap is the binding constraint but does not characterise the recall curve between the two anchors.
- **Override mode** (T37j `request_include_descendants=true`) was not re-run at cap=500. The hierarchy expander operates post-LLM and adds OMOP 'Is a' descendants of LLM-included codes; its lift on descendant-closed gold lists is largely independent of where the merger cap sits, provided the cap doesn't drop the *parent* codes the expander walks from.
- **Small-gold codelists** (gold ≤ 100: copd, diabetes_mellitus, heart_failure, hepatitis_c_chronic, atrial_fib_icd10, mi_icd10) were not re-run because their gold size sits below the structural cap. heart_failure's validation run at cap=100 still surfaced 5 gold codes lost to the merger cap (see *Cap diagnostic interpretation* above), so the cap is not strictly non-binding on small-gold lists either, but the F1 ceiling is not cap-bound.

## Files

- Per-run envelopes: `_cap_sensitivity/cap_500_bare/{short}.result_runK_{1..5}.json`
- Aggregate JSON: `_cap_sensitivity/compare_cap_sensitivity.json`
- Sweep log: `_cap_sensitivity/sweep.log`
- Orchestrator: `backend/app/evaluation/run_cap_sensitivity.py`
- Aggregator: `backend/bench/compare_cap_sensitivity.py`
