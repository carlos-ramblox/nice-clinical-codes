# OpenCodeCounts source data

Drop NHS Digital CSV releases here to populate the per-code usage column on
the search results page (T31).

## What goes in this directory

Three datasets, all CSV. Filenames must contain one of `snomed`, `icd10`,
`opcs` and a 4-digit year so the loader can route them — the rest of the
filename is up to you.

- **SNOMED CT primary care GP usage** (Aug-Jul, NHS Digital):
  https://digital.nhs.uk/data-and-information/publications/statistical/mi-snomed-code-usage-in-primary-care
- **ICD-10 inpatient HES** (Apr-Mar, FCE-level):
  https://digital.nhs.uk/data-and-information/publications/statistical/hospital-admitted-patient-care-activity
- **OPCS-4 inpatient HES** (Apr-Mar):
  https://digital.nhs.uk/data-and-information/publications/statistical/hospital-admitted-patient-care-activity

## Licence

NHS Digital data is published under the **Open Government Licence v3.0**;
attribution is required when redistributing. The project README §Data Sources
captures the attribution string we render in the response payload.

## Methodology

We follow the methodology of Bennett Institute's
[OpenCodeCounts](https://bennettoxford.github.io/opencodecounts/) package
but pull the source CSVs from NHS Digital directly rather than re-using
Bennett's harmonised `.rda` artefacts — keeps the licence chain
unambiguous (NHS Digital → us, with no intermediate derivative-work
question).

## Privacy / withholding

For the SNOMED primary-care dataset, NHS Digital rounds counts to the
nearest 10 and withholds counts of 1-4. The loader applies this rule
itself so the published values stay consistent if NHS Digital ever
change theirs. ICD-10 / OPCS-4 are unrounded with zero-usage codes
excluded from the source.

## Workflow

```bash
# Print the canonical NHS Digital landing pages.
python -m app.ingestion.opencodecounts --refresh

# Ingest whatever CSVs are present in this directory.
python -m app.ingestion.opencodecounts

# Or run the full ingest pipeline (QOF, OPCS, ICD-10, OpenCodelists,
# OpenCodeCounts) used by the Docker build:
python -m app.ingestion.run_all
```

## Why this directory ships in the repo (but the CSVs don't)

The CSVs are gitignored (see `.gitignore`). The directory itself is
present so the Docker `COPY` instruction has a target whether or not
the operator has staged a fresh CSV — a missing directory would fail
the build, but an empty one is fine and the ingest step warns and
continues.
