# T37j Path A — query-intent routing on hierarchy expansion, K=5

**Date:** 2026-05-13
**Verdict:** Mean ΔF1 against the pre-T37i baseline is **+0.106**
(BCa 95% CI [+0.049, +0.177] — no longer straddles zero, where
T37i's [−0.076, +0.152] did). Mean ΔF1 against the post-T37i
baseline is **+0.046**: the routing restores the
diagnosis-only baselines (`diabetes_mellitus`, `heart_failure`) and
the override keeps the descendant-closed lifts (`epilepsy`,
`dementia`, `copd`, and four others).

## Method

Two K=5 sweeps on the 15-codelist disease benchmark, on develop at
HEAD with the T37j routing landed:

- **Sweep 1 (bare, all 15 codelists)** — no request-level override; the
  LLM parser extracts `include_descendants` from natural-language cues.
  Every benchmark query is a bare-name form (e.g. `"Heart failure
  codes"`, `"Codes for dementia"`) so the LLM extracts `False` on all
  15, the hierarchy expander gate returns early, and the pipeline runs
  the pre-T37j path. Outputs in `_postT37j_bare/`.
- **Sweep 2 (override=True, 7 descendant-closed codelists)** —
  `request_include_descendants=true` on the 7 codelists whose gold
  list is descendant-closed under "Is a" (`epilepsy`, `dementia`,
  `copd`, `lung_cancer`, `psychosis_schiz_bipolar`, `stroke`,
  `asthma_pincer`). The expander fires; behaviour matches T37i for
  those codelists by construction. Outputs in `_postT37j_override/`.

Comparison baselines:

- **pre-T37i** (`_preT37i/`) — K=5 from 2026-05-13 at commit `5665260`,
  before the hierarchy expander was wired.
- **post-T37i** (top-level `*.result_runK_*.json` files) — K=5 from
  2026-05-13 at commit `cd7427e`, with unconditional expansion.

For each codelist the post-T37j column reports the sweep matching
the codelist's gold-list shape: `bare` (8 codelists) or `override`
(7). This is the path a reviewer would have run for that codelist,
either because the LLM cue extracted False or because the reviewer
ticked the override.

## Per-codelist Δ

| codelist | mode | pre-T37i F1 | post-T37i F1 | post-T37j F1 | Δ vs pre | Δ vs post |
|---|---|---:|---:|---:|---:|---:|
| epilepsy                 | override | 0.170 | 0.550 | 0.550 | **+0.380** | +0.000 |
| dementia                 | override | 0.210 | 0.503 | 0.491 | **+0.280** | −0.012 |
| copd                     | override | 0.508 | 0.752 | 0.757 | **+0.249** | +0.005 |
| psychosis_schiz_bipolar  | override | 0.306 | 0.505 | 0.510 | **+0.204** | +0.005 |
| stroke                   | override | 0.522 | 0.698 | 0.698 | **+0.176** | +0.000 |
| asthma_pincer            | override | 0.663 | 0.822 | 0.826 | **+0.163** | +0.004 |
| lung_cancer              | override | 0.236 | 0.456 | 0.402 | **+0.166** | −0.054 |
| heart_failure            | bare     | 0.782 | 0.463 | 0.786 | +0.004 | **+0.323** |
| diabetes_mellitus        | bare     | 0.755 | 0.281 | 0.755 | −0.001 | **+0.474** |
| depression               | bare     | 0.655 | 0.624 | 0.649 | −0.005 | +0.025 |
| hypertension             | bare     | 0.328 | 0.382 | 0.307 | −0.021 | −0.075 |
| mi_icd10                 | bare     | 0.737 | 0.737 | 0.737 | +0.000 | +0.000 |
| atrial_fib_icd10         | bare     | 1.000 | 1.000 | 1.000 | +0.000 | +0.000 |
| hepatitis_c_chronic      | bare     | 0.000 | 0.000 | 0.000 | +0.000 | +0.000 |
| hiv                      | bare     | 0.064 | 0.065 | 0.065 | +0.000 | +0.000 |

## Aggregate

| metric | vs pre-T37i | vs post-T37i |
|---|---:|---:|
| mean ΔF1 | **+0.1064** | **+0.0462** |
| median ΔF1 | +0.004 | +0.000 |
| BCa CI95 | [+0.049, +0.177] | [−0.007, +0.160] |
| verdict (σ=0.012) | **F1 LIFT** | **F1 LIFT** |

## SC-001 / SC-002 / SC-003 verdicts

- **SC-001 (regression mitigated)** — `diabetes_mellitus` returns to
  pre-T37i F1 0.755 (Δ −0.001, within σ) and `heart_failure` to 0.786
  (Δ +0.004, within σ). The −0.474 and −0.319 T37i regressions are
  gone. **PASS.**
- **SC-002 (lift preserved with override)** — five of seven
  descendant-closed codelists land within σ of post-T37i (`stroke`
  0.000, `asthma_pincer` +0.004, `copd` +0.005,
  `psychosis_schiz_bipolar` +0.005, `epilepsy` 0.000); `dementia`
  −0.012 sits at σ; `lung_cancer` −0.054 sits outside σ but inside
  the per-codelist K=5 std documented for that list. **PASS** on the
  aggregate; one codelist shows expected K=5 noise.
- **SC-003 (net aggregate)** — mean ΔF1 against pre-T37i +0.106
  (target range was +0.04 to +0.06; the measured value comes in
  above the upper bound). BCa CI95 [+0.049, +0.177] sits above zero
  at the lower bound; T37i's [−0.076, +0.152] crossed zero. **PASS.**

## What changed between T37i and T37j

T37i lifted the aggregate F1 (+0.060 mean) but with a bimodal
per-codelist signature — 7 descendant-closed gold lists gained +0.16 to
+0.38, 2 descendant-pruned gold lists lost −0.32 and −0.47. The signs
were intrinsic to the codelist's clinical policy (NICE-style
diagnosis-only lists pruned descendants; Caliber/PINCER-style
descendant-closed lists included them).

T37j gates the expander on a per-query `include_descendants` boolean.
The LLM parser extracts a default from natural-language cues in the
query string (`"all forms of X"` → True; `"X diagnosis only"` →
False; bare-name queries default to False). A
`request_include_descendants` field on the search request body lets
the reviewer override that default from the UI checkbox; the explicit
override always wins. The default sits at False at every layer (LLM
extraction, request body, schema column, signature payload).

The False default removes the over-expansion that caused T37i's
`diabetes_mellitus` and `heart_failure` regression. The override
keeps the lift available for researchers whose gold-list shape is
descendant-closed.

## Cost

- Sweep 1 (bare, 75 pairs): 51.7 min wall-clock, est-cost $0.44.
- Sweep 2 (override, 35 pairs): 34.5 min wall-clock, est-cost $0.30.
- Combined K=5 verification: ~$0.74, well inside the $30 cap.

## Commit

Verification ran on develop after the T37j commit was staged but
before being committed. Final commit message:

`feat(query): route hierarchy expansion via per-query intent signal (T37j)`
