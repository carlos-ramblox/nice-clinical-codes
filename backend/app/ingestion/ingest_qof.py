"""Parse QOF Business Rules Excel and load into SQLite + ChromaDB."""

import logging
from pathlib import Path

import pandas as pd

from app.db.code_store import insert_codes, get_stats
from app.db.vector_store import add_codes as add_to_chroma

logger = logging.getLogger(__name__)

QOF_SOURCE = "QOF Business Rules 2024-25"
SHEET_NAME = "Expanded Cluster List"
HEADER_ROW = 14  # 0-indexed row where column headers live


def parse_qof_excel(filepath: str | Path) -> pd.DataFrame:
    """Parse the Expanded Cluster List sheet from the QOF business rules Excel."""
    logger.info("Parsing QOF Excel: %s", filepath)

    df = pd.read_excel(
        filepath,
        sheet_name=SHEET_NAME,
        header=HEADER_ROW,
        engine="openpyxl",
    )

    # clean column names
    df.columns = df.columns.str.strip()

    # drop rows where SNOMED concept ID is missing
    df = df.dropna(subset=["SNOMED concept ID"])

    # only active codes (column is float: 1.0 = active, 0.0 = inactive)
    df = df[df["Active status"] == 1.0]

    # normalize into our standard format
    records = []
    for _, row in df.iterrows():
        records.append({
            "code": str(int(row["SNOMED concept ID"])) if pd.notna(row["SNOMED concept ID"]) else "",
            "term": str(row.get("Code description", "")),
            "vocabulary": "SNOMED CT",
            "source": QOF_SOURCE,
            "domain": "Drug" if "drug" in str(row.get("Type of inclusion (in code string)", "")).lower() else "Condition",
            "cluster_id": str(row.get("Cluster ID", "")),
            "cluster_description": str(row.get("Cluster description", "")),
            "active": 1,
        })

    result = pd.DataFrame(records)
    logger.info("Parsed %d active QOF codes from %d clusters", len(result), result["cluster_id"].nunique())
    return result


def ingest_qof(filepath: str | Path):
    """Full ingestion: parse Excel → SQLite + ChromaDB."""
    df = parse_qof_excel(filepath)
    records = df.to_dict(orient="records")

    # load into SQLite
    sqlite_count = insert_codes(records)
    logger.info("SQLite: %d codes loaded", sqlite_count)

    # load into ChromaDB for semantic search
    chroma_records = [
        {
            "code": r["code"],
            "term": r["term"],
            "vocabulary": r["vocabulary"],
            "source": r["source"],
            "domain": r["domain"],
        }
        for r in records
    ]
    chroma_count = add_to_chroma(chroma_records)
    logger.info("ChromaDB: %d codes embedded", chroma_count)

    stats = get_stats()
    logger.info("SQLite stats: %s", stats)

    return {"sqlite": sqlite_count, "chroma": chroma_count, "stats": stats}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    path = sys.argv[1] if len(sys.argv) > 1 else "data/raw/Business_Rules_Combined_Change_Log_QOF+2024-25_v49.1.xlsm"
    result = ingest_qof(path)
    print(f"Done: {result}")
