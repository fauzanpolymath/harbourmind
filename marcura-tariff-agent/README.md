# HarbourMind — Agentic Port Tariff Calculation System

HarbourMind is an AI-powered agent that parses port tariff documents and calculates
accurate port disbursement costs for any vessel call.

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Pydantic |
| Agent | LangChain + Gemini 1.5 Pro |
| Document parsing | LlamaParse |
| Formula evaluation | numexpr |
| Containerisation | Docker / docker-compose |

## Project Structure

```
marcura-tariff-agent/
├── data/                   # Tariff PDF/DOCX documents (gitignored)
├── src/
│   ├── api/
│   │   ├── routes.py       # FastAPI endpoints
│   │   └── schemas.py      # Pydantic request/response models
│   ├── core/
│   │   ├── config.py       # Environment-based configuration
│   │   ├── models.py       # Domain models
│   │   └── logging.py      # Structured logging setup
│   ├── engine/
│   │   ├── agent.py        # LangChain + Gemini agent pipeline
│   │   ├── parser.py       # LlamaParse document ingestion
│   │   ├── calculator.py   # numexpr tariff formula evaluator
│   │   └── tools.py        # LangChain tool wrappers
│   └── utils/
│       ├── file_utils.py   # File I/O helpers
│       └── text_utils.py   # Text normalisation helpers
├── main.py                 # FastAPI application entry point
├── requirements.txt
├── .env.example
├── Dockerfile
└── docker-compose.yml
```

## Getting Started

### 1. Clone & configure

```bash
git clone https://github.com/fauzanpolymath/harbourmind.git
cd harbourmind/marcura-tariff-agent
cp .env.example .env
# Fill in GOOGLE_API_KEY and LLAMA_CLOUD_API_KEY in .env
```

### 2. Run locally (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

### 3. Run with Docker

```bash
docker-compose up --build
```

API will be available at `http://localhost:8000`.  
Interactive docs at `http://localhost:8000/docs`.

## Development Roadmap

- [ ] Implement LlamaParse ingestion pipeline (`engine/parser.py`)
- [ ] Build numexpr calculation engine (`engine/calculator.py`)
- [ ] Wire LangChain + Gemini agent (`engine/agent.py`)
- [ ] Define FastAPI endpoints (`api/routes.py`)
- [ ] Add end-to-end tests
- [ ] Production hardening (non-root Docker user, Gunicorn, secrets management)
