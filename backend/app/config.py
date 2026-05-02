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
UMLS_EXPAND = os.getenv("UMLS_EXPAND", "yes").strip().lower() == "yes"

# OMOPHub
OMOPHUB_PAGE_SIZE = int(os.getenv("OMOPHUB_PAGE_SIZE", "20"))
OMOPHUB_VOCABULARIES = {
    "SNOMED": "SNOMED CT",
    "ICD10": "ICD-10 (WHO)",
    "OPCS4": "OPCS-4",
}
