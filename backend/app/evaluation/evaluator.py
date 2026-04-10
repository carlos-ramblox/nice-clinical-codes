"""
Evaluation module: compares pipeline output against a gold-standard
reference codelist. Adapted from Anna's Colab notebook.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class EvalMetrics:
    codelist_name: str
    n_ref_codes: int
    n_output_codes: int
    recall: float
    precision: float
    f1: float
    tp_count: int
    fp_count: int
    fn_count: int
    tp_codes: list = field(default_factory=list)
    fp_codes: list = field(default_factory=list)
    fn_codes: list = field(default_factory=list)


def evaluate_codelist(
    ref_codes: list[dict],
    output_codes: list[dict],
    codelist_name: str = "pipeline",
) -> EvalMetrics:
    """
    Compare output codes against reference (gold standard) codes.

    ref_codes: list of {"code": "G93.2", "term": "Intracranial hypertension"}
    output_codes: list of {"code": "...", "term": "...", ...}

    Returns EvalMetrics with recall, precision, F1, TP/FP/FN lists.
    """
    ref_set = {c["code"].strip().rstrip(".") for c in ref_codes}
    out_set = {c["code"].strip().rstrip(".") for c in output_codes}

    ref_lookup = {c["code"].strip().rstrip("."): c.get("term", "") for c in ref_codes}
    out_lookup = {c["code"].strip().rstrip("."): c.get("term", "") for c in output_codes}

    tp = ref_set & out_set
    fp = out_set - ref_set
    fn = ref_set - out_set

    recall = len(tp) / len(ref_set) if ref_set else 0.0
    precision = len(tp) / len(out_set) if out_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return EvalMetrics(
        codelist_name=codelist_name,
        n_ref_codes=len(ref_set),
        n_output_codes=len(out_set),
        recall=round(recall, 4),
        precision=round(precision, 4),
        f1=round(f1, 4),
        tp_count=len(tp),
        fp_count=len(fp),
        fn_count=len(fn),
        tp_codes=[{"code": c, "term": ref_lookup.get(c, out_lookup.get(c, ""))} for c in sorted(tp)],
        fp_codes=[{"code": c, "term": out_lookup.get(c, "")} for c in sorted(fp)],
        fn_codes=[{"code": c, "term": ref_lookup.get(c, "")} for c in sorted(fn)],
    )


def run_evaluation(
    test_set: list[dict],
    pipeline_results: dict,
) -> dict:
    """
    Run full evaluation given a test set JSON and pipeline results.

    test_set: Anna's format — list of entries with Codelist, Codelist_terms,
              Research_question, Codelist_vocabulary
    pipeline_results: the dict returned by /api/search (has "results" key)

    Returns a dict with stage-level metrics + overall summary.
    """
    # Build reference codes from test set
    ref_codes = []
    for entry in test_set:
        codes_raw = entry.get("Codelist", "")
        terms_raw = entry.get("Codelist_terms", "")

        codes = [c.strip().rstrip(".") for c in str(codes_raw).split(";") if c.strip()]
        terms = [t.strip() for t in str(terms_raw).split(";") if t.strip()]

        # zip codes with terms, pad terms with "" if fewer
        for i, code in enumerate(codes):
            term = terms[i] if i < len(terms) else ""
            ref_codes.append({"code": code, "term": term})

    if not ref_codes:
        return {"error": "No reference codes found in test set"}

    # Extract pipeline output at scored stage (final decisions)
    scored = pipeline_results.get("results", [])
    included = [c for c in scored if c.get("decision") == "include"]
    excluded = [c for c in scored if c.get("decision") == "exclude"]
    uncertain = [c for c in scored if c.get("decision") == "uncertain"]

    # Evaluate different views
    results = {
        "reference_count": len(ref_codes),
        "query": test_set[0].get("Research_question", ""),
        "vocabulary": test_set[0].get("Codelist_vocabulary", ""),
        "stages": {},
    }

    # All scored (full pipeline output)
    m_all = evaluate_codelist(ref_codes, scored, "all_scored")
    results["stages"]["all_scored"] = _metrics_to_dict(m_all)

    # Included only
    m_inc = evaluate_codelist(ref_codes, included, "included_only")
    results["stages"]["included_only"] = _metrics_to_dict(m_inc)

    # Included + uncertain (if user reviews uncertain)
    m_inc_unc = evaluate_codelist(ref_codes, included + uncertain, "included_plus_uncertain")
    results["stages"]["included_plus_uncertain"] = _metrics_to_dict(m_inc_unc)

    # Check excluded codes that were in reference (false exclusions)
    ref_set = {c["code"].strip().rstrip(".") for c in ref_codes}
    false_exclusions = [c for c in excluded if c.get("code", "").strip().rstrip(".") in ref_set]
    results["false_exclusions"] = {
        "count": len(false_exclusions),
        "codes": [{"code": c["code"], "term": c["term"], "rationale": c.get("rationale", "")} for c in false_exclusions],
    }

    # Check uncertain codes that were in reference
    uncertain_in_ref = [c for c in uncertain if c.get("code", "").strip().rstrip(".") in ref_set]
    results["uncertain_in_reference"] = {
        "count": len(uncertain_in_ref),
        "codes": [{"code": c["code"], "term": c["term"], "rationale": c.get("rationale", "")} for c in uncertain_in_ref],
    }

    # Summary sentence (like Anna's notebook)
    m = results["stages"]["included_only"]
    results["summary"] = (
        f"Recall: {m['recall']:.1%} ({m['tp_count']} of {m['n_ref_codes']} reference codes retrieved). "
        f"Precision: {m['precision']:.1%}. F1: {m['f1']:.1%}. "
        f"{results['false_exclusions']['count']} reference codes were incorrectly excluded."
    )

    logger.info("Evaluation: %s", results["summary"])
    return results


def _metrics_to_dict(m: EvalMetrics) -> dict:
    return {
        "codelist_name": m.codelist_name,
        "n_ref_codes": m.n_ref_codes,
        "n_output_codes": m.n_output_codes,
        "recall": m.recall,
        "precision": m.precision,
        "f1": m.f1,
        "tp_count": m.tp_count,
        "fp_count": m.fp_count,
        "fn_count": m.fn_count,
        "tp_codes": m.tp_codes,
        "fp_codes": m.fp_codes,
        "fn_codes": m.fn_codes,
    }
