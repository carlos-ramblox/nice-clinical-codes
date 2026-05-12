import csv
import logging
from pathlib import Path

from app.db.code_store import get_concept_id_for, insert_codes, search_by_condition
from app.db.vector_store import add_codes as add_to_chroma
from app.services.dmd_classification import VOCABULARY, infer_dmd_level

logger = logging.getLogger(__name__)

SOURCE_TAG = "OpenCodelists (dm+d)"

__all__ = ["SOURCE_TAG", "ingest_dmd_csv", "ingest_dmd_dir", "retrieve_from_dmd"]


def ingest_dmd_csv(csv_path: str | Path, codelist_name: str = "") -> int:
    path = Path(csv_path)
    try:
        f = open(path, encoding="utf-8")
    except FileNotFoundError:
        logger.warning("dm+d CSV not found: %s", csv_path)
        return 0
    except OSError as exc:
        logger.warning("dm+d CSV could not be opened: %s -- %s", csv_path, exc)
        return 0

    with f:
        reader = csv.DictReader(f)
        codes = []
        for row in reader:
            code = row.get("code") or row.get("id") or ""
            term = row.get("term") or row.get("description") or ""
            if code and term:
                codes.append({
                    "code": str(code),
                    "term": term,
                    "vocabulary": VOCABULARY,
                    "source": SOURCE_TAG,
                    "domain": "Drug",
                    "cluster_id": path.stem,
                    "cluster_description": codelist_name or path.stem,
                    "active": 1,
                })

    if not codes:
        return 0

    count = insert_codes(codes)
    add_to_chroma([
        {"code": c["code"], "term": c["term"], "vocabulary": c["vocabulary"],
         "source": c["source"], "domain": c["domain"]}
        for c in codes
    ])
    logger.info("Ingested %d dm+d codes from %s", count, path.name)
    return count


def ingest_dmd_dir(directory: str | Path) -> int:
    dirpath = Path(directory)
    if not dirpath.exists():
        logger.warning("dm+d directory not found: %s", directory)
        return 0
    total = 0
    for csv_file in sorted(dirpath.glob("*.csv")):
        total += ingest_dmd_csv(csv_file, codelist_name=csv_file.stem)
    logger.info("Ingested %d total dm+d codes from %s", total, dirpath)
    return total


def retrieve_from_dmd(state: dict) -> dict:
    """Fan-out dm+d retriever; FR-008 gates on ``domain == "Drug"``."""
    conditions = state.get("parsed_conditions", [])
    drug_conditions = [c for c in conditions if c.get("domain") == "Drug" and c.get("name")]
    if not drug_conditions:
        return {"retrieved_codes": [], "sources_queried": []}

    all_codes = []
    for condition in drug_conditions:
        name = condition["name"]
        rows = search_by_condition(name, vocabulary=VOCABULARY)
        # Filter explicitly to OpenCodelists source until a TRUD ingest lands.
        dmd_rows = [r for r in rows if r.get("source") == SOURCE_TAG]
        for r in dmd_rows:
            all_codes.append({
                "code": r["code"],
                "term": r["term"],
                "vocabulary": r["vocabulary"],
                "source": r["source"],
                "domain": r["domain"],
                "similarity_score": None,
                "usage_frequency": None,
                "concept_id": get_concept_id_for(r["vocabulary"], r["code"]),
                "dmd_level": infer_dmd_level(r["term"]),
            })
        logger.info("dm+d: '%s' returned %d codes", name, len(dmd_rows))

    return {"retrieved_codes": all_codes, "sources_queried": [SOURCE_TAG]}
