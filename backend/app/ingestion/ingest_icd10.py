"""Parse ICD-10 5th Edition (NHS TRUD) XML codes and load into SQLite + ChromaDB.

NHS TRUD's ICD-10 5th Edition release is distributed as ClaML (Classification
Markup Language) XML. The relevant structure for our purposes:

    <ClaML>
      <Class code="I21" kind="category">
        <Rubric kind="preferred">
          <Label>Acute myocardial infarction</Label>
        </Rubric>
      </Class>
      ...
    </ClaML>

We extract every ``<Class kind="category">`` (the actual diagnosis codes like
``I21``, ``I21.0``) and skip ``chapter`` / ``block`` (groupings used for
navigation, not coding). The preferred-rubric label is the term we embed.

A defensive fallback also accepts the OPCS-style flat ``<code CODE="..."
TITLE="..."/>`` schema, in case TRUD ever ships ICD-10 in that format.
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
    """Drop XML namespace prefix (``{ns}Class`` -> ``Class``)."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _preferred_label(class_elem: ET.Element) -> str:
    """Return the text of ``<Rubric kind="preferred">/<Label>`` for a Class.

    ClaML allows multiple Rubrics per Class (preferred, inclusion, exclusion,
    note, …). We only want the preferred term.
    """
    for rubric in class_elem.iter():
        if _strip_ns(rubric.tag) != "Rubric":
            continue
        if rubric.get("kind") != "preferred":
            continue
        for child in rubric.iter():
            if _strip_ns(child.tag) == "Label":
                # Label may contain mixed inline tags (Term, Reference, Fragment).
                # ``itertext`` flattens them into a single readable string.
                text = "".join(child.itertext()).strip()
                if text:
                    return text
    return ""


def _parse_claml(root: ET.Element) -> list[dict]:
    """Extract ICD-10 diagnosis codes from a ClaML root element."""
    records: list[dict] = []
    seen: set[str] = set()
    for cls in root.iter():
        if _strip_ns(cls.tag) != "Class":
            continue
        kind = cls.get("kind", "")
        # chapters and blocks are navigational groupings (e.g. "I00-I99",
        # "I20-I25"); only "category" is an actual ICD-10 diagnosis code.
        if kind != "category":
            continue
        code = (cls.get("code") or "").strip()
        if not code or code in seen:
            continue
        term = _preferred_label(cls)
        if not term:
            continue
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


def _parse_flat(root: ET.Element) -> list[dict]:
    """Fallback: OPCS-style flat ``<code CODE="..." TITLE="..."/>`` XML."""
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
    root_tag = _strip_ns(root.tag)

    if root_tag == "ClaML":
        records = _parse_claml(root)
        schema = "ClaML"
    else:
        records = _parse_flat(root)
        schema = f"flat ({root_tag})"

    logger.info("Parsed %d ICD-10 codes from %s [%s]", len(records), path.name, schema)
    return records


def ingest_icd10(filepath: str | Path) -> dict:
    """Parse ICD-10 XML and load into SQLite + ChromaDB."""
    records = parse_icd10_xml(filepath)
    if not records:
        return {"sqlite": 0, "chroma": 0}

    sqlite_count = insert_codes(records)

    chroma_records = [
        {"code": r["code"], "term": r["term"], "vocabulary": r["vocabulary"],
         "source": r["source"], "domain": r["domain"]}
        for r in records
    ]
    chroma_count = add_to_chroma(chroma_records)

    logger.info("ICD-10: %d in SQLite, %d in ChromaDB", sqlite_count, chroma_count)
    return {"sqlite": sqlite_count, "chroma": chroma_count}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        # default: pick the first XML in data/icd10/
        candidates = sorted(Path("data/icd10").glob("*.xml"))
        if not candidates:
            print("No ICD-10 XML found in data/icd10/. Pass a path as the first argument.")
            sys.exit(1)
        path = str(candidates[0])
    print(ingest_icd10(path))
