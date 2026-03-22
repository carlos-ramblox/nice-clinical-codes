from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS
from app.api.routes import router

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


@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": app.version}
