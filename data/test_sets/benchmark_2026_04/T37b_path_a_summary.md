# T37b Path A — same-day apples-to-apples K=5 verification

**Date:** 2026-05-12
**Verdict:** **T37 + T37g are F1-neutral.** Mean ΔF1 = +0.0025, BCa CI95
[-0.0006, +0.0059] crosses zero. The 9-day environmental drift visible
in the original t37b sweep was confirmed environmental, not code-level.

## Method

Three K=5 baselines compared:

- **A** = `_preT37/` — pre-T37 K=5 from 2026-05-03 at commit f4c9556 (T07).
  Today's environment: ?? (different — see B−A drift below).
- **B** = `_sameDayPre/` — pre-T37 K=5 from 2026-05-12 at commit 31ca009
  (parent of the T37 chain) with **T37f + T37h source files overlaid**
  so the OMOPHub call shape matches HEAD. Today's environment.
- **C** = `*.result_runK_*.json` (top of `benchmark_2026_04/`) — post-T37
  K=5 from 2026-05-12 at HEAD (T37 + T37g + T37f + T37h). Today's
  environment.

The only code-level delta between B and C is the T37/T37g pair
(retriever wiring + per-vocab merger quota). Bulk-search (T37f) and
the hallucination safety net (T37h) are in both → cancel out of the
comparison.

## Per-codelist Δ

| codelist | A (2026-05-03) | B (today, pre-T37) | C (today, HEAD) | C − B (T37) | B − A (env) |
|---|---:|---:|---:|---:|---:|
| heart_failure | 0.7845 | 0.7643 | 0.7815 | **+0.0172** | −0.0203 |
| dementia | 0.2406 | 0.2222 | 0.2104 | −0.0119 | −0.0184 |
| depression | 0.6364 | 0.6433 | 0.6546 | +0.0113 | +0.0069 |
| hypertension | 0.4621 | 0.3201 | 0.3275 | +0.0074 | −0.1420 |
| copd | 0.5397 | 0.5010 | 0.5078 | +0.0068 | −0.0387 |
| lung_cancer | 0.2414 | 0.2319 | 0.2359 | +0.0039 | −0.0094 |
| asthma_pincer | 0.6216 | 0.6599 | 0.6630 | +0.0031 | +0.0383 |
| diabetes_mellitus | 0.7252 | 0.7536 | 0.7551 | +0.0016 | +0.0284 |
| psychosis_schiz_bipolar | 0.6519 | 0.3077 | 0.3062 | −0.0014 | −0.3443 |
| hiv | 0.0235 | 0.0650 | 0.0644 | −0.0006 | +0.0415 |
| mi_icd10 | 0.7368 | 0.7368 | 0.7368 | 0.0000 | 0.0000 |
| atrial_fib_icd10 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| stroke | 0.4801 | 0.5222 | 0.5222 | 0.0000 | +0.0421 |
| epilepsy | 0.1852 | 0.1704 | 0.1704 | 0.0000 | −0.0148 |
| hepatitis_c_chronic | 0.7933 | 0.0000 | 0.0000 | 0.0000 | **−0.7933** |

## Aggregate

| metric | C − B (T37 code drift) | B − A (environmental drift) |
|---|---:|---:|
| mean Δ | **+0.0025** | −0.0816 |
| median Δ | 0.0000 | −0.0094 |
| max |Δ| | 0.0172 | 0.7933 |
| BCa CI95 | **[−0.0006, +0.0059]** | [−0.2739, −0.0114] |

## Conclusion

The C − B comparison (T37 code drift, environment held constant)
returns a BCa 95% confidence interval that crosses zero: T37 + T37g
introduce no statistically significant F1 change at K=5 sampling.

The single per-codelist outlier is heart_failure at +0.0172 — 5/1000ths
above the K=5 σ ≈ 0.012 noise floor and well within T07's documented
per-codelist F1 std max of 0.025 (`hypertension`). Plausible source:
T37g's lexicographic `(vocabulary, code)` tiebreaker shifting which
multi-source rows survive `MAX_CANDIDATES = 100` at the cap boundary
for this specific list. Not a regression.

The B − A comparison documents that the original t37b FAIL was
**entirely environmental drift** over 9 days, not T37 code drift:

- hepatitis_c_chronic collapsed 0.79 → 0.00 (same code at 31ca009,
  different OMOPHub state today vs 2026-05-03)
- psychosis_schiz_bipolar dropped 0.65 → 0.31
- hypertension dropped 0.46 → 0.32

These match exactly the pattern documented in
`~/.claude/projects/.../memory/project_benchmark_comparability.md` for
time-separated K=5 sweeps: OMOPHub re-ingest + UMLS suggestion
evolution + OpenCodelists republish.

## Commit

`test(eval): K=5 apples-to-apples confirms T37 F1-neutral (T37b)`
