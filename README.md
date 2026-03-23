# NICE Clinical Code List Generator

Tool for generating and validating clinical code lists (SNOMED CT, ICD-10) from public NHS data sources. Built for the NICE healthcare data analytics team.

Given a clinical condition (e.g. "type 2 diabetes with hypertension"), the tool retrieves relevant codes from multiple public sources, enriches them with UMLS relationships, scores each code for inclusion/exclusion, and presents the results with full provenance.

## Architecture

```
User → Frontend (Next.js)
         │
         ▼  /api/*
       Backend (FastAPI)
         │
         ▼
       LangGraph Pipeline
         │
         ├─→ Query Parser (Claude API)
         │
         ├─→ Retrievers (parallel)
         │     ├── OMOPHub (SNOMED/ICD-10)
         │     ├── QOF Business Rules
         │     ├── OpenCodelists
         │     └── ChromaDB (semantic search)
         │
         ├─→ Result Merger + Dedup
         ├─→ UMLS Enrichment (synonyms, narrower, siblings)
         ├─→ ML Classifier (scikit-learn)
         ├─→ LLM Reasoning (Claude API)
         ├─→ Human Review Gate
         └─→ Output Assembly
```

## Tech Stack

- **Backend:** Python, FastAPI, LangGraph
- **Frontend:** Next.js, TypeScript, Tailwind CSS
- **Vector DB:** ChromaDB with PubMedBERT embeddings
- **LLM:** Claude API (Anthropic)
- **ML:** scikit-learn (code relevance classifier)
- **Data:** NHS England refsets, QOF business rules, OpenCodelists, UMLS
- **Deployment:** AWS ECS Fargate, Docker

## Getting Started

### Prerequisites

- Python 3.12+
- Node.js 20+
- Docker (optional, for containerised setup)

### Local Setup

1. Clone the repo:

```bash
git clone <repo-url>
cd nice-clinical-codes
```

2. Set up the backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

3. Set up the frontend:

```bash
cd frontend
npm install
```

4. Create your `.env` file from the template:

```bash
cp .env.example .env
# Edit .env and add your API keys
```

5. Run both services:

```bash
# Terminal 1 — backend
cd backend
uvicorn app.main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend
npm run dev
```

Backend: http://localhost:8000 (API docs at /docs)
Frontend: http://localhost:3000

### Docker Setup

```bash
# Copy env template and add your keys
cp .env.example .env

# Start everything
docker-compose up --build
```

## Environment Variables

| Variable | Description | Required |
|----------|------------|----------|
| `OMOPHUB_API_KEY` | OMOPHub API key for SNOMED/ICD-10 queries | Yes |
| `UMLS_API_KEY` | UMLS Metathesaurus API key | Yes |
| `ANTHROPIC_API_KEY` | Anthropic Claude API key | Yes |
| `BACKEND_HOST` | Backend bind address | No (default: 0.0.0.0) |
| `BACKEND_PORT` | Backend server port | No (default: 8000) |
| `CORS_ORIGINS` | Allowed CORS origins | No (default: http://localhost:3000) |
| `CHROMA_PERSIST_DIR` | ChromaDB storage path | No (default: ./chromadb_data) |
| `CHROMA_COLLECTION_NAME` | ChromaDB collection name | No (default: clinical_codes) |
| `DATABASE_URL` | SQLite database path | No (default: sqlite:///./data/codes.db) |
| `EMBEDDING_MODEL` | Sentence transformer model | No (default: NeuML/pubmedbert-base-embeddings) |
| `LLM_MODEL` | Claude model ID | No (default: claude-sonnet-4-20250514) |
| `RETRIEVAL_TOP_K` | Max results per retrieval source | No (default: 50) |
| `CONFIDENCE_THRESHOLD` | Min confidence for auto-include | No (default: 0.5) |
| `UMLS_EXPAND` | Enable UMLS enrichment | No (default: yes) |

## Data Sources

| Source | Type | Description |
|--------|------|------------|
| [OMOPHub](https://omophub.com) | API | SNOMED CT and ICD-10 concept search |
| [QOF Business Rules](https://digital.nhs.uk/data-and-information/data-collections-and-data-sets/data-collections/quality-and-outcomes-framework-qof) | Excel | NHS primary care quality indicator code sets |
| [OpenCodelists](https://www.opencodelists.org) | CSV + scraping | Published, peer-reviewed clinical code lists |
| [UMLS Metathesaurus](https://uts.nlm.nih.gov) | API | Concept relationships, synonyms, hierarchies |
| [NHS England Refsets](https://digital.nhs.uk/services/terminology-and-classifications/snomed-ct) | CSV | Curated SNOMED reference sets |

## Project Structure

```
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI routes
│   │   ├── graph/          # LangGraph pipeline
│   │   │   ├── nodes/      # Pipeline nodes (retrievers, reasoning, etc.)
│   │   │   └── state.py    # Typed pipeline state
│   │   ├── db/             # ChromaDB and SQLite
│   │   ├── ingestion/      # Data source parsers
│   │   ├── ml/             # Classifier training and inference
│   │   └── evaluation/     # Metrics (P/R/F1)
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/            # Next.js pages
│   │   └── lib/            # API client
│   └── Dockerfile
├── data/
│   ├── raw/                # Source data files (gitignored)
│   └── gold_standard/      # Reference code lists for evaluation
├── notebooks/              # Jupyter notebooks for exploration
├── infra/                  # AWS deployment configs
├── docker-compose.yml
└── .env.example
```

## Team

Cambridge University Data Science Career Accelerator — Group 3

## License

MIT
