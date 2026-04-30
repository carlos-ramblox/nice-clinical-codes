# Limitations

## Framework

Wood and Higgins (Bennett Institute for Applied Data Science) published a blog post in September 2023 titled [*What Are Codelists and How Are They Constructed?*](https://www.bennett.ox.ac.uk/blog/2023/09/what-are-codelists-and-how-are-they-constructed/), in which four common failure modes in codelist construction are identified. This document maps the clinicalcodes.uk pipeline against each of those failure modes. It is intended as an honest account of where the pipeline mitigates the risk and, more importantly, where it does not.

The pipeline parses a natural-language query into structured conditions, runs four retrievers in parallel (OMOPHub, OpenCodelists, QOF Business Rules, and a ChromaDB semantic index over ingested vocabulary), enriches the merged candidate set against UMLS Metathesaurus relationships, scores each candidate with Claude Haiku 4.5 at temperature 0, and routes the result through a clinician review gate before any artefact is signed and exported.

## Failure mode 1: Including similar-sounding but unrelated codes

Bennett's example: "ocular hypertension" being mistakenly included in a hypertension codelist.

**How the pipeline addresses this.** Each candidate is scored by Claude Haiku 4.5, which returns an `include`/`exclude`/`uncertain` decision, a confidence score, and a one-sentence rationale; all three are stored in the audit log. The scoring prompt explicitly instructs the model to exclude codes for unrelated comorbidities and to mark ambiguous cases as `uncertain` rather than silently including them. Candidate provenance (which retriever surfaced the code, how many sources support it) is preserved through to review, so a reviewer can see at a glance whether a code is corroborated by multiple vocabularies.

**Residual risk and gaps.** The LLM rationale is a heuristic, not a proof; the pipeline does not formally guarantee that similar-sounding unrelated codes are excluded. There is no curated adversarial test set specifically targeting this failure mode — the [EVALUATION.md](./EVALUATION.md) benchmark compares against published OpenCodelists, which does not isolate this class of error. The reviewer must still verify exclusions manually.

## Failure mode 2: Omitting synonyms

Bennett's example: a sore-throat codelist failing to include codes for pharyngitis.

**How the pipeline addresses this.** UMLS Metathesaurus enrichment expands each top-ranked candidate with its synonyms (atom-level string variants of the same CUI), narrower concepts (`RN` relations), and sibling concepts (`SIB` relations). Running four retrievers in parallel further reduces the chance that a synonym held in one vocabulary but not another is missed.

**Residual risk and gaps.** UMLS coverage is itself incomplete, and the enrichment step is capped at the top 30 ranked candidates per query, so tail-end synonyms may not be surfaced. There is no automated check that all canonical synonyms for a concept have been considered; the reviewer must still confirm obvious synonyms are present.

## Failure mode 3: Misunderstanding study intent

Bennett's example: whether gestational diabetes belongs in a diabetes codelist depends on the specific research aims.

**How the pipeline addresses this.** The query parser converts free-text input into structured conditions that are passed verbatim to the scoring step. Reviewer overrides are captured with a written comment in the audit log and contribute to the SHA-256 signature applied on approval, providing a durable record of the intent applied at review time.

**Residual risk and gaps.** The scoring prompt encodes a small set of hardcoded study-intent heuristics for known cases (for example, distinguishing type 1 from type 2 diabetes); these are baked into the prompt rather than configurable per-query, and cannot be extended by the reviewer at run time. The pipeline does not currently support reviewer-specified study-context disambiguators (for example, "diabetes excluding pregnancy-related"). A reviewer wishing to apply the gestational-diabetes exclusion from Bennett (2023) must do so manually post-hoc rather than have the system retrieve under that constraint. Of the four failure modes, this is the one the pipeline addresses least directly.

## Failure mode 4: Codes that refer to both relevant and irrelevant cases

Bennett's example: "sore throat" indicates Group A Streptococcus but also many other conditions — improving sensitivity costs specificity.

**How the pipeline addresses this.** The per-code rationale captures, in one sentence, why the model judged the code relevant; this is visible to the reviewer alongside source provenance. Codes the model flags `uncertain` are routed for explicit reviewer adjudication rather than being assigned a default.

**Residual risk and gaps.** There is no sensitivity-vs-specificity preference toggle: the pipeline returns what the model scores, and the reviewer cannot ask the system for a "high-sensitivity superset" or a "high-specificity consensus" variant of the same query. The trade-off must be applied by the reviewer at the review stage and is not captured as a structured pipeline parameter.

## Limitations beyond the Bennett framework

- Evaluation reference sets are derived from existing OpenCodelists curations, but the mapping between each reference codelist and the corresponding test query was interpreted by a single non-clinician evaluator; no independent peer validation of that selection has been performed. The F1 numbers in [EVALUATION.md](./EVALUATION.md) should be read as pilot-tier signal on n = 15 codelists rather than a peer-reviewed methods evaluation.
- Sample composition is skewed toward `nhsd-primary-care-domain-refsets` (12 of 15 codelists). The QOF retriever is built from the same NHS QOF Business Rules from which those refsets are derived, so the sample is favourable to QOF retrieval and may overstate QOF's marginal contribution at scale. Full caveat in [EVALUATION.md → Methodology → Sample selection](./EVALUATION.md#sample-selection).
- Recall is the system's primary current weakness. Mean recall across the 15 reference codelists is 0.50; the LLM scoring step trades recall for precision (precision 0.62 → 0.86; recall 0.62 → 0.50). The recall ceiling is set by the four retrievers' joint coverage on each query and is decomposed by retriever in [EVALUATION.md §Per-retriever ablation](./EVALUATION.md#per-retriever-ablation). Improving recall is the next research direction (retrieval reranking, query reformulation, additional retrievers).
- Codelists are point-in-time. SNOMED releases, QOF rule changes, and OpenCodelists edits do not trigger automatic re-validation of previously approved codelists.
- No multi-condition cohort composition (for example, "diabetes AND hypertension AND age > 65" as a structured query).
- Authentication is a demo mechanism; NHS OAuth/SAML and NHS Identity integration are not yet implemented.
- No clinical safety case has been written. The system is a research and development prototype.
- FAIR-compliance of generated codelists has not been formally evaluated against the criteria set out in Williams et al. (2019).
- Cost and latency at scale (large query batches, multi-tenant load) have not been characterised. Per-query latency is sensitive to model availability, candidate volume, and source-API response time; the README's "tens of seconds" framing is representative of the test conditions used during development and is not a guaranteed bound. Per-query cost is recorded at request time but has not been benchmarked against a fixed test set, so headline cost-reduction figures are not currently claimed.

## What this document does NOT claim

- This tool does not replace clinical review. Every codelist requires human approval before use.
- This tool is not certified for clinical decision-making and has not been assessed under DCB0129 or DCB0160.
- This tool's outputs are not suitable for direct patient-care use; they are intended as input to a downstream study or service that itself meets the relevant clinical safety standards.

## Citation

Wood, C., & Higgins, R. (2023, 27 September). *What Are Codelists and How Are They Constructed?* Bennett Institute for Applied Data Science, University of Oxford. https://www.bennett.ox.ac.uk/blog/2023/09/what-are-codelists-and-how-are-they-constructed/
