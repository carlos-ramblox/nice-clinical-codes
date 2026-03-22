"""App configuration. All settings loaded from env vars."""

import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
OMOPHUB_API_KEY = os.getenv("OMOPHUB_API_KEY", "")
UMLS_API_KEY = os.getenv("UMLS_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# App
BACKEND_HOST = os.getenv("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")

# ChromaDB
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chromadb_data")
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "clinical_codes")

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/codes.db")

# Pipeline
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "NeuML/pubmedbert-base-embeddings")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "50"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.5"))
UMLS_EXPAND = os.getenv("UMLS_EXPAND", "yes").strip().lower() == "yes"

# OMOPHub
OMOPHUB_PAGE_SIZE = int(os.getenv("OMOPHUB_PAGE_SIZE", "20"))
OMOPHUB_VOCABULARIES = {
    "SNOMED": "SNOMED CT",
    "ICD10": "ICD-10 (WHO)",
}
