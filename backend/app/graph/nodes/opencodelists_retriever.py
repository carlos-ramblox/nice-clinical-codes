import re
import time
import logging
import csv
from io import StringIO
from pathlib import Path

import requests

from app.db.code_store import search_by_condition, insert_codes
from app.db.vector_store import add_codes as add_to_chroma

logger = logging.getLogger(__name__)

BASE_URL = "https://www.opencodelists.org"
REQUEST_GAP = 0.3
MAX_CODELISTS = 5
SOURCE_TAG = "OpenCodelists (Bennett Institute)"


# --- Pre-downloaded codelist ingestion ---

def ingest_opencodelists_csv(csv_path: str | Path, codelist_name: str = ""):
    """
    Ingest a pre-downloaded OpenCodelists CSV into SQLite.
    CSV format: code,term (standard OpenCodelists export).
    """
    path = Path(csv_path)
    if not path.exists():
        logger.warning("CSV not found: %s", csv_path)
        return 0

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        codes = []
        for row in reader:
            code = row.get("code") or row.get("id") or ""
            term = row.get("term") or row.get("description") or ""
            if code and term:
                codes.append({
                    "code": str(code),
                    "term": term,
                    "vocabulary": "SNOMED CT",
                    "source": SOURCE_TAG,
                    "domain": "Condition",
                    "cluster_id": path.stem,
                    "cluster_description": codelist_name or path.stem,
                    "active": 1,
                })

    if codes:
        count = insert_codes(codes)
        # also load into ChromaDB for semantic search
        chroma_records = [
            {"code": c["code"], "term": c["term"], "vocabulary": c["vocabulary"],
             "source": c["source"], "domain": c["domain"]}
            for c in codes
        ]
        add_to_chroma(chroma_records)
        logger.info("Ingested %d codes from %s", count, path.name)
        return count
    return 0


def ingest_opencodelists_dir(directory: str | Path):
    """Ingest all CSVs from a directory of pre-downloaded OpenCodelists exports."""
    dirpath = Path(directory)
    if not dirpath.exists():
        logger.warning("OpenCodelists directory not found: %s", directory)
        return 0

    total = 0
    for csv_file in sorted(dirpath.glob("*.csv")):
        # use filename as codelist name (e.g. diabetes-type-2.csv → diabetes-type-2)
        count = ingest_opencodelists_csv(csv_file, codelist_name=csv_file.stem)
        total += count

    logger.info("Ingested %d total codes from %s", total, dirpath)
    return total


# --- Live scraping fallback ---

def _search_codelists_live(condition: str, coding_system: str = "snomedct") -> list[dict]:
    """Search OpenCodelists website for published code lists matching a condition."""
    try:
        r = requests.get(
            BASE_URL,
            params={"q": condition, "coding_system_id": coding_system},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as exc:
        logger.warning("OpenCodelists search failed: %s", exc)
        return []

    links = re.findall(r'href="(/codelist/[^"]+/)"', r.text)
    seen = set()
    codelists = []
    for link in links:
        parts = link.strip("/").split("/")
        if len(parts) == 3 and link not in seen:
            seen.add(link)
            codelists.append({"path": link, "org": parts[1], "slug": parts[2]})

    return codelists[:MAX_CODELISTS]


def _find_csv_url(codelist_path: str) -> str | None:
    """Find the latest CSV download link on a codelist page."""
    if not codelist_path.startswith("/codelist/"):
        return None
    try:
        r = requests.get(f"{BASE_URL}{codelist_path}", timeout=15)
        r.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to load %s: %s", codelist_path, exc)
        return None

    matches = re.findall(r'href="([^"]*download\.csv[^"]*)"', r.text)
    if not matches:
        logger.debug("No CSV download link found at %s", codelist_path)
    return matches[0] if matches else None


def _download_csv(csv_path: str) -> list[dict]:
    """Download and parse a codelist CSV from OpenCodelists."""
    try:
        r = requests.get(f"{BASE_URL}{csv_path}", timeout=15)
        r.raise_for_status()
    except Exception as exc:
        logger.warning("CSV download failed for %s: %s", csv_path, exc)
        return []

    reader = csv.DictReader(StringIO(r.text))
    codes = []
    for row in reader:
        code = row.get("code") or row.get("id") or ""
        term = row.get("term") or row.get("description") or ""
        if code and term:
            codes.append({"code": str(code), "term": term})
    return codes


def _search_live(condition: str) -> list[dict]:
    """Scrape OpenCodelists for codes. Used as fallback when pre-downloaded data misses."""
    codelists = _search_codelists_live(condition)
    if not codelists:
        return []

    all_codes = []
    for cl in codelists:
        time.sleep(REQUEST_GAP)
        csv_url = _find_csv_url(cl["path"])
        if not csv_url:
            continue

        time.sleep(REQUEST_GAP)
        codes = _download_csv(csv_url)
        for c in codes:
            all_codes.append({
                "code": c["code"],
                "term": c["term"],
                "vocabulary": "SNOMED CT",
                "source": f"OpenCodelists ({cl['org']})",
                "domain": "Condition",
                "similarity_score": None,
                "usage_frequency": None,
            })

        logger.info("OpenCodelists live: %s/%s → %d codes", cl["org"], cl["slug"], len(codes))

    return all_codes


# --- LangGraph node ---

def retrieve_from_opencodelists(state: dict) -> dict:
    """
    LangGraph node: search for codes in OpenCodelists.
    First checks SQLite (pre-downloaded data), falls back to live scraping.
    """
    conditions = state.get("parsed_conditions", [])
    if not conditions:
        logger.warning("No conditions to search")
        return {"retrieved_codes": [], "sources_queried": []}

    all_codes = []
    for condition in conditions:
        name = condition.get("name", "")
        if not name:
            continue

        # try pre-downloaded data in SQLite first
        local_rows = search_by_condition(name, vocabulary=None)
        local_oc = [r for r in local_rows if "OpenCodelists" in r.get("source", "")]

        if local_oc:
            for r in local_oc:
                all_codes.append({
                    "code": r["code"],
                    "term": r["term"],
                    "vocabulary": r["vocabulary"],
                    "source": r["source"],
                    "domain": r["domain"],
                    "similarity_score": None,
                    "usage_frequency": None,
                })
            logger.info("OpenCodelists (local): '%s' → %d codes", name, len(local_oc))
        else:
            # fallback to live scraping
            logger.info("OpenCodelists: no local data for '%s', trying live", name)
            live_codes = _search_live(name)
            all_codes.extend(live_codes)

    return {
        "retrieved_codes": all_codes,
        "sources_queried": [SOURCE_TAG],
    }
