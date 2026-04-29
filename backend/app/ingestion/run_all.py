"""
Run all data ingestion: QOF, OpenCodelists, OPCS → SQLite + ChromaDB.

Usage:
    python -m app.ingestion.run_all                    # default paths
    python -m app.ingestion.run_all --data-dir /data   # custom data dir
"""

import argparse
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def run_all(data_dir: str = "data") -> dict:
    base = Path(data_dir)
    results = {}
    t0 = time.time()

    # QOF Business Rules
    qof_files = list((base / "raw").glob("Business_Rules_*.xlsm"))
    if qof_files:
        from app.ingestion.ingest_qof import ingest_qof
        logger.info("Ingesting QOF: %s", qof_files[0].name)
        results["qof"] = ingest_qof(qof_files[0])
    else:
        logger.warning("No QOF Excel found in %s/raw/", base)

    # OpenCodelists CSVs
    oc_dir = base / "raw" / "opencodelists"
    if oc_dir.exists() and list(oc_dir.glob("*.csv")):
        from app.graph.nodes.opencodelists_retriever import ingest_opencodelists_dir
        logger.info("Ingesting OpenCodelists from %s", oc_dir)
        results["opencodelists"] = ingest_opencodelists_dir(oc_dir)
    else:
        logger.warning("No OpenCodelists CSVs found in %s", oc_dir)

    # OPCS XML
    opcs_files = list((base / "opcs").glob("*.xml")) if (base / "opcs").exists() else []
    if opcs_files:
        from app.ingestion.ingest_opcs import ingest_opcs
        logger.info("Ingesting OPCS: %s", opcs_files[0].name)
        results["opcs"] = ingest_opcs(opcs_files[0])
    else:
        logger.warning("No OPCS XML found in %s/opcs/", base)

    # ICD-10 XML (NHS TRUD 5th Edition) — release unzips into a nested
    # ICD10_Edition5_XML_<date>/Content/ folder, so search recursively.
    if (base / "icd10").exists():
        from app.ingestion.ingest_icd10 import _find_icd10_xml, ingest_icd10
        icd10_xml = _find_icd10_xml(base / "icd10")
        if icd10_xml:
            logger.info("Ingesting ICD-10: %s", icd10_xml.name)
            results["icd10"] = ingest_icd10(icd10_xml)
        else:
            logger.warning("No ICD-10 XML found in %s/icd10/", base)
    else:
        logger.warning("No ICD-10 directory at %s/icd10/", base)

    elapsed = round(time.time() - t0, 1)

    from app.db.code_store import get_stats
    stats = get_stats()
    logger.info("Ingestion complete in %ss. SQLite: %s", elapsed, stats)

    results["elapsed_seconds"] = elapsed
    results["stats"] = stats
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data", help="Path to data directory")
    args = parser.parse_args()
    result = run_all(args.data_dir)
    print(f"\nDone: {result}")
