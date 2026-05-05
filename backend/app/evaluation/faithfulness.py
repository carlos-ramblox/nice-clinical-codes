"""Offline RAGAS-style faithfulness metric (T11).

For each (codelist x scored code) pair in the 15-codelist benchmark, asks
Claude Haiku 4.5 (LLM-as-judge) whether the per-code rationale produced
by the live scoring step is grounded in the code term and the queried
condition. Persists every verdict and aggregate groundedness rates to
``data/test_sets/benchmark_2026_04/_faithfulness.json``.

Why offline-only
----------------
Running this judge inside ``/api/search`` would double the per-request
LLM cost and add latency for no user-facing gain. The check exists to
characterise rationale quality across the benchmark, not to gate the
live response. The live path remains untouched.

Why hand-rolled, not the RAGAS library
--------------------------------------
RAGAS implements a two-step claim-decomposition + entailment pipeline
designed for free-form RAG answers. Our rationales are one-sentence
verdicts on a single (term, query) pair, so the claim-decomposition
step is structurally redundant. A direct three-way verdict prompt
(grounded / partial / unfounded) is cheaper, simpler, and produces a
verdict we can read alongside the existing P/R/F1 numbers without
plumbing a second eval framework into the codebase.

Resumability
------------
Each run rewrites ``_faithfulness.json`` from scratch BUT pre-loads any
previously persisted verdicts and skips codes whose verdict is already
on disk. So an aborted run resumes by re-invoking the same command -
useful given the wall-clock is ~1300 calls.

Usage::

    python -m app.evaluation.faithfulness
    python -m app.evaluation.faithfulness --codelists hiv,heart_failure
    python -m app.evaluation.faithfulness --concurrency 10 --max-pairs 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Literal

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from app.config import ANTHROPIC_API_KEY, LLM_SCORING_MODEL

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "data" / "test_sets" / "benchmark_2026_04"
SELECTION = ROOT / "data" / "raw" / "opencodelists" / "selection.json"
OUT_PATH = BENCH / "_faithfulness.json"

# Verbatim prompt, also cited in EVALUATION.md - keep in sync.
PROMPT_TEMPLATE = """Given:
 - Query Q: {query}
 - Code term T: {term}
 - Rationale R: {rationale}
Does R make a claim that is grounded in T and the queried condition Q?
Answer one of: 'grounded', 'partial', 'unfounded'.
One-sentence reason."""

Verdict = Literal["grounded", "partial", "unfounded"]


class FaithfulnessVerdict(BaseModel):
    verdict: Verdict = Field(description="One of grounded / partial / unfounded")
    reason: str = Field(description="One-sentence justification")


def _build_pair_key(short: str, code: str) -> str:
    return f"{short}::{code}"


async def _judge_one(
    structured_llm,
    sem: asyncio.Semaphore,
    short: str,
    query: str,
    code: dict,
) -> dict:
    """Judge a single (codelist, code) pair. Returns a record suitable
    for the per_code list. On LLM error, records verdict='error' so the
    aggregate honestly reflects the partial result rather than silently
    counting the failure as 'grounded'."""
    term = code.get("term", "")
    rationale = code.get("rationale", "")
    prompt = PROMPT_TEMPLATE.format(query=query, term=term, rationale=rationale)
    async with sem:
        try:
            result = await structured_llm.ainvoke([{"role": "user", "content": prompt}])
            verdict = result.verdict
            reason = result.reason
        except Exception as exc:
            logger.warning("[%s %s] judge failed: %s", short, code.get("code"), exc)
            verdict = "error"
            reason = f"judge error: {exc}"
    return {
        "short": short,
        "code": code.get("code"),
        "term": term,
        "decision": code.get("decision"),
        "rationale_pipeline": rationale,
        "verdict": verdict,
        "reason": reason,
    }


def _load_existing() -> dict[str, dict]:
    """Load prior verdicts keyed by (short, code) for resumability."""
    if not OUT_PATH.exists():
        return {}
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        logger.warning("Could not parse existing %s, starting fresh: %s", OUT_PATH.name, exc)
        return {}
    out: dict[str, dict] = {}
    for rec in payload.get("per_code", []):
        if rec.get("verdict") in ("grounded", "partial", "unfounded"):
            out[_build_pair_key(rec["short"], rec["code"])] = rec
    return out


def _aggregate(per_code: list[dict]) -> tuple[dict, dict]:
    """Return (per_codelist, overall) aggregate dicts."""
    by_list: dict[str, dict[str, int]] = {}
    overall: dict[str, int] = {"grounded": 0, "partial": 0, "unfounded": 0, "error": 0}
    for rec in per_code:
        bucket = by_list.setdefault(rec["short"], {"grounded": 0, "partial": 0, "unfounded": 0, "error": 0, "n": 0})
        v = rec.get("verdict", "error")
        if v not in bucket:
            v = "error"
        bucket[v] += 1
        bucket["n"] += 1
        overall[v] = overall.get(v, 0) + 1

    def _rate(d: dict[str, int], key: str, denom_key: str = "n_judged") -> float:
        denom = d.get(denom_key, 0)
        return round(d[key] / denom, 4) if denom else 0.0

    per_codelist: dict[str, dict] = {}
    for short, d in by_list.items():
        n_judged = d["grounded"] + d["partial"] + d["unfounded"]
        d["n_judged"] = n_judged
        per_codelist[short] = {
            "n": d["n"],
            "n_judged": n_judged,
            "grounded": d["grounded"],
            "partial": d["partial"],
            "unfounded": d["unfounded"],
            "error": d["error"],
            "groundedness_rate": _rate(d, "grounded"),
            "grounded_or_partial_rate": round((d["grounded"] + d["partial"]) / n_judged, 4) if n_judged else 0.0,
        }

    n_judged = overall["grounded"] + overall["partial"] + overall["unfounded"]
    overall_out = {
        "n": sum(d["n"] for d in by_list.values()),
        "n_judged": n_judged,
        "grounded": overall["grounded"],
        "partial": overall["partial"],
        "unfounded": overall["unfounded"],
        "error": overall["error"],
        "groundedness_rate": round(overall["grounded"] / n_judged, 4) if n_judged else 0.0,
        "grounded_or_partial_rate": round(
            (overall["grounded"] + overall["partial"]) / n_judged, 4
        ) if n_judged else 0.0,
    }
    return per_codelist, overall_out


def _write_payload(per_code: list[dict]) -> None:
    per_codelist, overall = _aggregate(per_code)
    payload = {
        "model": LLM_SCORING_MODEL,
        "prompt_template": PROMPT_TEMPLATE,
        "n_codelists": len({rec["short"] for rec in per_code}),
        "n_pairs": len(per_code),
        "overall": overall,
        "per_codelist": per_codelist,
        "per_code": per_code,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_pairs(codelists: list[str] | None) -> list[tuple[str, str, dict]]:
    """Build the (short, query, scored_code) work list from the persisted
    .result_postfix.json files."""
    with open(SELECTION, encoding="utf-8") as f:
        selection = json.load(f)
    if codelists:
        wanted = set(codelists)
        selection = [s for s in selection if s["short"] in wanted]
        missing = wanted - {s["short"] for s in selection}
        if missing:
            raise SystemExit(f"Unknown codelist short(s): {sorted(missing)}")

    pairs: list[tuple[str, str, dict]] = []
    for s in selection:
        short = s["short"]
        result_path = BENCH / f"{short}.result_postfix.json"
        if not result_path.exists():
            logger.warning("No result file for %s, skipping", short)
            continue
        with open(result_path, encoding="utf-8") as f:
            result = json.load(f)
        query = result.get("query", "")
        scored = result.get("scored_codes", [])
        for code in scored:
            pairs.append((short, query, code))
    return pairs


async def run(
    codelists: list[str] | None = None,
    concurrency: int = 10,
    max_pairs: int | None = None,
) -> dict:
    if not ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    pairs = _load_pairs(codelists)
    if max_pairs is not None:
        pairs = pairs[:max_pairs]

    existing = _load_existing()
    todo = [p for p in pairs if _build_pair_key(p[0], p[2].get("code", "")) not in existing]
    logger.info(
        "Faithfulness sweep: %d pairs total, %d already judged, %d to run (concurrency=%d)",
        len(pairs), len(existing), len(todo), concurrency,
    )
    if not todo:
        logger.info("Nothing to do; rewriting aggregates from existing verdicts.")
        per_code = list(existing.values())
        _write_payload(per_code)
        return {"per_code": per_code}

    llm = ChatAnthropic(
        model=LLM_SCORING_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=256,
        temperature=0,
    )
    structured_llm = llm.with_structured_output(FaithfulnessVerdict)
    sem = asyncio.Semaphore(concurrency)

    t0 = time.perf_counter()
    tasks = [_judge_one(structured_llm, sem, short, query, code) for (short, query, code) in todo]

    # Persist incrementally every PERSIST_EVERY completions so an aborted
    # sweep loses at most that many verdicts.
    per_code = list(existing.values())
    PERSIST_EVERY = 50
    completed = 0
    for fut in asyncio.as_completed(tasks):
        rec = await fut
        per_code.append(rec)
        existing[_build_pair_key(rec["short"], rec["code"])] = rec
        completed += 1
        if completed % PERSIST_EVERY == 0:
            _write_payload(per_code)
            logger.info("  ... %d / %d done (%.1fs elapsed)", completed, len(todo), time.perf_counter() - t0)

    _write_payload(per_code)
    elapsed = time.perf_counter() - t0
    per_codelist, overall = _aggregate(per_code)
    logger.info(
        "Sweep complete in %.1fs. Overall: %d grounded / %d partial / %d unfounded / %d error "
        "(groundedness rate %.3f)",
        elapsed,
        overall["grounded"], overall["partial"], overall["unfounded"], overall["error"],
        overall["groundedness_rate"],
    )
    return {"per_code": per_code, "per_codelist": per_codelist, "overall": overall}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codelists", type=str, default=None,
                        help="Comma-separated list of codelist short names (default: all 15)")
    parser.add_argument("--concurrency", type=int, default=10,
                        help="Max concurrent LLM judge calls (default: 10)")
    parser.add_argument("--max-pairs", type=int, default=None,
                        help="Cap on total pairs judged this run (smoke-test aid)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    codelists = [c.strip() for c in args.codelists.split(",")] if args.codelists else None
    asyncio.run(run(codelists=codelists, concurrency=args.concurrency, max_pairs=args.max_pairs))


if __name__ == "__main__":
    main()
