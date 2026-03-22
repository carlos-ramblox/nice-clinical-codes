# Data

Raw data files and reference code lists used for evaluation.

## Structure

- `raw/` — Source files downloaded from NHS England, QOF, etc.
- `gold_standard/` — Validated code lists used as ground truth for testing

Large files (`.xlsm`, `.xlsx`, `.csv`) are gitignored. Download them manually or run the ingestion scripts.

## Files

### raw/Business_Rules_Combined_Change_Log_QOF+2024-25_v49.1.xlsm

QOF (Quality and Outcomes Framework) business rules for 2024-25. Contains the clinical codes required to meet each QOF indicator across primary care conditions (diabetes, hypertension, asthma, etc.).

- **Source:** [NHS England QOF Business Rules](https://digital.nhs.uk/data-and-information/data-collections-and-data-sets/data-collections/quality-and-outcomes-framework-qof)
- **Version:** v49.1 (2024-25)
- **Format:** Excel with macros (.xlsm)
- **Size:** ~1.7MB
- **Used by:** `backend/app/ingestion/ingest_qof.py` (NICE-017)
