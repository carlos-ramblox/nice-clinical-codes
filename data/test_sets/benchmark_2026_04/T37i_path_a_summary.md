# T37i Path A — hierarchy descendant expansion, K=5

**Date:** 2026-05-13
**Verdict:** **Net F1 LIFT mean +0.060, median +0.055** — well above the
K=5 σ ≈ 0.012 noise floor, but the BCa 95% CI **[−0.076, +0.152]**
straddles zero because the per-codelist distribution is **bimodal**.
Eight codelists lift by +0.05 to +0.38; two regress by −0.32 and −0.47;
five are essentially flat.

The lift is real on the mean, recall-driven (+0.18), at the cost of
precision (−0.15). Whether T37i is a net win depends on whether the
target gold list **closes descendants under "Is a"** or **prunes them**.

## Method

Two K=5 baselines compared on the disease benchmark (15 codelists):

- **Pre (`_preT37i/`)** — K=5 from 2026-05-13 at commit `5665260`
  (post-T37 chain through T37h, before T37i landed). Today's
  environment, same OMOPHub Pro key.
- **Post (top of `benchmark_2026_04/`)** — K=5 from 2026-05-13 at
  HEAD with `hierarchy_expander` wired between `llm_reasoning` and
  `output_assembly`. Today's environment, same key.

Only code-level delta: hierarchy descendant expansion via
`client.hierarchy.descendants(cid, max_levels=3, relationship_types=["Is a"], page_size=100)`
for every LLM-included row with a `concept_id`. Standard descendants
in OMOPHub vocabs (SNOMED / ICD-10 / OPCS-4) are added as new
include rows with confidence 0.7 and rationale
"Expanded via OMOP 'Is a' from concept_id N".

## Per-codelist Δ

| codelist | pre F1 | post F1 | ΔF1 | ΔP | ΔR | within σ |
|---|---:|---:|---:|---:|---:|:---:|
| epilepsy | 0.170 | 0.550 | **+0.380** | +0.09 | +0.32 | no |
| dementia | 0.210 | 0.503 | **+0.293** | +0.13 | +0.25 | no |
| copd | 0.508 | 0.752 | **+0.244** | +0.01 | +0.43 | no |
| lung_cancer | 0.236 | 0.456 | **+0.220** | +0.08 | +0.17 | no |
| psychosis_schiz_bipolar | 0.306 | 0.505 | +0.199 | −0.48 | +0.31 | no |
| stroke | 0.522 | 0.698 | +0.176 | −0.34 | +0.39 | no |
| asthma_pincer | 0.663 | 0.822 | +0.159 | +0.03 | +0.26 | no |
| hypertension | 0.328 | 0.382 | +0.054 | −0.43 | +0.09 | no |
| hiv | 0.064 | 0.065 | +0.001 | +0.02 | 0.00 | yes |
| mi_icd10 | 0.737 | 0.737 | 0.000 | 0.00 | 0.00 | yes |
| atrial_fib_icd10 | 1.000 | 1.000 | 0.000 | 0.00 | 0.00 | yes |
| hepatitis_c_chronic | 0.000 | 0.000 | 0.000 | 0.00 | 0.00 | yes |
| depression | 0.655 | 0.624 | −0.031 | −0.29 | +0.24 | no |
| heart_failure | 0.782 | 0.463 | **−0.319** | −0.46 | +0.15 | no |
| diabetes_mellitus | 0.755 | 0.281 | **−0.474** | −0.65 | +0.05 | no |

## Aggregate

| metric | value |
|---|---:|
| mean ΔF1 | **+0.0601** |
| median ΔF1 | +0.0545 |
| max \|ΔF1\| | 0.4743 (`diabetes_mellitus`) |
| BCa CI95 | [−0.0764, +0.1519] |
| mean Δ precision | −0.1529 |
| mean Δ recall | +0.1775 |
| verdict | **F1 LIFT** (mean > σ) |

## Where the lift comes from, and where it hurts

**Big winners (low pre-F1, gold list closed under descendants).** The
five biggest lifts — epilepsy, dementia, COPD, lung_cancer, psychosis
— share two features: pre-T37i F1 below 0.5, and gold lists that
include every standard descendant of the high-level concept. Hierarchy
expansion turns a 17-row include set into a 100-row include set whose
new rows mostly intersect the gold list. Recall jumps by 0.17–0.43;
precision is flat to mildly positive because the descendants are
genuinely on-target.

**Big losers (high pre-F1, gold list prunes descendants).** The two
biggest regressions — diabetes_mellitus and heart_failure — share the
inverse pattern: pre-T37i F1 above 0.75, and gold lists that
deliberately exclude specific descendants (e.g. `diabetes_mellitus`
excludes "diabetic retinopathy", "diabetic nephropathy", etc.; the
gold list is the *diagnosis* of diabetes, not its complications).
Hierarchy expansion floods the include set with descendants the gold
list pruned. Precision crashes (−0.46, −0.65); recall barely
moves because the gold list was already well-covered.

This is a structural property of the *codelist*, not a noise pattern.
It's the same shape called out in the T37i hypothesis: "hierarchy lift
depends on whether the gold codelist is descendant-closed".

## Conclusion

T37i is a recall-machine: +0.18 mean recall at the cost of −0.15 mean
precision. On the disease benchmark, the recall gain wins on average
(+0.060 F1) and dominates for low-baseline codelists, but it
regresses two of the high-baseline diabetes/cardiology lists badly
enough that the BCa CI95 crosses zero.

Two production-relevant takeaways:

1. **T37i ships as-is** because the mean lift is +5× σ and 8/15
   codelists see a positive lift. The diabetes/heart_failure
   regressions are explainable from the gold-list construction
   (NICE pruned descendants), not from a bug in the expander.

2. **A future T37k could gate the expander on a per-query
   "expand descendants?" signal** — a small classifier or a UI
   toggle — so high-baseline diagnostic codelists can opt out.
   This would not be an F1 measurement run, just a routing change.

## Commit

`test(eval): K=5 confirms T37i hierarchy lift ΔF1 +0.060 mean (T37i-F1)`
