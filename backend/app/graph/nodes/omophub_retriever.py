import datetime
import logging

import pandas as pd
from omophub import OMOPHub

from app.config import (
    OMOPHUB_API_KEY,
    OMOPHUB_PAGE_SIZE,
    OMOPHUB_VOCABULARIES,
)

logger = logging.getLogger(__name__)


# Clinical-prefix variants tried in addition to the raw query string.
# Motivating case: OMOPHub indexes "Acute myocardial infarction" but does
# not return ICD-10 I21/I22 codes for the bare term "Myocardial
# infarction". Issuing both forms unblocks ICD-10 retrieval for those
# codes without changing behaviour for queries the bare form already
# resolves.
_CLINICAL_PREFIXES: tuple[str, ...] = ("Acute", "Chronic")


def query_variants(search_term: str) -> list[str]:
    """Generate the multi-query variant list for OMOPHub.

    Always includes the raw term. For each prefix in
    :data:`_CLINICAL_PREFIXES`, appends a prefixed form unless the raw
    term already starts with that prefix (case-insensitive) — which
    avoids "Acute Acute myocardial infarction" duplication.

    Heuristic only — no semantic understanding of the term. The intent
    is to surface ICD-10 codes that OMOPHub indexes under a clinical
    qualifier; non-clinical queries simply pay the cost of two extra
    requests with no harm.
    """
    if not search_term or not search_term.strip():
        return []
    raw = search_term.strip()
    raw_lower = raw.lower()
    variants = [raw]
    for prefix in _CLINICAL_PREFIXES:
        if raw_lower.startswith(prefix.lower() + " "):
            continue
        if raw_lower == prefix.lower():
            continue
        variants.append(f"{prefix} {raw}")
    return variants


def query_vocabulary(
    client: OMOPHub,
    search_term: str,
    vocab_id: str,
    page_size: int = 20,
    domain_id: str | None = None,
) -> list[dict]:
    """Query a single vocabulary and return annotated result dicts."""
    kwargs = dict(vocabulary_ids=[vocab_id], page_size=page_size)
    if domain_id:
        kwargs["domain_ids"] = [domain_id]

    try:
        response = client.search.basic(search_term, **kwargs)
        raw = response if isinstance(response, list) else response.get("data", response)
    except Exception as exc:
        logger.warning("OMOPHub query failed for %s: %s", vocab_id, exc)
        return []

    query_ts = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds") + "Z"
    annotated = []
    for record in raw:
        row = dict(record)
        row["_query_term"] = search_term
        row["_query_vocabulary"] = vocab_id
        row["_vocabulary_label"] = OMOPHUB_VOCABULARIES.get(vocab_id, vocab_id)
        row["_query_domain"] = domain_id or "all"
        row["_queried_at_utc"] = query_ts
        row["_source"] = "OMOPHub"
        annotated.append(row)

    return annotated


def search_omophub(
    search_term: str,
    vocabularies: dict[str, str] | None = None,
    page_size: int | None = None,
    domain_id: str | None = None,
) -> pd.DataFrame:
    """
    Search OMOPHub for clinical codes across specified vocabularies,
    issuing the raw term plus heuristic clinical-prefix variants and
    deduplicating across them.

    Result deduplication keys on (concept_code, vocabulary_id) so the
    same code surfaced under two distinct vocabularies still yields
    two rows. Total candidate count is capped at
    ``page_size * len(variants) * len(vocabs)`` — the theoretical
    no-overlap upper bound — so multi-vocab queries don't get
    truncated below the merger's downstream cap. The cap therefore
    depends on the caller's ``vocabularies`` argument: the
    ``omophub_retriever_node`` graph node passes the parsed
    condition's coding_systems (typically 2 vocabs → cap 120 with
    default page_size=20 and 3 variants); calling
    ``search_omophub`` directly with ``vocabularies=None`` falls
    through to the full ``OMOPHUB_VOCABULARIES`` (3 vocabs → cap 180).
    A single-vocabulary cue (e.g. ICD10-only) caps at 60.
    """
    if not OMOPHUB_API_KEY:
        raise ValueError("OMOPHUB_API_KEY not set")

    vocabs = vocabularies or OMOPHUB_VOCABULARIES
    ps = page_size or OMOPHUB_PAGE_SIZE

    variants = query_variants(search_term)
    if not variants:
        return pd.DataFrame()

    cap = ps * len(variants) * max(len(vocabs), 1)

    client = OMOPHub(api_key=OMOPHUB_API_KEY)
    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []

    for variant in variants:
        if len(merged) >= cap:
            break
        for vocab_id in vocabs:
            if len(merged) >= cap:
                break
            rows = query_vocabulary(client, variant, vocab_id, ps, domain_id)
            for row in rows:
                code = str(row.get("concept_code", row.get("concept_id", "")))
                if not code:
                    continue
                key = (code, vocab_id)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(row)
                if len(merged) >= cap:
                    break

    if not merged:
        return pd.DataFrame()

    logger.info(
        "OMOPHub multi-query: %d variants × %d vocabs → %d unique rows (cap %d) for %r",
        len(variants), len(vocabs), len(merged), cap, search_term,
    )
    return pd.DataFrame(merged)


def _coerce_concept_id(value) -> int | None:
    """Coerce an OMOPHub ``concept_id`` cell to int.

    Pandas may surface the value as float (NaN promotes the dtype)
    or str depending on the row; None / NaN / unparseable -> None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        return int(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def omophub_to_retrieved_codes(df: pd.DataFrame) -> list[dict]:
    """Convert OMOPHub DataFrame rows to RetrievedCode dicts for the pipeline."""
    return [
        {
            "code": str(row.get("concept_code", row.get("concept_id", ""))),
            "term": row.get("concept_name", ""),
            "vocabulary": row.get("_vocabulary_label", row.get("_query_vocabulary", "")),
            "source": "OMOPHub",
            "domain": row.get("domain_id", "Unknown"),
            "similarity_score": None,
            "usage_frequency": None,
            "concept_id": _coerce_concept_id(row.get("concept_id")),
        }
        for row in df.to_dict(orient="records")
    ]
