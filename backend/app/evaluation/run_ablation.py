"""Per-retriever ablation runner.

Decomposes the pipeline by retriever and by LLM-scoring step against
the same 15 reference codelists used in the v2 benchmark
(``data/raw/opencodelists/selection.json``).

For each codelist this script:

* Parses the query once (one Claude Sonnet 4 call) and obtains the
  ``parsed_conditions`` the retriever nodes consume.
* Runs each of the five retriever nodes (``omophub``, ``qof``,
  ``chroma``, ``opencodelists``, ``hdruk``) in isolation against those
  parsed conditions and evaluates the raw, un-merged, un-enriched,
  un-scored retriever output against the reference list. This is
  configs 1--5.
* Reuses the existing ``result_postfix.json`` files (which were
  produced under the same benchmark conditions) for configs 6
  (``stages.retrieved_raw``), 7 (``stages.merged_enriched``) and 8
  (``stages.included_only``), and the corresponding
  ``result_coldstart.json`` files for config 9.

Why this isolated path rather than re-invoking the full graph with
``disabled_retrievers={a, b, c}`` per ablation:

* Cost. The full graph runs UMLS enrichment and Claude Haiku 4.5
  per-code scoring on every request. Configs 1--7 want the
  *retriever's* output, not the model's verdict on it, so paying for
  scoring would be a waste of budget.
* Methodological cleanliness. Configs 1--5 are defined in
  EVALUATION.md as **raw retriever output without LLM scoring** so
  the contribution of each retriever can be read directly. Calling
  the retriever node functions in-process gives that exactly --
  identical code path the production graph uses, no merger filter,
  no UMLS expansion.

Outputs
-------
* ``data/test_sets/benchmark_2026_04/_ablation.json`` -- per-codelist
  rows for each of the eight configs plus aggregate mean / median /
  BCa-95 CI per (P, R, F1).

Usage::

    python -m app.evaluation.run_ablation
"""
from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path
from typing import Callable

from app.evaluation.benchmark_aggregate import bootstrap_ci, normalize_code
from app.graph.nodes.chroma_retriever import retrieve_from_chromadb
from app.graph.nodes.hdruk_retriever import retrieve_from_hdruk
from app.graph.nodes.omophub_retriever import omophub_to_retrieved_codes, search_omophub
from app.graph.nodes.opencodelists_retriever import retrieve_from_opencodelists
from app.graph.nodes.qof_retriever import retrieve_from_qof
from app.graph.nodes.query_parser import parse_query
from app.graph.nodes.result_merger import merge_and_dedup
from app.config import OMOPHUB_VOCABULARIES

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "data" / "test_sets" / "benchmark_2026_04"
SELECTION = ROOT / "data" / "raw" / "opencodelists" / "selection.json"
OUT = BENCH / "_ablation.json"

# Configs 1-4: each retriever in isolation. The OMOPHub retriever has a
# slightly larger surface than its node-level wrapper (it loops over
# parsed conditions) so we re-implement that wrapper inline -- the same
# logic ``omophub_retriever_node`` uses in graph.py, just isolated from
# the LangGraph state plumbing.

def _omophub_in_isolation(parsed_conditions: list[dict]) -> list[dict]:
    """Replicate ``omophub_retriever_node`` outside the graph."""
    all_codes: list[dict] = []
    for condition in parsed_conditions:
        name = condition.get("name", "")
        if not name:
            continue
        systems = condition.get("coding_systems", ["SNOMED", "ICD10"])
        vocabs = {k: OMOPHUB_VOCABULARIES[k] for k in systems if k in OMOPHUB_VOCABULARIES}
        df = search_omophub(name, vocabularies=vocabs, page_size=20)
        all_codes.extend(omophub_to_retrieved_codes(df))
    return all_codes


def _node_in_isolation(node_fn: Callable, parsed_conditions: list[dict]) -> list[dict]:
    """Call a retriever node function with a minimal state dict and
    return its ``retrieved_codes`` payload."""
    state = {"parsed_conditions": parsed_conditions}
    out = node_fn(state)
    return out.get("retrieved_codes", []) or []


_RETRIEVER_RUNNERS: dict[str, Callable[[list[dict]], list[dict]]] = {
    "omophub":       _omophub_in_isolation,
    "qof":           lambda pc: _node_in_isolation(retrieve_from_qof, pc),
    "chroma":        lambda pc: _node_in_isolation(retrieve_from_chromadb, pc),
    "opencodelists": lambda pc: _node_in_isolation(retrieve_from_opencodelists, pc),
    "hdruk":         lambda pc: _node_in_isolation(retrieve_from_hdruk, pc),
}

# Display names for the table. The order here is the order they will
# appear in EVALUATION.md and in the chart (after F1-sort).
_CONFIG_LABELS: dict[str, str] = {
    "omophub_only":       "OMOPHub only",
    "qof_only":           "QOF only",
    "opencodelists_only": "OpenCodelists only",
    "chromadb_only":      "ChromaDB only",
    "hdruk_only":         "HDR UK only",
    "merger_raw":         "Merger (raw retrieval)",
    "merger_umls":        "Merger + UMLS (no LLM)",
    "full_pipeline":      "Full pipeline (default)",
    "cold_start":         "Cold-start (no OpenCodelists)",
}


def _ref_lookup(test_entry: dict) -> dict[str, tuple[str, str]]:
    """Build the normalized ``{code -> (raw, term)}`` lookup for a test
    entry. Mirrors the construction in ``benchmark_aggregate.evaluate_one``
    so the ablation aggregator and the headline aggregator agree on every
    metric."""
    codes_raw = str(test_entry.get("Codelist", "")).split(";")
    terms_raw = str(test_entry.get("Codelist_terms", "")).split(";")
    vocab = test_entry.get("Codelist_vocabulary", "")
    codes = [c.strip() for c in codes_raw if c.strip()]
    terms = [t.strip() for t in terms_raw if t.strip()]
    out: dict[str, tuple[str, str]] = {}
    for i, code in enumerate(codes):
        nc = normalize_code(code, vocab)
        term = terms[i] if i < len(terms) else ""
        out[nc] = (code, term)
    return out


def _set_metrics(ref_norm: set[str], out_norm: set[str]) -> dict:
    tp = ref_norm & out_norm
    fp = out_norm - ref_norm
    fn = ref_norm - out_norm
    p = len(tp) / (len(tp) + len(fp)) if (tp or fp) else 0.0
    r = len(tp) / (len(tp) + len(fn)) if (tp or fn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return {
        "precision": round(p, 4),
        "recall": round(r, 4),
        "f1": round(f1, 4),
        "tp": len(tp),
        "fp": len(fp),
        "fn": len(fn),
        "n_out": len(out_norm),
        "n_ref": len(ref_norm),
    }


def _evaluate_codes_against_ref(codes: list[dict], ref_lookup: dict[str, tuple[str, str]]) -> dict:
    """Apply the same dot-stripping rule the live evaluator uses, dedup
    via set, and return P/R/F1 against the reference."""
    out_norm = {normalize_code(c.get("code", ""), c.get("vocabulary", "")) for c in codes if c.get("code")}
    out_norm.discard("")
    return _set_metrics(set(ref_lookup), out_norm)


def _stage_metrics(stage: dict | None) -> dict | None:
    """Lift the relevant fields out of an evaluator ``stages.<name>``
    block so it lines up with ``_set_metrics``."""
    if not stage:
        return None
    return {
        "precision": stage.get("precision"),
        "recall": stage.get("recall"),
        "f1": stage.get("f1"),
        "tp": stage.get("tp_count"),
        "fp": stage.get("fp_count"),
        "fn": stage.get("fn_count"),
        "n_out": stage.get("n_output_codes"),
        "n_ref": stage.get("n_ref_codes"),
    }


def run() -> dict:
    with open(SELECTION, encoding="utf-8") as f:
        selection = json.load(f)

    rows_by_config: dict[str, list[dict]] = {k: [] for k in _CONFIG_LABELS}

    for s in selection:
        short = s["short"]
        with open(BENCH / f"{short}.json", encoding="utf-8") as f:
            test_set = json.load(f)
        entry = test_set[0]
        query = entry.get("Research_question", "")
        ref_lookup = _ref_lookup(entry)

        logger.info("=== %s :: %r (n_ref=%d) ===", short, query, len(ref_lookup))

        # Single Sonnet call shared across all four retrievers
        parsed = parse_query(query)
        parsed_conditions = parsed["conditions"]

        # Configs 1-5: per-retriever, raw output
        per_retriever_metrics: dict[str, dict] = {}
        retriever_to_config = {
            "omophub":       "omophub_only",
            "qof":           "qof_only",
            "chroma":        "chromadb_only",
            "opencodelists": "opencodelists_only",
            "hdruk":         "hdruk_only",
        }
        for retriever_name, runner in _RETRIEVER_RUNNERS.items():
            try:
                codes = runner(parsed_conditions)
            except Exception as exc:
                logger.warning("Retriever %s failed for %s: %s", retriever_name, short, exc)
                codes = []
            m = _evaluate_codes_against_ref(codes, ref_lookup)
            cfg = retriever_to_config[retriever_name]
            per_retriever_metrics[cfg] = m
            rows_by_config[cfg].append({
                "short": short, "vocabulary": entry.get("Codelist_vocabulary", ""),
                "area": s["area"], "n_ref": len(ref_lookup), **m,
            })
            logger.info(
                "  %s -> n_out=%d  P=%.3f  R=%.3f  F1=%.3f",
                cfg, m["n_out"], m["precision"], m["recall"], m["f1"],
            )

        # Config 6 (Merger raw): run the production merger directly on the
        # union of all five retrievers' raw output, with parsed_conditions
        # so the merger's vocabulary-constraint filter (Fix F) fires the
        # same way as in the live pipeline. UMLS and LLM scoring are
        # skipped. This is the cleanest "all retrievers, no UMLS, no LLM"
        # baseline -- post-merger but pre-enrichment.
        merger_input: list[dict] = []
        for runner in _RETRIEVER_RUNNERS.values():
            try:
                merger_input.extend(runner(parsed_conditions))
            except Exception:
                # Already reported above; tolerate so the merger still runs
                # over the retrievers that succeeded.
                pass
        merged = merge_and_dedup({
            "retrieved_codes": merger_input,
            "parsed_conditions": parsed_conditions,
        }).get("enriched_codes", []) or []
        m_merger = _evaluate_codes_against_ref(merged, ref_lookup)
        rows_by_config["merger_raw"].append({
            "short": short, "vocabulary": entry.get("Codelist_vocabulary", ""),
            "area": s["area"], "n_ref": len(ref_lookup), **m_merger,
        })
        logger.info(
            "  merger_raw -> n_out=%d  P=%.3f  R=%.3f  F1=%.3f",
            m_merger["n_out"], m_merger["precision"], m_merger["recall"], m_merger["f1"],
        )

        # Configs 6 and 7: read from the existing post-fix run. Same
        # evaluator, same dot-stripping, same reference set, so numbers
        # are directly comparable to the v2 headline.
        # Config 6 uses stages.merged_enriched (post-merger + UMLS),
        # config 7 uses stages.included_only (full pipeline).
        # Where a post-tune (Apr-29) result exists for a codelist (only
        # HIV, after the targeted prompt tweak), prefer that file so the
        # full-pipeline rows match the v2 Post-tune row in EVALUATION.md.
        def _pick(paths: list[Path]) -> Path | None:
            for p in paths:
                if p.exists():
                    return p
            return None

        post_path = _pick([
            BENCH / f"{short}.result_postfix_v2.json",
            BENCH / f"{short}.result_postfix.json",
        ])
        if post_path is None:
            logger.warning("No post-fix result file for %s", short)
        else:
            with open(post_path, encoding="utf-8") as f:
                post = json.load(f)
            for cfg, stage_name in [
                ("merger_umls",   "merged_enriched"),
                ("full_pipeline", "included_only"),
            ]:
                m = _stage_metrics(post.get("stages", {}).get(stage_name))
                if m is None:
                    logger.warning("Missing stage %s in %s", stage_name, post_path.name)
                    continue
                rows_by_config[cfg].append({
                    "short": short, "vocabulary": entry.get("Codelist_vocabulary", ""),
                    "area": s["area"], "n_ref": len(ref_lookup), **m,
                })

        # Config 8: cold-start full pipeline. Same v2-preference rule.
        cold_path = _pick([
            BENCH / f"{short}.result_coldstart_v2.json",
            BENCH / f"{short}.result_coldstart.json",
        ])
        if cold_path is None:
            logger.warning("No cold-start result file for %s", short)
        else:
            with open(cold_path, encoding="utf-8") as f:
                cold = json.load(f)
            m = _stage_metrics(cold.get("stages", {}).get("included_only"))
            if m is None:
                logger.warning("Missing stage included_only in %s", cold_path.name)
            else:
                rows_by_config["cold_start"].append({
                    "short": short, "vocabulary": entry.get("Codelist_vocabulary", ""),
                    "area": s["area"], "n_ref": len(ref_lookup), **m,
                })

    aggregate: dict[str, dict] = {}
    for cfg, rows in rows_by_config.items():
        if not rows:
            continue
        ps = [r["precision"] for r in rows]
        rs = [r["recall"] for r in rows]
        fs = [r["f1"] for r in rows]
        aggregate[cfg] = {
            "label": _CONFIG_LABELS[cfg],
            "n": len(rows),
            "precision_mean": round(statistics.mean(ps), 4),
            "recall_mean":    round(statistics.mean(rs), 4),
            "f1_mean":        round(statistics.mean(fs), 4),
            "f1_median":      round(statistics.median(fs), 4),
            "f1_ci95":        bootstrap_ci(fs),
        }

    payload = {
        "n_codelists": len(selection),
        "configs": aggregate,
        "per_codelist": rows_by_config,
        "notes": (
            "Configs 1-5 are raw retriever output (no merger filter, "
            "no UMLS, no LLM scoring). Config 6 (merger_raw) is "
            "produced by calling result_merger.merge_and_dedup directly "
            "on the union of the five retrievers' raw outputs, with "
            "parsed_conditions, so the merger's Fix-F vocabulary filter "
            "fires the same way as in the production graph -- post-merger "
            "but pre-UMLS, pre-LLM. Config 7 (merger_umls) is read from "
            "the existing post-fix result files' stages.merged_enriched "
            "(post-merger + UMLS, pre-LLM). Configs 8 and 9 are read from "
            "the same files' stages.included_only (post-fix default and "
            "post-fix cold-start). For the HIV codelist where a post-tune "
            "result exists (Apr-29), the v2 file is used in preference, "
            "matching the Post-tune row in EVALUATION.md."
        ),
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("wrote %s", OUT)

    # Concise stdout for the operator -- mirrors benchmark_aggregate.main().
    print()
    print(f"{'Configuration':<32s}  {'n':>2s}  {'P':>5s}  {'R':>5s}  {'F1':>5s}  {'medF1':>5s}  {'BCa95':>16s}")
    for cfg, agg in aggregate.items():
        ci_lo, ci_hi = agg["f1_ci95"]
        print(f"{agg['label']:<32s}  {agg['n']:>2d}  "
              f"{agg['precision_mean']:>.3f}  {agg['recall_mean']:>.3f}  "
              f"{agg['f1_mean']:>.3f}  {agg['f1_median']:>.3f}  "
              f"[{ci_lo:.3f}, {ci_hi:.3f}]")
    return payload


if __name__ == "__main__":
    run()
