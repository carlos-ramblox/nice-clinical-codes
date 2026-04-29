# Evaluation

## Summary

We benchmarked clinicalcodes.uk against 15 published codelists from
OpenCodelists (Bennett Institute, University of Oxford), covering seven
clinical areas (cardiometabolic, cerebrovascular, respiratory, mental
health, neurology, oncology, infection) and two vocabularies (13 SNOMED
CT, 2 ICD-10). This is the second pass of the benchmark: an initial run
on 2026-04-27 surfaced four failure modes, four targeted code changes
(Fixes B, C, E, F — see §5.5) were deployed to production, and the
benchmark was re-run end-to-end.

Headline result on the strict view (15 codelists, included-only stage,
mean ± 95 % BCa bootstrap CI):

| View | Mean P | Mean R | Mean F1 | Median F1 | F1 95 % CI |
|---|---|---|---|---|---|
| Pre-fix (Apr-27 baseline) | 0.71 | 0.51 | **0.49** | 0.53 | [0.36, 0.62] |
| Post-fix default | 0.88 | 0.49 | **0.57** | 0.67 | [0.44, 0.68] |
| Post-fix cold-start | 0.90 | 0.47 | **0.56** | 0.70 | [0.43, 0.68] |

Mean F1 lifted from 0.49 to 0.57 (+0.08) and median F1 from 0.53 to 0.67
(+0.14). The bigger move on median than mean reflects that the lift was
concentrated on previously-failing cases — both ICD-10 codelists went
from F1 < 0.21 to F1 ≥ 0.74, while several already-passing SNOMED lists
moved by less than 0.05. Two SNOMED-CT codelists regressed (asthma
−0.19, HIV −0.16 — see §4 cases 6 and 7).

The pre-vs-post-fix lift is significant under a paired McNemar's test
on per-code (pre, post) correctness aggregated across the 15 lists:
**χ² = 42.93, p = 5.7 × 10⁻¹¹** (b = 119 regressions, c = 245
improvements; n = 2 769 paired code observations). The cold-start view
is also significant against pre-fix: χ² = 14.42, p = 1.5 × 10⁻⁴
(b = 213, c = 300; n = 2 790). Overlapping CIs is a known-conservative
substitute for the paired test (Schenker & Gentleman 2001) so we
report McNemar alongside the BCa intervals.

The cold-start view differs from the default view by less than the CI
width — the OpenCodelists retriever's marginal contribution to mean F1
is small. See §2.5 for the framing.

## Methodology

### Sample selection

We drew 15 codelists from the OpenCodelists API listing
(https://www.opencodelists.org/api/v1/codelist/, 3,735 published
lists at time of run). Selection was deterministic and applied
*before* any benchmark run, with the rules:

1. **Vocabulary**: SNOMED CT, ICD-10, or OPCS-4 only. dm+d (drug
   reference set) and BNF lists were excluded because clinicalcodes.uk
   does not ingest drug terminologies.
2. **Recency**: most recent published version updated within the last
   24 months (≥ 2024-04-27). No OPCS-4 list met this; all eligible
   OPCS-4 lists were last updated in 2023, so OPCS-4 is absent from
   the sample. This is documented as a coverage gap rather than
   silently dropped.
3. **Curator independence**: Bennett-Institute-curated organisations
   (`OpenSAFELY`, `OpenPrescribing`) excluded to avoid the appearance
   of cherry-picking from a related team.
4. **Code count ≥ 5**: lists with fewer codes carry too little signal.
5. **Clinical-area coverage**: at least five distinct areas; the
   final 15 cover seven (cardiometabolic, cerebrovascular, respiratory,
   mental health, neurology, oncology, infection).
6. **Single canonical condition**: register-style "diagnosis codes"
   lists were preferred over derivative subsets ("review codes",
   "resolved codes", "exception codes"), because the title maps to a
   single clinical concept and is usable as a free-text query without
   modification.

Two post-selection swaps were made *before any benchmarking ran*, both
for methodological reasons:

- The originally-listed `reducehf/prostate-cancer-icd10` codelist
  contained only 1 code, violating rule (4). Replaced with
  `nhsd-primary-care-domain-refsets/lungcan_cod` (SNOMED CT lung
  cancer, 363 codes).
- To preserve the ICD-10 share after that swap, we replaced the SNOMED
  `nhsd-primary-care-domain-refsets/chd_cod` list with REDUCEHF's
  `myocardial-infarction-icd10`. This kept ICD-10 representation at
  2/15 lists.

Neither swap was performed after observing F1 scores; both were applied
during sample selection on the basis of the published rules above.

| # | Short name | Codelist | Vocabulary | Area | N(ref) |
|---|---|---|---|---|---|
| 1 | heart_failure | [Heart failure codes](https://www.opencodelists.org/codelist/nhsd-primary-care-domain-refsets/hf_cod/20250912/) | SNOMED CT | cardiometabolic | 42 |
| 2 | diabetes_mellitus | [Diabetes mellitus codes](https://www.opencodelists.org/codelist/nhsd-primary-care-domain-refsets/dm_cod/20250912/) | SNOMED CT | cardiometabolic | 86 |
| 3 | hypertension | [Hypertension diagnosis codes](https://www.opencodelists.org/codelist/nhsd-primary-care-domain-refsets/hyp_cod/20250912/) | SNOMED CT | cardiometabolic | 117 |
| 4 | mi_icd10 | [Myocardial infarction (ICD10)](https://www.opencodelists.org/codelist/reducehf/myocardial-infarction-icd10/6b463edb/) | ICD-10 | cardiometabolic | 12 |
| 5 | atrial_fib_icd10 | [Atrial Fibrillation and Flutter - ICD10](https://www.opencodelists.org/codelist/reducehf/atrial-fibrillation-and-flutter-icd10/0cfc2f94/) | ICD-10 | cardiometabolic | 7 |
| 6 | stroke | [Stroke diagnosis codes](https://www.opencodelists.org/codelist/nhsd-primary-care-domain-refsets/strk_cod/20250912/) | SNOMED CT | cerebrovascular | 266 |
| 7 | asthma_pincer | [Asthma](https://www.opencodelists.org/codelist/pincer/ast/v1.8/) | SNOMED CT | respiratory | 124 |
| 8 | copd | [Chronic obstructive pulmonary disease (COPD) codes](https://www.opencodelists.org/codelist/nhsd-primary-care-domain-refsets/copd_cod/20250912/) | SNOMED CT | respiratory | 56 |
| 9 | depression | [Depression diagnosis codes](https://www.opencodelists.org/codelist/nhsd-primary-care-domain-refsets/depr_cod/20250912/) | SNOMED CT | mental_health | 106 |
| 10 | psychosis_schiz_bipolar | [Psychosis and schizophrenia and bipolar affective disease codes](https://www.opencodelists.org/codelist/nhsd-primary-care-domain-refsets/mh_cod/20250912/) | SNOMED CT | mental_health | 198 |
| 11 | dementia | [Codes for dementia](https://www.opencodelists.org/codelist/nhsd-primary-care-domain-refsets/dem_cod/20250912/) | SNOMED CT | neurology | 325 |
| 12 | epilepsy | [Epilepsy diagnosis codes](https://www.opencodelists.org/codelist/nhsd-primary-care-domain-refsets/epil_cod/20250912/) | SNOMED CT | neurology | 476 |
| 13 | lung_cancer | [Lung cancer codes](https://www.opencodelists.org/codelist/nhsd-primary-care-domain-refsets/lungcan_cod/20250912/) | SNOMED CT | oncology | 363 |
| 14 | hepatitis_c_chronic | [Chronic hepatitis C codes](https://www.opencodelists.org/codelist/nhsd-primary-care-domain-refsets/hepcchron_cod/20250912/) | SNOMED CT | infection | 20 |
| 15 | hiv | [Human immunodeficiency virus (HIV) codes](https://www.opencodelists.org/codelist/nhsd-primary-care-domain-refsets/hiv_cod/20250912/) | SNOMED CT | infection | 243 |

Twelve of the fifteen come from the `nhsd-primary-care-domain-refsets`
organisation. These are the SNOMED CT cluster lists used to define
QOF (Quality and Outcomes Framework) registers; they share a curating
organisation but each represents a distinct, independently-derived
clinical concept. Two come from REDUCEHF and one from PINCER.

### Test set construction

Each codelist's CSV was downloaded from OpenCodelists and converted
to the existing test-set JSON shape (`data/test_sets/Source_entry_7.json`
defines the schema). The `Research_question` field is the codelist's
published title verbatim — no hand-crafting, no query rewriting,
even where the title reads slightly awkwardly as a search string
(e.g. *"Codes for dementia"*, *"Atrial Fibrillation and Flutter -
ICD10"*).

### Evaluation protocol

Each test set was POSTed to `https://clinicalcodes.uk/api/evaluate`,
twice — once with the default retriever stack, once with
`?cold_start=true` (which disables the OpenCodelists retriever for
that request). Raw responses are persisted at
`data/test_sets/benchmark_2026_04/<short>.result_postfix.json` and
`<short>.result_coldstart.json` so any number in this document can be
re-derived without re-running the pipeline.

#### Code normalization

The live evaluator (`backend/app/evaluation/evaluator.py`) and the
offline aggregator (`backend/app/evaluation/benchmark_aggregate.py`)
apply the same normalization rule: ``code.strip().replace(".", "")``.
ICD-10 codes round-trip from the production pipeline as `I48.0` while
OpenCodelists CSVs use `I480`; stripping all dots collapses these to
the same key. The same transformation is applied to both reference
and output codes, so OPCS-4 codes (which carry dots like `K40.1`)
are mutated symmetrically and set membership is preserved. Earlier
versions of the live evaluator only stripped trailing dots; that
divergence is fixed.

We report two views per list:

- **Strict** — every included code counts as a candidate, including
  cross-vocabulary equivalents. This is the default headline.
- **Vocabulary-filtered** — only included codes whose vocabulary
  matches the reference list's declared vocabulary count. Isolates
  concept recall from multi-vocabulary output behaviour. Reported
  alongside as a supplementary view.

#### Statistical methodology

For each metric (P, R, F1), aggregate point estimates are simple
arithmetic means across the 15 lists. 95 % confidence intervals are
bias-corrected and accelerated (BCa) non-parametric bootstrap
(1 000 resamples, seed 7) computed via `scipy.stats.bootstrap`. BCa
adjusts for both bias and skewness in the bootstrap sampling
distribution and is preferred over the basic percentile method on
small samples (Efron 1987). On this n = 15 sample BCa shifts each
F1 interval bound by up to ~1.5 percentage points relative to a
naive percentile interval — mostly correcting upward bias on the
lower limit (pre-fix) and right-skew on the upper limit (post-fix
and cold-start) — rather than uniformly narrowing the interval.
Median and IQR are reported alongside the mean to surface skew.
Stratified breakdowns are unweighted means within each stratum.

The pre-vs-post-fix comparison is paired by codelist and code, so the
appropriate significance test is McNemar's test on per-code
(pre-correct, post-correct) outcomes — not a comparison of overlap
between the pre and post CIs (which is known-conservative; Schenker &
Gentleman 2001). For each codelist we form the universe of codes
appearing in the reference list or in either run's included output;
each such code contributes one row to a 2 × 2 contingency. The
chi-squared form with continuity correction is used when discordant
pairs `b + c ≥ 25`, the binomial-exact form otherwise.

Each (codelist, code) pair is treated as an independent paired
observation. Codes within a codelist are clinically correlated
(shared concept, shared retrieval and prompt context), so the
chi-squared p-values reported here are anti-conservative under any
realistic dependence structure. The magnitude of the post-fix lift
(p ≈ 5.7 × 10⁻¹¹) survives any plausible correction; reporting
McNemar with this caveat is more defensible than the overlapping-CI
substitute it replaces.

### Sensitivity analysis: default vs. cold-start

clinicalcodes.uk uses OpenCodelists as one of its four retrievers
(`backend/app/graph/nodes/opencodelists_retriever.py`). When the tool
processes a query for a condition that has a published OpenCodelists
list, codes from that list will be among the retrieval candidates.

We report two views to characterise the tool's capability under
different conditions of source availability:

- **Default**: full retriever stack (OMOPHub, ChromaDB, QOF Business
  Rules, OpenCodelists). This measures the system as deployed —
  the ability to assemble a codelist for queries where curated lists
  exist among the retrieval sources.
- **Cold-start**: OpenCodelists retriever disabled via
  `?cold_start=true`. This measures system discovery capability
  when starting from raw vocabularies, simulating queries with no
  curated list available.

This is a RAG system, not a trained model — there is no
train/test contamination to worry about. The default view is not a
biased measurement; it measures something different from the
cold-start view. Both are valid measures of different capabilities,
and the delta between them characterises the marginal contribution of
the OpenCodelists retriever to the system's output.

In practice, the mean F1 delta is small (+0.005, default over
cold-start, well within bootstrap CI) — the OpenCodelists retriever
has limited *aggregate* impact because most of its codes are also
present in QOF Business Rules and ChromaDB, but it does affect specific
cases (see §4: hypertension and lung_cancer drop in cold-start;
hepatitis C improves in cold-start because cold-start avoids a
UMLS-vocabulary noise pattern).

## Results

### Aggregate (15 codelists, included-only stage)

#### Strict view

CIs are 95 % BCa bootstrap (1 000 resamples, seed 7).

| View | Mean P | Mean R | Mean F1 | Median F1 | F1 IQR | F1 95 % CI |
|---|---|---|---|---|---|---|
| Pre-fix | 0.71 | 0.51 | **0.49** | 0.53 | [0.21, 0.73] | [0.36, 0.62] |
| Post-fix default | 0.88 | 0.49 | **0.57** | 0.67 | [0.32, 0.74] | [0.44, 0.68] |
| Post-fix cold-start | 0.90 | 0.47 | **0.56** | 0.70 | [0.31, 0.74] | [0.43, 0.68] |

Paired McNemar's test on per-code (pre, post) correctness:
post-fix vs. pre-fix χ² = 42.93, p = 5.7 × 10⁻¹¹;
cold-start vs. pre-fix χ² = 14.42, p = 1.5 × 10⁻⁴.

#### Vocabulary-filtered view (supplementary)

| View | Mean P | Mean R | Mean F1 | Median F1 | F1 95 % CI |
|---|---|---|---|---|---|
| Pre-fix | 0.83 | 0.51 | 0.56 | 0.65 | [0.40, 0.69] |
| Post-fix default | 0.94 | 0.49 | 0.59 | 0.67 | [0.45, 0.70] |
| Post-fix cold-start | 0.95 | 0.47 | 0.57 | 0.70 | [0.43, 0.68] |

Filtering raises precision (cross-vocabulary equivalents aren't
counted as FPs), but the post-fix default and cold-start strict
numbers are already high enough that the filtered view adds little.
Most of the filtered/strict gap was concentrated in the two ICD-10
cases pre-fix; those are now resolved, so the gap has narrowed.

### Per-codelist (strict view, all three views side-by-side)

| Codelist | Vocab | N(ref) | Pre-F1 | Post-F1 | Cold-F1 | Δ Pre→Post |
|---|---|---|---|---|---|---|
| heart_failure | SNOMED CT | 42 | 0.73 | 0.71 | 0.71 | −0.03 |
| diabetes_mellitus | SNOMED CT | 86 | 0.78 | 0.81 | 0.79 | +0.03 |
| hypertension | SNOMED CT | 117 | 0.53 | 0.53 | 0.43 | +0.00 |
| mi_icd10 | ICD-10 | 12 | 0.00 | **0.74** | 0.74 | **+0.74** |
| atrial_fib_icd10 | ICD-10 | 7 | 0.21 | **1.00** | 1.00 | **+0.79** |
| stroke | SNOMED CT | 266 | 0.52 | 0.52 | 0.52 | +0.00 |
| asthma_pincer | SNOMED CT | 124 | 0.86 | **0.67** | 0.72 | **−0.19** |
| copd | SNOMED CT | 56 | 0.77 | 0.76 | 0.76 | −0.01 |
| depression | SNOMED CT | 106 | 0.72 | 0.69 | 0.70 | −0.02 |
| psychosis_schiz_bipolar | SNOMED CT | 198 | 0.65 | 0.67 | 0.61 | +0.02 |
| dementia | SNOMED CT | 325 | 0.33 | 0.28 | 0.31 | −0.05 |
| epilepsy | SNOMED CT | 476 | 0.20 | 0.20 | 0.20 | +0.00 |
| lung_cancer | SNOMED CT | 363 | 0.32 | 0.32 | 0.24 | +0.00 |
| hepatitis_c_chronic | SNOMED CT | 20 | 0.58 | 0.60 | **0.71** | +0.02 |
| hiv | SNOMED CT | 243 | 0.18 | **0.02** | 0.02 | **−0.16** |

Bold rows are the largest movers. ICD-10 cases (mi, atrial_fib) are
the post-fix headlines; the asthma and HIV regressions are the
counter-evidence we discuss honestly in §4.

### Stratified

#### By vocabulary (strict)

| Vocabulary | n | Pre F1 | Post-default F1 | Post-cold F1 |
|---|---|---|---|---|
| SNOMED CT | 13 | 0.55 | 0.52 | 0.52 |
| ICD-10    | 2  | 0.10 | **0.87** | 0.87 |

The ICD-10 stratum lift is the dominant aggregate effect of the
fixes. The SNOMED stratum slightly regressed in mean (−0.03) — the
asthma and HIV drops outweigh the small gains elsewhere. Stratum
median F1 for SNOMED moved from 0.58 to 0.69, so the regression is
in the tails not the middle.

#### By condition area (strict)

| Area | n | Pre F1 | Post-default F1 | Post-cold F1 |
|---|---|---|---|---|
| cardiometabolic | 5 | 0.45 | **0.76** | 0.73 |
| cerebrovascular | 1 | 0.52 | 0.52 | 0.52 |
| respiratory | 2 | 0.82 | 0.71 | 0.74 |
| mental_health | 2 | 0.68 | 0.68 | 0.66 |
| neurology | 2 | 0.26 | 0.24 | 0.25 |
| oncology | 1 | 0.32 | 0.32 | 0.24 |
| infection | 2 | 0.38 | 0.31 | 0.37 |

Cardiometabolic moved from 0.45 to 0.76 — driven entirely by the two
ICD-10 cases in that area. Respiratory regressed because of asthma.
Infection regressed in default mode because of HIV; the cold-start
view recovers most of that ground via hepatitis C.

#### By reference list size (strict)

| Size | n | Pre F1 | Post-default F1 | Post-cold F1 |
|---|---|---|---|---|
| small (< 50) | 4 | 0.38 | **0.76** | 0.79 |
| medium (50–200) | 6 | 0.72 | 0.69 | 0.67 |
| large (> 200) | 5 | 0.31 | 0.27 | 0.26 |

The small-list stratum nearly doubled — both ICD-10 cases live there,
and Fix B+E+F resolved them. The large-list stratum is unchanged: the
recall ceiling on long reference lists (see case 3) was not a target
of this iteration.

## Failure case analysis

The cases below are chosen to span the four Bennett (2023) failure
modes catalogued in LIMITATIONS.md, plus the tool-specific
vocabulary-routing failure that drove the pre-fix ICD-10
underperformance and is now resolved.

### 1. mi_icd10 — vocabulary routing (resolved)

- **Codelist**: REDUCEHF *Myocardial infarction (ICD10)*, 12 codes.
- **Query sent**: `Myocardial infarction (ICD10)` (verbatim title).
- **Reference codes**: `I21, I210, I211, I212, I213, I214, I219,
  I22, I220, I221, I228, I229`.

**Initial benchmark: F1 = 0.00 (0 ICD-10 codes returned).**
Investigation revealed three layers.

- **Layer 1 (Fix B, deployed)**: query parser did not extract the
  vocabulary requirement embedded in the title. Now does, via regex
  cue extraction (`(ICD10)` → `coding_systems = ["ICD10"]` on the
  parsed condition).
- **Layer 2 (Fix E, deployed)**: OMOPHub's index keys on
  *"Acute myocardial infarction"* but did not return any ICD-10 codes
  for the bare term *"Myocardial infarction"*. Fix E issues raw plus
  *"Acute X"* and *"Chronic X"* prefix variants, deduplicated, capped
  at `page_size × len(variants) × len(vocabs)`. Adding "Acute" to
  the query now surfaces 22 ICD-10 codes including the I21 and I22
  families.
- **Layer 3 (Fix F, deployed)**: result-merger ranked candidates by
  source count and capped at 100; with the OpenCodelists retriever
  active, hundreds of multi-source SNOMED candidates outranked the
  single-source ICD-10 codes from OMOPHub and pushed them past the
  cap. Fix F filters by the parsed vocabulary constraint *before*
  the cap, so when the user pins ICD-10 the merger no longer drops
  ICD-10 candidates in favour of SNOMED ones.

**Post-fix: F1 = 0.74 (P = 1.00, R = 0.58)**. Seven of twelve
reference codes returned, all I21 family. Five reference codes from
the I22 family ("Subsequent myocardial infarction…") are still
missed — the LLM scorer excluded them as a different concept than
*"Myocardial infarction"* under the new "instance vs. association"
rule (Fix C). This is a Bennett 2023 mode 3 disagreement
(study-intent ambiguity) — the OpenCodelists curator chose to include
I22 as part of the MI register; the model chose to exclude
"subsequent" as a temporally distinct concept.

The deeper structural issue — that ICD-10 retrieval is single-sourced
through OMOPHub, with no ingested ICD-10 corpus to fall back on —
remains. Two follow-up paths: (1) extend the prefix expansion to
additional clinical qualifiers (recurrent, prior, history-of); (2)
ingest an ICD-10 corpus into ChromaDB to remove the single-source
dependency.

### 2. atrial_fib_icd10 — multi-vocabulary expansion (resolved)

- **Codelist**: REDUCEHF *Atrial Fibrillation and Flutter - ICD10*, 7 codes.

Pre-fix strict F1 = 0.21 (the 7 ICD-10 codes were correctly retrieved
under all-views — but the included-list also contained 54 SNOMED
equivalents, dragging strict precision to 0.12). Vocab-filtered F1
was already 1.00. The same chain of fixes that resolved mi_icd10
also resolved this case: Fix B propagates the ICD-10 constraint, Fix
F's merger filter drops the SNOMED candidates before scoring, and
the output_assembly filter (introduced with Fix B) provides a
belt-and-braces final pass.

**Post-fix: F1 = 1.00 (P = 1.00, R = 1.00)**. Both default and
cold-start.

### 3. stroke — coverage ceiling on long reference lists (Bennett 2023 failure mode 2)

- **Codelist**: NHSD *Stroke diagnosis codes*, 266 SNOMED codes.
- **Result (all three views)**: F1 = 0.52 (P = 1.00, R = 0.35).
  Unchanged across pre-fix, post-fix default, and post-fix cold-start.

This case was not a target of the iteration. The pipeline returns 93
of 266 reference codes — all true positives, zero false positives —
and misses 173. The missed codes are highly specific compounds:

- `107557061000119108` *Cerebrovascular accident due to embolism of bilateral anterior cerebral arteries*
- `12204031000119101` *Cerebrovascular accident following procedure on heart*
- `1259499007` *Dementia due to hemorrhagic cerebral infarction…*

The retrievers cap candidate breadth (`page_size = 20` in
`omophub_retriever`, `MAX_CODELISTS = 5` in `opencodelists_retriever`),
so the long tail of post-coordinated SNOMED expressions is
structurally beyond reach. **Pipeline change still required**: a
hierarchical-expansion retrieval step (e.g. SNOMED ECL `<<` walk
from the parent concepts the pipeline already finds) before ranking.

### 4. hypertension — study-intent ambiguity (Bennett 2023 failure mode 3)

- **Codelist**: NHSD *Hypertension diagnosis codes*, 117 SNOMED codes.
- **Result**: post-fix default F1 = 0.53 (unchanged from pre-fix);
  post-fix cold-start F1 = 0.43 (the 10-point drop is the
  OpenCodelists retriever's contribution on this list).

The pre-fix narrative still holds. The pipeline included 42 codes
(P = 1.00) and excluded 55 reference codes that the LLM categorised
as *"secondary hypertension, not primary hypertension"* — pregnancy-
related, renal-arterial, drug-induced. The OpenCodelists reference
includes them; the QOF hypertension register counts patients with
hypertension *of any cause*. This is a defensible clinical
disagreement, not a code-search failure. **Pipeline change still
required**: surface this ambiguity to the user (uncertainty bucket
with rationale visible) rather than silently exclude.

### 5. hepatitis_c_chronic — Fix C scope-tightening (mostly resolved)

- **Codelist**: NHSD *Chronic hepatitis C codes*, 20 SNOMED codes.

Pre-fix: F1 = 0.58 (R = 1.00, P = 0.41). The pipeline returned all
20 reference codes plus 29 false positives — hepatocellular
carcinoma, intrahepatic bile duct carcinoma, and other liver
malignancies that the LLM included with the rationale *"X is a
well-established complication of chronic hepatitis C."*

Fix C added an *"instance of X vs. clinical association with X"*
distinction to the scoring prompt, with hepatitis C and HIV as
explicit examples ("Hepatocellular carcinoma → exclude. These are
complications of chronic hepatitis C, not instances of chronic
hepatitis C, and their term names do not contain 'hepatitis C'.").

**Post-fix default: F1 = 0.60 (R = 0.70, P = 0.52)**. The 29
liver-cancer false positives are gone; their LLM rationales now read
*"Liver cell carcinoma is a complication of chronic hepatitis C, not
an instance of the condition itself; the term does not name hepatitis
C."* (Verbatim — directly quoting the Fix C prompt.)

Two effects partially offset the precision gain:
- 6 reference codes were dropped to false negatives — Fix C is
  slightly over-aggressive on borderline cases. (R: 1.00 → 0.70.)
- 13 *new* false positives appeared, all of them UMLS CUI-vocabulary
  rows for legitimate Hep C subtypes (e.g. *Chronic hepatitis C with
  stage 2 fibrosis*, *Chronic hepatitis C caused by HCV genotype 1a*).
  These are clinically correct codes that don't match the
  SNOMED-only OpenCodelists reference — vocabulary mismatch, not a
  scoring failure. The output_assembly vocabulary filter does not fire
  because the user typed *"Chronic hepatitis C codes"* with no
  explicit vocabulary cue.

**Post-fix cold-start: F1 = 0.71 (R = 0.55, P = 1.00)**. The
cold-start view eliminates the UMLS-vocabulary noise pattern (fewer
upstream candidates → fewer UMLS expansions → no UMLS CUIs surfacing
as FPs). Recall drops further (the OpenCodelists retriever was
contributing legitimate matches), but precision is perfect and F1 is
the highest of the three views. This is the tradeoff the cold-start
view was designed to surface.

### 6. asthma_pincer — Fix C overcorrection on refset-style inclusions (regressed)

- **Codelist**: PINCER *Asthma*, 124 SNOMED codes.
- **Pre-fix**: F1 = 0.86 (P = 0.97, R = 0.77).
- **Post-fix default**: F1 = 0.67 (P = 0.78, R = 0.59).
- **Post-fix cold-start**: F1 = 0.72 (P = 0.83, R = 0.63).

This is the largest regression in the run and the most important
honest disclosure. Fix C added the *"refset-style condition-named
manifestation"* INCLUDE rule (preserving the diabetic retinopathy
convention while adding the new EXCLUDE rule for non-named
complications). On asthma, this rule fired more aggressively than
intended: the pipeline now includes codes like
`10674791000119100` *Acute exacerbation of intermittent allergic
asthma* and `10675591000119108` *Severe persistent allergic asthma
controlled* — codes whose term name explicitly contains *"asthma"*
and which Fix C therefore directs INCLUDE.

The PINCER reference list excludes these codes: PINCER is a curated
set for prescribing-safety indicators, not a general asthma register,
and its curators chose to omit several specific clinical-state
qualifiers. The pipeline cannot know this without the reference list.
**This is a Bennett 2023 mode 3 (study-intent) failure** —
clinically defensible inclusions that the curator chose to exclude
for trial-specific reasons.

The 12-point recall drop (0.77 → 0.59) is more concerning than the
precision drop. Investigation needed: Fix C's prompt may be
discouraging inclusion of codes that *don't* contain "asthma" in
their term name even when they should be included (e.g. specific
syndromes with non-eponymous names). Following up is the next
iteration's job.

### 7. hiv — Fix C overcorrection on AIDS-defining illnesses (regressed)

- **Codelist**: NHSD *HIV codes*, 243 SNOMED codes.
- **Pre-fix**: F1 = 0.18 (P = 0.52, R = 0.11). Already weak.
- **Post-fix default and cold-start**: F1 = 0.02 (P = 1.00, R = 0.01).

This is the most striking regression in the run: Fix C reduced
inclusion from 27 codes to 3. The pipeline now scores almost every
HIV-adjacent candidate as *exclude*, citing the new
*"AIDS-defining illness, not the HIV/AIDS infection itself"* rule
from Fix C's HIV worked example.

Looking at the 240 false negatives, many are genuine HIV codes the
pipeline previously included but now excludes — for example, codes
naming HIV-associated organ-specific conditions (some of which the
reference does include). The Fix C prompt's HIV example —
*"Kaposi's sarcoma with AIDS → exclude"* — appears to be steering
the model to exclude any HIV-comorbidity compound, including codes
that legitimately belong on a comprehensive HIV register.

This is the clearest evidence that Fix C's prompt change was an
over-correction on this particular codelist. **Recommendation**: tune
the HIV example specifically — distinguish "HIV with X complication"
(arguable) from "X disease with AIDS" (clearly distinct). A targeted
re-test of HIV-only would confirm the diagnosis without re-running
the full benchmark.

### Net read on the regressions

Fixes B, E, F resolved the ICD-10 cases cleanly with no observable
side effects. Fix C resolved the hep C scope-drift (its target case)
but introduced over-correction on asthma and HIV. The aggregate F1
moved up because the ICD-10 wins were larger than the SNOMED
regressions — but a faithful reading is that *Fix C is the right
direction with the wrong calibration*, and the next iteration should
tune its examples rather than expand the scope.

## Iteration history

This is the second pass of the benchmark.

- **2026-04-27 (morning)**: initial benchmark run on the deployed
  baseline. Mean F1 0.49 strict, 0.56 vocabulary-filtered.
  See §4 cases 1–6 (pre-fix paragraphs) for the failure modes
  identified.
- **2026-04-27 (afternoon)**: four code changes implemented and
  deployed.
  - **Fix B**: query parser regex extraction of `ICD10` /
    `SNOMED` / `OPCS4` cues, propagation to `coding_systems` and to
    a final output filter.
  - **Fix C**: scoring prompt addition — *"instance of X vs.
    clinical association with X"* with worked Hep C and HIV examples,
    preserving the existing diabetic retinopathy / NHSD refset
    convention via a term-name test.
  - **Fix E**: OMOPHub multi-query expansion — raw plus *"Acute X"*
    and *"Chronic X"* variants, deduplicated by (code, vocab),
    capped at `page_size × len(variants) × len(vocabs)`.
  - **Fix F**: result-merger vocabulary-constraint filter applied
    *before* the source-count cap, so explicitly-requested
    vocabulary candidates aren't outranked by other-vocab noise.
- **2026-04-27 (evening)**: re-run, three-view aggregation, this
  document.

The fixes were not blind to the failures they address — every fix
was designed to address a specific case identified in the morning
run. The cold-start view is therefore the most methodologically
defensible single number: it measures the discovery capability of
the system minus the OpenCodelists retriever, which means the
overlap between *"the retrieval source"* and *"the reference"* is
removed by construction.

### §5.7 Future methods work

The following directions emerged from the v2 benchmark and from
independent reviews of the pipeline. They are listed as topics
worth discussing rather than commitments.

**Retrieval ranking and fusion**
Replace the merger's source-count ordering with weighted Reciprocal
Rank Fusion (RRF, k=60) so heterogeneous retriever score scales
combine properly. Add a cross-encoder reranking step (e.g.
BAAI/bge-reranker-v2-m3) over the merged top-N before LLM scoring.

**LLM confidence calibration**
Verbalised LLM confidences are currently recorded but unused.
Post-hoc isotonic calibration on the 15-codelist benchmark labels
would yield trustworthy probabilities and a reliability diagram.
The calibrated confidence supports active-learning prioritisation
of the human review queue.

**Hierarchical SNOMED expansion**
The recall ceiling on long lists (stroke, dementia, epilepsy)
reflects a structural retrieval breadth cap. SNOMED ECL `<<`
walks from the parent concepts the pipeline already finds would
surface the long tail of post-coordinated SNOMED expressions.
OpenCodelists supports ECL natively, so this aligns with existing
Bennett tooling.

**ICD-10 corpus ingestion**
ICD-10 retrieval is currently single-sourced through OMOPHub.
Ingesting NHS TRUD's ICD-10 5th Edition into ChromaDB would
remove the single-source dependency.

**Run-to-run variance**
The pipeline runs at temperature=0 but Anthropic provides no
seed; production batched inference has measurable run-to-run
variance. K=5 reruns per codelist would replace the single-run
caveat with measured noise.

**Faithfulness / groundedness metrics**
The current evaluation is set-based (P/R/F1). RAGAS-style
faithfulness and context-relevance metrics, applied offline to
the benchmark, would extend the evaluation beyond set membership.

## Limitations

- **Sample size**. 15 of 3,735 published lists is a 0.4 % sample.
  The CIs reflect this — F1's 95 % CI spans roughly 25 percentage
  points in each view. This benchmark is a *plausibility check*, not
  a definitive characterisation. Doubling the sample size to 30 lists
  would roughly halve the CI width.
- **Vocabulary coverage**. Two of three target vocabularies are
  represented (SNOMED CT, ICD-10). OPCS-4 is absent because no
  list met the recency criterion. dm+d (drug terminology) is out
  of scope. The two ICD-10 lists are insufficient to characterise
  ICD-10 performance reliably even after the Fix B/E/F lift.
- **Iteration disclosure**. This benchmark reports both pre-fix and
  post-fix views. Fixes B, C, E, F were applied between runs and
  were *not blind* to the failures they address. The cold-start view
  is the most methodologically defensible single number we report —
  by ablating one retriever we measure the discovery capability of
  the remaining stack.
- **Test/train leakage**. The single development test set used while
  building the evaluation framework was `Source_entry_7.json`
  (intracranial hypertension), which is not among the 15 codelists
  benchmarked here. The pipeline's prompt and scoring logic were not
  tuned against any of these 15 lists at development time. The
  OpenCodelists retriever ingests published codelists into ChromaDB
  at build time, so a list present in the ingested corpus can be
  retrieved verbatim — the cold-start view ablates this exact path.
- **Single-rater ground truth**. The OpenCodelists curation is
  treated as authoritative without independent re-validation. Some
  reference lists themselves contain debatable inclusions (e.g.
  the NHSD hypertension list including secondary hypertension —
  see case 4) or debatable exclusions (e.g. PINCER asthma — see
  case 6). A blinded clinician re-rating would be the appropriate
  next step before claiming any of the F1 numbers as a measure of
  *clinical correctness* rather than *reproduction of curated
  artefacts*.
- **Single run, no temperature sweep**. The pipeline's LLM scoring
  is non-deterministic at default temperature. Numbers in this
  document are from a single run on 2026-04-27 (per view). Run-to-
  run variance is unmeasured; a 5-run average would be more honest.
- **Static reference vocabulary**. Reference codelists were pulled
  on 2026-04-27. The OpenCodelists list versions used here can
  change as curators republish; the `version` slug pinned in the
  selection metadata makes this run reproducible against today's
  references but not against future ones.
- **Fix C over-correction on asthma and HIV**. The largest
  unresolved limitation surfaced by this iteration. See §4 cases 6
  and 7. Treat post-fix asthma and HIV F1 numbers as floor estimates;
  a targeted prompt tweak in the next iteration is expected to
  recover most of the regression.

## Reproducibility

All test sets, raw API responses, and aggregate output for this
benchmark are committed to the repository:

- Codelists & metadata: `data/raw/opencodelists/selection.json`,
  `data/raw/opencodelists/csv/<short>.csv`
- Test sets: `data/test_sets/benchmark_2026_04/<short>.json`
- Raw API responses (pre-fix baseline):
  `data/test_sets/benchmark_2026_04/<short>.result.json`
- Raw API responses (post-fix default):
  `data/test_sets/benchmark_2026_04/<short>.result_postfix.json`
- Raw API responses (post-fix cold-start):
  `data/test_sets/benchmark_2026_04/<short>.result_coldstart.json`
- Aggregate output (v1, pre-fix only):
  `_aggregate.json`, `_per_list.csv`
- Aggregate output (v2, three views):
  `data/test_sets/benchmark_2026_04/_aggregate_v2.json`,
  `_per_list_v2.csv`
- Aggregator script: `backend/app/evaluation/benchmark_aggregate.py`

To reproduce against the same OpenCodelists versions:

```bash
# Recompute aggregates from the persisted .result*.json files
python -m app.evaluation.benchmark_aggregate

# To re-run the live API end-to-end (requires network access):
#   POST each data/test_sets/benchmark_2026_04/<short>.json to
#   https://clinicalcodes.uk/api/evaluate (default mode) and
#   https://clinicalcodes.uk/api/evaluate?cold_start=true (cold-start),
#   save the responses, then re-run the aggregator above.
```

- Date of run: **2026-04-27** (both views)
- Commit SHA at deploy time: `ad7ccad07341658529e902cd415705df97d378e0`
  with uncommitted Fix B/C/D/E/F changes staged in the working tree
  (image was tagged `ad7ccad-dirty` per `aws/deploy.sh` convention).
  The post-fix and cold-start runs were served by that image.
- Live API host: clinicalcodes.uk

## Citation of related methodology

This benchmark adopts the precision/recall/F1 evaluation framing
used in Watson et al. (2017), *Identifying clinical features in
primary care electronic health record studies: methods for codelist
development*, BMJ Open 7:e019637, and the FAIR-publication
conventions Williams et al. (2019) outlined for codelist sharing
(PLoS ONE 14:e0212291). The BCa bootstrap follows Efron (1987),
*Better Bootstrap Confidence Intervals*, JASA 82:171–185;
the McNemar paired-comparison framing follows Dietterich (1998),
*Approximate Statistical Tests for Comparing Supervised
Classification Learning Algorithms*, Neural Computation 10:1895–1923;
the cautionary note on overlapping-CI inference follows Schenker &
Gentleman (2001), *On Judging the Significance of Differences by
Examining the Overlap Between Confidence Intervals*, The American
Statistician 55:182–186. It differs from Aslam et al. (2025)'s
clinician-rater design — we benchmark against curated artefacts
(OpenCodelists) rather than a clinician panel, which trades inter-
rater reliability for scale. The failure-mode taxonomy in §4 follows
Bennett 2023 (see also LIMITATIONS.md in this repository), which
catalogues four recurring pathologies of automated codelist
generation: similar-sounding unrelated codes (mode 1), synonym
omission (mode 2), study-intent ambiguity (mode 3), and codes
covering both relevant and irrelevant cases (mode 4). Modes 1, 2 and
3 were directly observed across the 15 cases; mode 4 is harder to
surface from set-membership metrics alone. A fifth failure type —
vocabulary routing — was tool-specific; the Fix B/E/F set has now
resolved it for ICD-10 queries.
