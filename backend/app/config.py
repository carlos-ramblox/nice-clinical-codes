"""App configuration. All settings loaded from env vars."""

import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
OMOPHUB_API_KEY = os.getenv("OMOPHUB_API_KEY", "")
UMLS_API_KEY = os.getenv("UMLS_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# App
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")

# ChromaDB
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chromadb_data")
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "clinical_codes")

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/codes.db")

# HITL (codelists, reviews, audit) — kept separate from reference codes
# because the reference DB is baked into the Docker image on build, while
# HITL state is mutable per-deployment. Production: back this with RDS/Postgres.
HITL_DATABASE_URL = os.getenv("HITL_DATABASE_URL", "sqlite:///./data/hitl.db")

# Session cookie secret — demo default; set in prod via SSM
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-only-change-me")

# Pipeline
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "NeuML/pubmedbert-base-embeddings")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
LLM_SCORING_MODEL = os.getenv("LLM_SCORING_MODEL", "claude-haiku-4-5-20251001")
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "50"))
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", "100"))
# T37g: reserved slots per drug vocabulary inside MAX_CANDIDATES, only
# applied when at least one parsed condition has domain="Drug". Without
# it, single-source dm+d / BNF rows are displaced by multi-source SNOMED
# from OMOPHub + ChromaDB and never reach the scorer.
DRUG_VOCAB_QUOTA = int(os.getenv("DRUG_VOCAB_QUOTA", "15"))
UMLS_EXPAND = os.getenv("UMLS_EXPAND", "yes").strip().lower() == "yes"

# OMOPHub
OMOPHUB_PAGE_SIZE = int(os.getenv("OMOPHUB_PAGE_SIZE", "20"))
# OMOP-side vocabulary IDs surfaced by OMOPHub.concepts.get_by_code.
# MUST NOT contain non-OMOP vocabularies (dm+d, BNF, etc.) — those
# poison the inverse map built by concept_id_enricher and burn API
# slots on guaranteed misses. For UI / display purposes (e.g. rendering
# "dm+d" or future "RxNorm" labels) use VOCABULARY_DISPLAY_LABELS.
OMOPHUB_VOCABULARIES = {
    "SNOMED": "SNOMED CT",
    "ICD10": "ICD-10 (WHO)",
    "OPCS4": "OPCS-4",
}

# Canonical display labels for every vocabulary the pipeline can emit.
# Superset of OMOPHUB_VOCABULARIES. Use this when you need the full
# vocabulary set (UI labels, export filters); use OMOPHUB_VOCABULARIES
# only for the OMOPHub API boundary.
VOCABULARY_DISPLAY_LABELS = {
    **OMOPHUB_VOCABULARIES,
    "DMD": "dm+d",
    "BNF": "BNF",
}

# HDR UK Phenotype Library -- anonymous public read, no API key required.
# Consumed by the read-side discovery service (T34) and the post-hoc
# cross-reference panel (T35). The retriever-shape integration that
# originally lived behind these env vars (T13) was reverted in T36 after
# a persona audit established that phenotype libraries are designed for
# browse-and-adjudicate use, not retrieve-and-merge use; see EVALUATION.md
# for the methodology write-up.
HDR_UK_BASE_URL = os.getenv("HDR_UK_BASE_URL", "https://phenotypes.healthdatagateway.org")
HDR_UK_TOP_K_PHENOTYPES = int(os.getenv("HDR_UK_TOP_K_PHENOTYPES", "3"))
# When true, the LLM scope-fit judge filters HDR UK candidate phenotypes
# by clinical-scope match against the user query before they are surfaced
# to the discovery sidebar (T34) or used for cross-reference (T35). The
# judge guards against HDR UK's full-text search-quality failure mode
# where a query like "HIV" returns paediatric Asthma phenotypes by
# metadata keyword overlap.
HDR_UK_USE_JUDGE = os.getenv("HDR_UK_USE_JUDGE", "yes").strip().lower() == "yes"
# Model id for the relevance judge. Defaults to the code-scoring model
# (Haiku 4.5) -- fast, cheap, validated. Override to a stronger model
# (e.g. the query-parser id) if a future benchmark shows borderline
# scope-fit decisions need more reasoning depth.
HDR_UK_JUDGE_MODEL = os.getenv("HDR_UK_JUDGE_MODEL", LLM_SCORING_MODEL)
