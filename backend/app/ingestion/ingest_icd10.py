"""Parse ICD-10 5th Edition (NHS TRUD) XML codes and load into SQLite + ChromaDB.

NHS TRUD's ICD-10 5th Edition release is distributed as a flat NHS-namespaced
XML where each code is a single element:

    <DSV xmlns="urn:nhs-org:icd-10">
      <CLASS CODE="I21"   ALT_CODE="I21"  USAGE="DEFAULT" USAGE_UK="3"
             DESCRIPTION="Acute myocardial infarction" />
      <CLASS CODE="I21.0" ALT_CODE="I210" USAGE="DEFAULT" USAGE_UK="3"
             DESCRIPTION="Acute transmural myocardial infarction of anterior wall" />
      ...
    </DSV>

USAGE distinguishes the dagger/asterisk system (DEFAULT vs DAGGER vs
ASTERISK). All three are valid ICD-10 codes used in clinical coding, so we
ingest all of them.

A defensive fallback also accepts the OPCS-style ``<code CODE="..."
TITLE="..."/>`` schema in case TRUD ever ships a different XML shape.
"""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

from app.db.code_store import insert_codes
from app.db.vector_store import add_codes as add_to_chroma

logger = logging.getLogger(__name__)

VOCABULARY = "ICD-10 (WHO)"
SOURCE_TAG = "ICD-10 5th Edition (NHS TRUD)"


def _strip_ns(tag: str) -> str:
    """Drop XML namespace prefix (``{ns}CLASS`` -> ``CLASS``)."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _compose_term(desc: str, mod4: str | None, mod5: str | None) -> str:
    """Fold MODIFIER_4 / MODIFIER_5 into the term.

    TRUD ICD-10 stores the 4th- and 5th-character meanings as separate
    attributes (DESCRIPTION = parent term). Without folding them in,
    sibling codes are indistinguishable for semantic retrieval — e.g.
    every ``E11.x`` code would embed as just "Type 2 diabetes mellitus".
    """
    parts = [desc]
    if mod4:
        parts.append(mod4.strip())
    if mod5:
        parts.append(mod5.strip())
    return ", ".join(p for p in parts if p)


def _parse_nhs_flat(root: ET.Element) -> list[dict]:
    """Extract codes from the NHS TRUD ``<CLASS CODE="..." DESCRIPTION="..."/>`` schema."""
    records: list[dict] = []
    seen: set[str] = set()
    for elem in root.iter():
        if _strip_ns(elem.tag) != "CLASS":
            continue
        code = (elem.get("CODE") or "").strip()
        desc = (elem.get("DESCRIPTION") or "").strip()
        if not code or not desc or code in seen:
            continue
        term = _compose_term(desc, elem.get("MODIFIER_4"), elem.get("MODIFIER_5"))
        seen.add(code)
        records.append({
            "code": code,
            "term": term,
            "vocabulary": VOCABULARY,
            "source": SOURCE_TAG,
            "domain": "Condition",
            "cluster_id": "",
            "cluster_description": "",
            "active": 1,
        })
    return records


def _parse_opcs_style(root: ET.Element) -> list[dict]:
    """Fallback: OPCS-style ``<code CODE="..." TITLE="..."/>`` XML."""
    records: list[dict] = []
    seen: set[str] = set()
    for elem in root.iter():
        if _strip_ns(elem.tag) != "code":
            continue
        code = (elem.get("CODE") or elem.get("code") or "").strip()
        title = (elem.get("TITLE") or elem.get("title") or "").strip()
        if not code or not title or code in seen:
            continue
        seen.add(code)
        records.append({
            "code": code,
            "term": title,
            "vocabulary": VOCABULARY,
            "source": SOURCE_TAG,
            "domain": "Condition",
            "cluster_id": "",
            "cluster_description": "",
            "active": 1,
        })
    return records


def parse_icd10_xml(filepath: str | Path) -> list[dict]:
    """Parse an ICD-10 XML file into standard code records."""
    path = Path(filepath)
    if not path.exists():
        logger.warning("ICD-10 file not found: %s", filepath)
        return []

    tree = ET.parse(path)
    root = tree.getroot()

    records = _parse_nhs_flat(root)
    schema = "NHS-flat (CLASS/DESCRIPTION)"
    if not records:
        records = _parse_opcs_style(root)
        schema = "OPCS-style (code/TITLE)"

    logger.info("Parsed %d ICD-10 codes from %s [%s]", len(records), path.name, schema)
    return records


def ingest_icd10(filepath: str | Path) -> dict:
    """Parse ICD-10 XML and load into SQLite + ChromaDB.

    Idempotent: deletes any existing rows for this source before inserting
    so re-runs pick up term changes (e.g. when MODIFIER_4/5 folding was
    added). ChromaDB's ``upsert`` already replaces docs by ID.
    """
    records = parse_icd10_xml(filepath)
    if not records:
        return {"sqlite": 0, "chroma": 0}

    from app.db.code_store import get_connection
    conn = get_connection()
    deleted = conn.execute(
        "DELETE FROM codes WHERE source = ?", (SOURCE_TAG,)
    ).rowcount
    conn.commit()
    if deleted:
        logger.info("Cleared %d stale ICD-10 rows from SQLite before re-ingest", deleted)

    sqlite_count = insert_codes(records)

    chroma_records = [
        {"code": r["code"], "term": r["term"], "vocabulary": r["vocabulary"],
         "source": r["source"], "domain": r["domain"]}
        for r in records
    ]
    chroma_count = add_to_chroma(chroma_records)

    logger.info("ICD-10: %d in SQLite, %d in ChromaDB", sqlite_count, chroma_count)
    return {"sqlite": sqlite_count, "chroma": chroma_count}


def _find_icd10_xml(data_dir: Path) -> Path | None:
    """Locate the codes-and-titles XML inside data/icd10/.

    Walks the directory recursively because the TRUD release unzips into a
    nested ``ICD10_Edition5_XML_<date>/Content/`` folder. We pick the file
    whose name contains ``CodesAndTitles`` to avoid the equivalence tables
    that ship in the same release.
    """
    candidates = [p for p in data_dir.rglob("*.xml") if "CodesAndTitles" in p.name]
    if candidates:
        return sorted(candidates)[0]
    fallback = sorted(data_dir.rglob("*.xml"))
    return fallback[0] if fallback else None


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1:
        path: str | Path = sys.argv[1]
    else:
        found = _find_icd10_xml(Path("data/icd10"))
        if not found:
            print("No ICD-10 XML found in data/icd10/. Pass a path as the first argument.")
            sys.exit(1)
        path = found
    print(ingest_icd10(path))
