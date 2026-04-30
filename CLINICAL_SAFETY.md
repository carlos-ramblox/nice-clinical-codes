# Clinical Safety

## Framework

DCB0129 (*Clinical Risk Management: its Application in the Manufacture of Health IT Systems*) and DCB0160 (*Clinical Risk Management: its Application in the Deployment and Use of Health IT Systems*) are information standards published by NHS Digital under section 250 of the Health and Social Care Act 2012. DCB0129 places obligations on manufacturers — most centrally, the appointment of a Clinical Safety Officer (CSO), the maintenance of a Hazard Log, and the production of a Clinical Safety Case Report. DCB0160 places parallel obligations on the organisations that deploy, use, maintain, and decommission Health IT Systems. NHS England's position is that NHS organisations should not procure a digital health technology without DCB0129 assurance, nor deploy one without DCB0160 assurance.

This document maps clinicalcodes.uk against those frameworks for transparency. It is not a claim of compliance. Oskrochi et al. (2025) surveyed 9,292 NHS digital health technology deployments and reported that only **17.3 %** (95 % CI 16.6 %–18.1 %) were fully assured against both DCB0129 and DCB0160, and **70.1 %** (95 % CI 69.1 %–71.1 %) had no documented assurance against either standard. Most NHS-deployed healthtech does not address these standards at all; this document records where clinicalcodes.uk sits relative to them, rather than asserting that it has met them.

## Status

clinicalcodes.uk is research and educational software. It is not certified for direct clinical decision-making, and its outputs are not suitable for direct patient-care use. Every codelist requires per-code review and explicit clinician approval before any onward use. The system is designed as upstream input to a downstream study or service that itself meets the relevant clinical safety standards; it is not itself such a service. The codelists it produces are research artefacts, not clinical instruments.

## Risk-mitigation patterns implemented

The following design choices address hazards that DCB0129 would require a manufacturer to consider. Each addresses a specific risk; collectively they do not constitute DCB0129 assurance.

- **Human-in-the-loop review gate.** Every codelist requires per-code clinician acceptance, rejection, or override before approval. No artefact leaves the system without explicit reviewer action (`backend/app/db/hitl_store.py`, `submit_review`). This addresses the DCB0129 expectation that automated outputs not be relied upon without clinician adjudication.
- **Deterministic content-hash on approved artefacts.** On approval, a SHA-256 digest is computed over the final human decisions in deterministic order and stored against the codelist record (`backend/app/db/hitl_store.py:364`). Any post-approval edit to the decisions changes the digest, so the hash detects accidental edits and provides a deterministic content-fingerprint for downstream reproduction. The hash is stored in the same SQLite database as the data it covers, so it does not defend against an actor with write access to that database; it is a content-integrity check, not an adversarial-tamper guarantee.
- **Full audit log.** Inputs, AI decisions, reviewer overrides with written rationales, user identities, and timestamps are written to a dedicated `audit_log` table (`backend/app/db/hitl_store.py`, schema at lines 89–102; population in `submit_review`). Each override event is logged individually before the terminal approval event, so the chain of decisions is reconstructible.
- **Per-code provenance.** For each candidate, the retriever(s) that surfaced it, the source count, and the LLM rationale are persisted to the decision row and visible to the reviewer. The reviewer sees *how* a code entered the candidate set, not only that the model recommended it.
- **Vocabulary constraint propagation.** When a clinician's query specifies a coding system (for example *ICD-10*), the constraint is parsed, propagated through the merger before the candidate cap, and applied as a final output filter; only codes in the requested vocabulary are returned. This addresses a class of cross-vocabulary leakage hazard documented in `EVALUATION.md` §4 case 1.
- **Deterministic LLM scoring.** Both the query parser (Claude Sonnet 4) and the per-code scorer (Claude Haiku 4.5) run at `temperature=0`, and candidates are sorted by `(vocabulary, code)` before batching, so identical inputs yield identical prompt batches across runs (see README *Reproducibility* section). This mitigates — though does not eliminate — the run-to-run variance that would otherwise frustrate reproducible hazard analysis.
- **Override rationale capture.** Every reviewer decision that disagrees with the AI score is required to carry a written rationale; the rationale is stored on the decision row and replicated into the audit log. Disagreement between the model and the clinician is therefore recorded as data, not silently resolved.

## What this document does NOT claim

- It does **not** claim DCB0129 or DCB0160 assurance.
- It does **not** replace a Clinical Safety Officer review.
- It does **not** replace a written clinical safety case; no Hazard Log or Clinical Safety Case Report has been produced for this system.
- It does **not** meet MHRA software-as-a-medical-device requirements. The system has not been assessed under the relevant medical-device regulations.
- It is **not** suitable for direct patient-care use, and must not be deployed as such.

For completeness, three adjacent UK frameworks are noted but not mapped here. ISO 14971 governs medical-device risk management where a system is a medical device. The MHRA's software- and AI-as-a-medical-device guidance determines whether a tool falls within the medical-device definition. The Data Security and Protection Toolkit (DSPT, successor to the IG Toolkit) governs information governance for organisations handling NHS data. None of the three has been formally assessed for this system; full assessment would require a CSO-led safety case, an MHRA classification review, and (for any deploying organisation) a DSPT submission.

## Cross-references

- For full functional limitations, including failure modes the system does not yet mitigate: see [`LIMITATIONS.md`](./LIMITATIONS.md).
- For evaluation methodology, benchmark results, and per-codelist failure-case analysis: see [`EVALUATION.md`](./EVALUATION.md).

## Citation

Oskrochi, Y., Roy-Highley, E., Grimes, K., & Shah, S. (2025). *Digital Health Technology Compliance With Clinical Safety Standards In the National Health Service in England: National Cross-Sectional Study*. Journal of Medical Internet Research. <https://www.jmir.org/2025/1/e80076>

NHS Digital. *DCB0129: Clinical Risk Management — its Application in the Manufacture of Health IT Systems*. <https://digital.nhs.uk/data-and-information/information-standards/information-standards-and-data-collections-including-extractions/publications-and-notifications/standards-and-collections/dcb0129-clinical-risk-management-its-application-in-the-manufacture-of-health-it-systems>

NHS Digital. *DCB0160: Clinical Risk Management — its Application in the Deployment and Use of Health IT Systems*. <https://digital.nhs.uk/data-and-information/information-standards/information-standards-and-data-collections-including-extractions/publications-and-notifications/standards-and-collections/dcb0160-clinical-risk-management-its-application-in-the-deployment-and-use-of-health-it-systems>
