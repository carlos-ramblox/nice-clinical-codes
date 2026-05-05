"""Ingest NHS Digital code-usage CSVs into the ``code_usage`` SQLite
table (T31). Methodology follows Bennett Institute's OpenCodeCounts
package; the CSVs themselves come from the upstream NHS Digital
publications directly (Open Government Licence v3.0) so we redistribute
nothing from Bennett's harmonised dataset.

Three datasets are supported:

- SNOMED CT primary-care GP usage (Aug-Jul, NHS Digital monthly
  publication). Counts are rounded to the nearest 10; any underlying
  count of 1-4 is withheld under NHS Digital's privacy rule.
- ICD-10 inpatient HES usage (Apr-Mar, FCE-level). Unrounded; codes
  with zero usage are excluded from the source.
- OPCS-4 inpatient HES usage (Apr-Mar). Unrounded; zero usage excluded.

The loader applies the rounding/withholding rule itself rather than
relying on the upstream having pre-applied it. That makes the rule
testable and pins the disclosure: if NHS Digital ever change the rule,
our published-rounded values stay consistent until we re-pin.

Input file resolution is column-name-tolerant. NHS Digital's CSV
column names drift between releases (e.g. ``SNOMED_Concept_ID`` vs
``SNOMED Concept ID`` vs ``SNOMED CT Concept ID``); the loader
matches by case-insensitive keyword rather than exact header.

Run as a script::

    python -m app.ingestion.opencodecounts                  # ingest local CSVs
    python -m app.ingestion.opencodecounts --refresh        # print fetch URLs

The ``--refresh`` flag deliberately does *not* fetch automatically.
NHS Digital file URLs are publication-specific hashes that drift; the
agent prints the canonical landing-page URLs and lets the operator
pull the CSV manually into ``data/raw/opencodecounts/``. This keeps
the build reproducible against a versioned CSV checked-out at build
time, matching how the QOF / OPCS / ICD-10 ingests are wired today.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
from datetime import date
from pathlib import Path
from typing import Iterable

from app.db.code_store import get_connection

logger = logging.getLogger(__name__)


# NHS Digital landing pages for the three upstream datasets. The actual
# CSV download links inside these pages are publication-specific hashed
# URLs that change with each release; we don't pin those because doing
# so would make the build silently fetch stale data when NHS Digital
# rotate their files. Document the landing page; the operator selects
# the latest release.
_NHS_DIGITAL_PAGES = {
    "snomed_primary_care": (
        "SNOMED Code Usage in Primary Care",
        "https://digital.nhs.uk/data-and-information/publications/statistical/"
        "mi-snomed-code-usage-in-primary-care",
    ),
    "icd10_hes_inpatient": (
        "ICD-10 codes in Hospital Admitted Patient Care Activity",
        "https://digital.nhs.uk/data-and-information/publications/statistical/"
        "hospital-admitted-patient-care-activity",
    ),
    "opcs4_hes_inpatient": (
        "OPCS-4 codes in Hospital Admitted Patient Care Activity",
        "https://digital.nhs.uk/data-and-information/publications/statistical/"
        "hospital-admitted-patient-care-activity",
    ),
}


# Filename-pattern → dataset key. The loader walks
# ``data/raw/opencodecounts/`` and routes each file to its dataset by
# matching a substring (case-insensitive). Operators name the file
# whatever they want as long as it includes "snomed" / "icd10" /
# "opcs" plus a year token; we don't depend on NHS Digital's
# release-specific filename hash.
_DATASET_PATTERNS: list[tuple[str, str]] = [
    ("snomed", "snomed_primary_care"),
    ("icd10", "icd10_hes_inpatient"),
    ("icd-10", "icd10_hes_inpatient"),
    ("opcs", "opcs4_hes_inpatient"),
]


# Vocabulary-name mapping. ``code_usage.vocabulary`` must use the
# canonical name from ``config.OMOPHUB_VOCABULARIES`` so the lookup
# matches what the retrievers populate on ``RetrievedCode.vocabulary``.
_DATASET_TO_VOCAB = {
    "snomed_primary_care": "SNOMED CT",
    "icd10_hes_inpatient": "ICD-10 (WHO)",
    "opcs4_hes_inpatient": "OPCS-4",
}


_DATASET_TO_SETTING = {
    "snomed_primary_care": "primary_care",
    "icd10_hes_inpatient": "secondary_care_hes",
    "opcs4_hes_inpatient": "secondary_care_hes",
}


# Rounding/withholding rules per dataset. SNOMED primary-care follows
# NHS Digital's documented rule (round to nearest 10, withhold 1-4);
# the HES datasets are unrounded with zero-usage codes simply absent.
_ROUND_TO_NEAREST_10 = {"snomed_primary_care"}
_WITHHOLD_BELOW_5 = {"snomed_primary_care"}


_YEAR_RE = re.compile(r"(20\d{2})")


def round_to_nearest_10(n: int) -> int:
    """Round to the nearest 10, half-up at .5 boundaries.

    NHS Digital's stated rule for SNOMED primary-care counts. Pinning
    it locally rather than trusting the upstream means a future change
    in their rule does not silently change the values we publish.
    """
    if n < 0:
        raise ValueError(f"usage count must be non-negative, got {n}")
    return ((n + 5) // 10) * 10


def is_withheld_below_5(n: int) -> bool:
    """SNOMED primary-care withholding rule: counts of 1-4 are
    suppressed for re-identification risk. Zero is *not* withheld —
    a true zero is a meaningful clinical signal."""
    return 1 <= n <= 4


def _detect_dataset(filename: str) -> str | None:
    name = filename.lower()
    for needle, key in _DATASET_PATTERNS:
        if needle in name:
            return key
    return None


def _find_column(fieldnames: Iterable[str], *needles: str) -> str | None:
    """Case-insensitive header lookup. Returns the first header that
    contains *all* of the supplied needles, or None when none match.
    Lets the loader survive NHS Digital's column-name drift without
    a per-release schema patch."""
    lowered = [(f, f.lower()) for f in fieldnames]
    for f, lc in lowered:
        if all(n.lower() in lc for n in needles):
            return f
    return None


def _parse_period(
    fieldnames: Iterable[str],
    sample_row: dict,
    fallback_year: int | None,
) -> tuple[str, str, int]:
    """Extract (period_start, period_end, year) from the CSV.

    NHS Digital files include the period as either:
    - explicit ``start_date`` / ``end_date`` columns (Bennett-derived
      shape; some NHS publications also expose them), or
    - a single ``Period`` column like "Aug 2024 - Jul 2025", or
    - implied by the filename (e.g. ``snomed_2024_25.csv``).

    Falls back to the filename year when no period columns are
    present. The ``year`` is taken from the period's end date so the
    ``MAX(year)`` cache picks the most-recent release.
    """
    start_col = _find_column(fieldnames, "start", "date") or _find_column(fieldnames, "period", "start")
    end_col = _find_column(fieldnames, "end", "date") or _find_column(fieldnames, "period", "end")

    if start_col and end_col and sample_row.get(start_col) and sample_row.get(end_col):
        ps = sample_row[start_col].strip()
        pe = sample_row[end_col].strip()
        year = _extract_year(pe) or fallback_year or date.today().year
        return ps, pe, year

    if fallback_year:
        return f"{fallback_year - 1}-08-01", f"{fallback_year}-07-31", fallback_year

    today = date.today().year
    return f"{today - 1}-08-01", f"{today}-07-31", today


def _extract_year(text: str) -> int | None:
    m = _YEAR_RE.search(text or "")
    return int(m.group(1)) if m else None


_FILENAME_RANGE_RE = re.compile(r"(20\d{2})[-_](\d{2,4})")


def _filename_year(path: Path) -> int | None:
    """Extract the period-END year from a filename.

    NHS Digital and Bennett both use a hyphenated academic-year tag
    in filenames (``snomed_code_usage_2024-25.txt``,
    ``hosp-epis-stat-admi-diag-2024-25.csv``,
    ``snomed_2024_25.csv``). The "2024-25" tag means *period running
    from 2024 into 2025*; we want the end year (2025) so the
    most-recent-year cache picks the right row when a future
    re-ingest adds a 2025-26 release alongside.

    Falls back to the first 4-digit year in the stem when no range
    pattern is present (e.g. a file named just ``snomed_2025.csv``).
    """
    m = _FILENAME_RANGE_RE.search(path.stem)
    if m:
        start = int(m.group(1))
        suffix = m.group(2)
        if len(suffix) == 2:
            return start + 1
        # "2024-2025" form: pick the second 4-digit year directly.
        return int(suffix)
    return _extract_year(path.stem)


def _safe_int(value: str | int | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    s = str(value).strip().replace(",", "")
    if not s or s in {"*", "[c]", "..", "-"}:
        # NHS Digital encodes withheld values variously across releases;
        # treat them all as "not a usable number" and let the caller
        # decide whether to mark the row withheld or skip it.
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _safe_active_flag(value: str | int | None) -> int | None:
    """Map TRUE/FALSE/1/0 strings to ints; return None for absent."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return 1 if value else 0
    s = str(value).strip().lower()
    if s in {"true", "1", "yes", "y", "t"}:
        return 1
    if s in {"false", "0", "no", "n", "f"}:
        return 0
    return None


# NHS Digital HES files are published in long ("attribute-per-row")
# format with one row per (code, category, attribute) tuple — different
# from the wide format used by the SNOMED primary-care file or by
# Bennett's processed dataset. Parsing them needs a separate code path
# that filters down to a single (category, attribute) slice.
#
# Categories worth keeping: 3-character codes at primary-position
# (DIAG_3_01 / OPERTN_3_01) and 4-character codes at primary-position
# (DIAG_4_01 / OPERTN_4_01). The 3-char and 4-char rows describe
# DIFFERENT codes ("E11" is 3-char, "E11.0" is 4-char), so ingesting
# both is necessary to cover the user's whole DB.
#
# Attribute worth keeping: ``FCE_SUM`` (Finished Consultant Episodes,
# the "this code was the primary diagnosis on N completed episodes"
# count). NHS Digital case-mixes ``FCE_SUM`` (ICD-10) vs ``FCE_Sum``
# (OPCS-4); we match case-insensitively.
#
# Note: only the position-01 (primary diagnosis / primary procedure)
# slice is published, not any-position. So the count we surface is
# "primary-only" — see LIMITATIONS.md for the user-visible caveat.
_HES_CATEGORIES_KEEP = {"DIAG_3_01", "DIAG_4_01", "OPERTN_3_01", "OPERTN_4_01"}
_HES_ATTRIBUTE_KEEP = "fce_sum"  # case-folded; matches FCE_SUM and FCE_Sum


def _sniff_dialect(sample: str) -> "csv.Dialect | type[csv.Dialect]":
    """Return a csv dialect for ``sample``. Tab and comma are the only
    delimiters NHS Digital uses across the files we ingest. Sniffer
    sometimes mispicks on small samples; fall back to a quick
    head-counting heuristic when it does."""
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t")
    except csv.Error:
        # Sniffer can throw on tiny samples or mixed whitespace. Cheap
        # fallback: more tabs than commas in the first line → tab.
        first_line = sample.splitlines()[0] if sample else ""

        class _Fallback(csv.excel):
            delimiter = "\t" if first_line.count("\t") > first_line.count(",") else ","
        return _Fallback


def _is_hes_long_format(fieldnames: list[str]) -> bool:
    """The HES Hospital Activity files publish one row per
    (code, category, attribute) tuple. Detect them by the joint
    presence of ``Code`` and ``Category`` headers — neither the
    Bennett wide format nor the SNOMED primary-care file has both."""
    names_lower = {f.lower() for f in fieldnames}
    return "code" in names_lower and "category" in names_lower


def _parse_hes_long(
    reader: csv.DictReader,
    dataset: str,
    vocab: str,
    setting: str,
    fallback_year: int | None,
) -> tuple[list[dict], int, int, int]:
    """Walk a HES long-format file once, emitting one wide-format row
    per kept (code, category) pair. Reuses the DictReader the caller
    already constructed so we don't double-read the header.

    Returns ``(rows, kept, skipped, year)``."""
    fieldnames = list(reader.fieldnames or [])
    code_col = _find_column(fieldnames, "code") or "Code"
    category_col = _find_column(fieldnames, "category") or "Category"
    # ICD-10 calls the attribute column "Attribute"; OPCS-4 calls it
    # "Measure". Match precisely on the single-word header to avoid
    # accidentally selecting "Measure Value".
    attribute_col = (
        _find_column(fieldnames, "attribute")
        or next((f for f in fieldnames if f.strip().lower() == "measure"), None)
    )
    # ICD-10 calls the count column "Value"; OPCS-4 calls it
    # "Measure Value".
    value_col = (
        _find_column(fieldnames, "measure", "value")
        or next((f for f in fieldnames if f.strip().lower() == "value"), None)
    )
    if not (code_col and category_col and attribute_col and value_col):
        raise ValueError(
            f"HES long-format file missing required columns "
            f"(code/category/attribute/value). Saw headers: {fieldnames}"
        )

    # HES Hospital Activity period: Apr-Mar. The files have no date
    # columns so derive the year from the filename token
    # ("...2024-25..." → 2025).
    year = fallback_year or date.today().year
    period_start = f"{year - 1}-04-01"
    period_end = f"{year}-03-31"

    out: list[dict] = []
    kept = skipped = 0

    for raw_row in reader:
        category = (raw_row.get(category_col) or "").strip()
        if category not in _HES_CATEGORIES_KEEP:
            continue
        attribute = (raw_row.get(attribute_col) or "").strip().lower()
        if attribute != _HES_ATTRIBUTE_KEEP:
            continue
        code = (raw_row.get(code_col) or "").strip()
        if not code or code in {"-", "&"}:
            # Sentinel rows for category-level totals or non-data
            # placeholders. Skip — they're not real codes.
            skipped += 1
            continue
        count = _safe_int(raw_row.get(value_col))
        if count is None or count <= 0:
            # HES excludes zero-usage codes from the source; a None
            # parsed value means a malformed cell. Either way, skip.
            skipped += 1
            continue
        out.append({
            "vocabulary": vocab,
            "code": code,
            "year": year,
            "count": count,
            "setting": setting,
            "period_start": period_start,
            "period_end": period_end,
            "is_withheld": 0,
            "active_at_start": None,
            "active_at_end": None,
        })
        kept += 1

    return out, kept, skipped, year


def parse_csv(filepath: str | Path) -> tuple[str, list[dict]]:
    """Parse one NHS Digital usage file.

    Two shapes are recognised:
    - **Wide** (one row per code): NHS Digital SNOMED primary-care
      ``snomed_code_usage_<period>.txt`` with columns
      ``SNOMED_Concept_ID, Description, Usage, Active_at_Start,
      Active_at_End``. Tab-delimited.
    - **Long** (one row per (code, category, attribute) tuple): NHS
      Digital HES diagnoses (``hosp-epis-stat-admi-diag-*.csv``) and
      procedures (``hosp-epis-stat-admi-proc-*.csv``). Comma-delimited.
      We filter to (DIAG/OPERTN)_(3|4)_01 × FCE_SUM and emit one
      wide-format row per kept pair.

    Returns ``(dataset_key, rows)`` where ``rows`` are dicts ready for
    ``insert_usage_rows()``.
    """
    path = Path(filepath)
    dataset = _detect_dataset(path.name)
    if dataset is None:
        raise ValueError(
            f"Could not detect dataset from filename {path.name!r}. "
            f"Expected one of: snomed/icd10/opcs in the filename."
        )

    fallback_year = _filename_year(path)
    setting = _DATASET_TO_SETTING[dataset]
    vocab = _DATASET_TO_VOCAB[dataset]

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        dialect = _sniff_dialect(sample)
        reader = csv.DictReader(fh, dialect=dialect)
        if reader.fieldnames is None:
            raise ValueError(f"File has no header row: {path}")

        # HES long-format path: separate parser, returns early.
        if _is_hes_long_format(list(reader.fieldnames)):
            rows, kept, skipped, year = _parse_hes_long(
                reader, dataset, vocab, setting, fallback_year,
            )
            if skipped:
                logger.info("opencodecounts: skipped %d malformed/sentinel rows in %s",
                            skipped, path.name)
            logger.info(
                "opencodecounts (HES long): parsed %d rows from %s "
                "(vocab=%s, year=%d)",
                kept, path.name, vocab, year,
            )
            return dataset, rows

        # Wide-format path (SNOMED primary care, Bennett-style): existing
        # logic continues below.
        rows: list[dict] = []

        code_col = (
            _find_column(reader.fieldnames, "snomed", "code")
            or _find_column(reader.fieldnames, "snomed", "concept")
            or _find_column(reader.fieldnames, "icd10")
            or _find_column(reader.fieldnames, "icd", "10")
            or _find_column(reader.fieldnames, "opcs", "4")
            or _find_column(reader.fieldnames, "opcs4")
            or _find_column(reader.fieldnames, "code")
        )
        usage_col = (
            _find_column(reader.fieldnames, "usage")
            or _find_column(reader.fieldnames, "count")
            or _find_column(reader.fieldnames, "frequency")
        )
        if code_col is None or usage_col is None:
            raise ValueError(
                f"CSV {path.name} missing code or usage column; "
                f"headers: {list(reader.fieldnames)}"
            )

        active_start_col = _find_column(reader.fieldnames, "active", "start")
        active_end_col = _find_column(reader.fieldnames, "active", "end")

        first_row: dict | None = None
        period_start = period_end = ""
        year = fallback_year or date.today().year

        skipped = 0
        for raw_row in reader:
            if first_row is None:
                first_row = raw_row
                period_start, period_end, year = _parse_period(
                    reader.fieldnames, first_row, fallback_year
                )

            code = (raw_row.get(code_col) or "").strip()
            if not code:
                skipped += 1
                continue

            raw_usage = raw_row.get(usage_col, "")
            count = _safe_int(raw_usage)
            withheld = False

            if dataset in _WITHHOLD_BELOW_5:
                if count is None and (raw_usage or "").strip() in {"*", "[c]"}:
                    # Already withheld in source.
                    withheld = True
                elif count is not None and is_withheld_below_5(count):
                    # Defensive: NHS Digital should already withhold,
                    # but if the upstream ever leaks a 1-4 we apply the
                    # rule ourselves rather than republishing it.
                    withheld = True
                    count = None
                elif count is not None and dataset in _ROUND_TO_NEAREST_10:
                    count = round_to_nearest_10(count)
                else:
                    # SNOMED row with an unparseable usage cell that
                    # isn't a known withhold marker. Skip rather than
                    # surface a counted-but-None row, which would be
                    # logically inconsistent (UI would render an em-dash
                    # against a "counted" status badge).
                    skipped += 1
                    continue

            elif count is None:
                # ICD-10/OPCS-4: unparseable usage value with no
                # documented withholding rule — skip rather than
                # surface as a misleading row.
                skipped += 1
                continue

            rows.append({
                "vocabulary": vocab,
                "code": code,
                "year": year,
                "count": count,
                "setting": setting,
                "period_start": period_start,
                "period_end": period_end,
                "is_withheld": 1 if withheld else 0,
                "active_at_start": _safe_active_flag(raw_row.get(active_start_col)) if active_start_col else None,
                "active_at_end": _safe_active_flag(raw_row.get(active_end_col)) if active_end_col else None,
            })

        if skipped:
            logger.info("opencodecounts: skipped %d malformed rows in %s", skipped, path.name)

    logger.info(
        "opencodecounts: parsed %d rows from %s (vocab=%s, year=%d, withheld=%d)",
        len(rows), path.name, vocab, year,
        sum(1 for r in rows if r["is_withheld"]),
    )
    return dataset, rows


def insert_usage_rows(rows: list[dict]) -> int:
    """Bulk-insert into ``code_usage``. Replaces any prior row for the
    same (vocabulary, code, year, setting) so re-running the ingest
    against an updated CSV is idempotent."""
    if not rows:
        return 0
    conn = get_connection()
    conn.executemany(
        """INSERT OR REPLACE INTO code_usage
           (vocabulary, code, year, count, setting, period_start, period_end,
            is_withheld, active_at_start, active_at_end)
           VALUES (:vocabulary, :code, :year, :count, :setting, :period_start,
                   :period_end, :is_withheld, :active_at_start, :active_at_end)""",
        rows,
    )
    conn.commit()
    return len(rows)


def ingest_directory(data_dir: str | Path) -> dict:
    """Load every recognised CSV under ``<data_dir>/raw/opencodecounts/``."""
    base = Path(data_dir) / "raw" / "opencodecounts"
    if not base.exists():
        logger.warning("opencodecounts: directory not found: %s", base)
        return {"loaded": 0, "files": []}

    # NHS Digital ships the SNOMED primary-care file as tab-separated
    # ``.txt`` and the HES files as ``.csv``. Glob both so the operator
    # doesn't need to rename the SNOMED file before ingest.
    csvs = sorted(list(base.glob("*.csv")) + list(base.glob("*.txt")))
    if not csvs:
        logger.warning("opencodecounts: no CSV/TXT files in %s", base)
        return {"loaded": 0, "files": []}

    total = 0
    loaded_files: list[dict] = []
    for path in csvs:
        try:
            dataset, rows = parse_csv(path)
        except ValueError as exc:
            logger.warning("opencodecounts: skipped %s: %s", path.name, exc)
            continue
        n = insert_usage_rows(rows)
        total += n
        loaded_files.append({"file": path.name, "dataset": dataset, "rows": n})

    # Bust the lookup cache so the runtime sees the new most-recent
    # year without a process restart.
    from app.db.code_usage import refresh_year_cache
    refresh_year_cache()

    logger.info("opencodecounts: loaded %d total rows from %d files", total, len(loaded_files))
    return {"loaded": total, "files": loaded_files}


def print_refresh_instructions() -> None:
    """Document the NHS Digital landing pages so an operator can
    download the current release manually. Intentionally not an
    auto-fetch: NHS Digital's actual file URLs are hashed per release
    and rotate; auto-fetch would silently grab stale or shifted data."""
    print("To refresh OpenCodeCounts source data, download the latest")
    print("release from each of the following NHS Digital pages and")
    print("save the CSV(s) under data/raw/opencodecounts/.")
    print()
    print("Filenames must contain a dataset keyword (snomed, icd10,")
    print("opcs) and a 4-digit year so the loader can route them.")
    print()
    for label, url in _NHS_DIGITAL_PAGES.values():
        print(f"  {label}")
        print(f"    {url}")
        print()
    print("Licence: Open Government Licence v3.0. Attribution required.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data", help="Path to data directory")
    parser.add_argument(
        "--refresh", action="store_true",
        help="Print NHS Digital download URLs instead of ingesting.",
    )
    args = parser.parse_args()

    if args.refresh:
        print_refresh_instructions()
    else:
        result = ingest_directory(args.data_dir)
        print(f"Done: {result}")
