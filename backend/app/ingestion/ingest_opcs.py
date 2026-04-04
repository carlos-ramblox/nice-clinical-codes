"""Parse OPCS-4 XML codes and load into SQLite + ChromaDB."""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

from app.db.code_store import insert_codes
from app.db.vector_store import add_codes as add_to_chroma

logger = logging.getLogger(__name__)

SOURCE_TAG = "OPCS-4 (NHS England)"
NAMESPACE = {"opcs": "https://www.digital.nhs.uk/opcs/codes"}


def parse_opcs_xml(filepath: str | Path) -> list[dict]:
    """Parse OPCS XML file into standard code records."""
    path = Path(filepath)
    if not path.exists():
        logger.warning("OPCS file not found: %s", filepath)
        return []

    tree = ET.parse(path)
    root = tree.getroot()

    records = []
    for elem in root.findall("opcs:code", NAMESPACE):
        code = elem.get("CODE", "").strip()
        title = elem.get("TITLE", "").strip()
        if code and title:
            records.append({
                "code": code,
                "term": title,
                "vocabulary": "OPCS-4",
                "source": SOURCE_TAG,
                "domain": "Procedure",
                "cluster_id": "",
                "cluster_description": "",
                "active": 1,
            })

    logger.info("Parsed %d OPCS codes from %s", len(records), path.name)
    return records


def ingest_opcs(filepath: str | Path) -> dict:
    """Parse OPCS XML and load into SQLite + ChromaDB."""
    records = parse_opcs_xml(filepath)
    if not records:
        return {"sqlite": 0, "chroma": 0}

    sqlite_count = insert_codes(records)

    chroma_records = [
        {"code": r["code"], "term": r["term"], "vocabulary": r["vocabulary"],
         "source": r["source"], "domain": r["domain"]}
        for r in records
    ]
    add_to_chroma(chroma_records)

    logger.info("OPCS: %d in SQLite, %d in ChromaDB", sqlite_count, len(chroma_records))
    return {"sqlite": sqlite_count, "chroma": len(chroma_records)}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    path = sys.argv[1] if len(sys.argv) > 1 else "data/opcs/OPCS411 CodesAndTitles Nov 2025 V1.0.xml"
    print(ingest_opcs(path))
