import logging
import os
from typing import Mapping

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS
from app.api.routes import router
from app.api.auth import router as auth_router
from app.api.codelists import router as codelists_router
from app.api.phenotypes import router as phenotypes_router
from app.api.public_codelists import router as public_codelists_router

logger = logging.getLogger(__name__)


def _langsmith_tracing_misconfigured(env: Mapping[str, str]) -> bool:
    """Return True iff tracing is enabled but no API key is set.

    Mirrors the langsmith client: accepts both LANGCHAIN_* and LANGSMITH_*
    var pairs, and treats {true,1,yes,on} (case-insensitive) as truthy.
    """
    truthy = {"true", "1", "yes", "on"}
    tracing_on = (
        env.get("LANGCHAIN_TRACING_V2", "").strip().lower() in truthy
        or env.get("LANGSMITH_TRACING", "").strip().lower() in truthy
    )
    has_key = bool(env.get("LANGCHAIN_API_KEY") or env.get("LANGSMITH_API_KEY"))
    return tracing_on and not has_key


if _langsmith_tracing_misconfigured(os.environ):
    logger.warning(
        "LangSmith tracing is enabled (LANGCHAIN_TRACING_V2 / LANGSMITH_TRACING) "
        "but no API key is set (LANGCHAIN_API_KEY / LANGSMITH_API_KEY). Traces "
        "will not be uploaded. Set the API key or disable tracing to silence "
        "this warning."
    )

app = FastAPI(
    title="NICE Clinical Code List Generator",
    description=(
        "Generates and validates clinical code lists (SNOMED CT, ICD-10) "
        "from public NHS data sources using a RAG pipeline."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(codelists_router, prefix="/api")
app.include_router(phenotypes_router, prefix="/api")
app.include_router(public_codelists_router, prefix="/api")


@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": app.version}
