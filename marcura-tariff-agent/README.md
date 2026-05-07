# HarbourMind — Agentic Port Tariff Calculator

HarbourMind extracts charge rules from any port tariff PDF and calculates the
itemised disbursement bill for any vessel, with full formula traceability for
every line item.

> *Any port. Any vessel.*

It is **dynamic by construction**: there are zero hardcoded rates, zero
hardcoded port names, and zero hardcoded calculator parameter shapes. The
system reads the rules straight from the uploaded tariff and computes against
the uploaded vessel certificate.

---

## 1. System overview

```
[ Tariff PDF ]                                                ┌──> Computed charges
[ Vessel PDF ] ─→ FastAPI ─→ LlamaParse ─→ Gemini agents ─→ ──┼──> Skipped rules (with reason)
                                                ↓             └──> Needs clarification (grouped)
                                  PerRuleCalculator
                                  (parallel · batched · safe-eval)
```

| Stage | Component | Responsibility |
|---|---|---|
| INPUT | `FastAPI /api/v1/calculate-from-pdfs` | Receives both PDFs over `multipart/form-data` |
| PARSING | `LlamaParse` | Converts each PDF to structured Markdown (tables preserved) |
| PARSING | `RuleExtractionAgent` | Extracts every charge rule from the tariff into `RuleStore` |
| PARSING | `VesselQueryParserAgent` | Extracts every available field from the certificate into `VesselProfile` |
| CALCULATION | `PerRuleCalculator` | For each rule, asks Gemini for `{formula, values, status}` and evaluates the formula deterministically |
| OUTPUT | three buckets + totals | `computed`, `not_applicable`, `needs_clarification`, plus subtotal/tax/grand-total |

Both PDF parsing and rule extraction are **content-hash cached on disk**, so
re-runs of the same tariff are instant. A cold first run takes ~3 minutes; a
hot re-run takes ~17 seconds.

---

## 2. Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI · Uvicorn (async) |
| Schemas | Pydantic v2 |
| LLM | `gemini-2.5-flash` via `langchain-google-genai` (thin chat-model wrapper) |
| Per-rule LLM mode | `thinking_budget=0`, batched 5 rules / call, dispatched 10-wide via `asyncio.Semaphore` |
| Document parser | LlamaParse Cloud (markdown output) |
| Formula evaluator | `simpleeval` (sandboxed math, no Python `eval`) |
| Cache | On-disk content-hash cache (SHA-256) |
| Container | Docker / docker-compose |

---

## 3. Project structure

```
marcura-tariff-agent/
├── data/
│   ├── .parse_cache/                           # PDF → markdown cache (gitignored)
│   └── .rule_cache/                            # parsed-text+port → RuleStore cache (gitignored)
├── src/
│   ├── api/
│   │   ├── main.py                             # FastAPI app + endpoints
│   │   └── models.py                           # API output models (ChargeOutput)
│   ├── core/
│   │   └── models.py                           # Domain models: VesselProfile, ExtractedRule,
│   │                                           # RuleStore, CalculatedCharge, CalculationResult
│   ├── engine/
│   │   ├── agents.py                           # VesselQueryParserAgent, RuleExtractionAgent
│   │   ├── pdf_parser.py                       # LlamaParse client + disk cache
│   │   └── per_rule_calculator.py              # The core calculation engine
│   ├── utils/
│   │   └── config.py                           # Env-driven Config (loads .hmenv.txt)
│   └── website/
│       └── index.html                          # Static UI served at /
├── docs/                                       # ADR, API reference, risk register
├── Dockerfile
├── docker-compose.yml
├── deploy.sh
├── requirements.txt
├── .hmenv.template                             # Template for local secrets (rename to .hmenv.txt)
└── README.md
```

12 Python files · ~2,150 lines of code. Every file is in active use; no dead
code in the tree.

---

## 4. Quick start

### 4.1 Prerequisites

* Python 3.11+
* A Gemini API key (Google AI Studio — free tier works for testing)
* A LlamaParse API key (LlamaIndex Cloud — free tier covers thousands of pages)

### 4.2 Local install

```bash
git clone <repository-url>
cd marcura-tariff-agent

python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .hmenv.template .hmenv.txt
# Edit .hmenv.txt and set GEMINI_API_KEY and LLAMAPARSE_API_KEY

python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

UI: `<base-url>/`
JSON API: `<base-url>/docs` (Swagger)

### 4.3 Docker

```bash
cp .hmenv.template .hmenv.txt   # populate keys
docker compose up --build
```

---

## 5. Endpoints (summary)

Full reference in [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md).

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Static UI |
| GET | `/health` | Liveness probe |
| POST | `/api/v1/calculate-from-pdfs` | Upload tariff + vessel PDFs, get itemised bill |
| GET | `/api/v1/logs?limit=N` | List recent calculations |
| GET | `/api/v1/logs/{calculation_id}` | Full detail of one calculation |

---

## 6. Worked example

```bash
curl -X POST <base-url>/api/v1/calculate-from-pdfs \
  -F "tariff_pdf=@<path-to-tariff>.pdf" \
  -F "vessel_pdf=@<path-to-vessel-certificate>.pdf"
```

Expected output (cold run ~3 min, hot run ~17 s):

```
subtotal       564,840.07 ZAR
tax (15%)       84,726.01 ZAR
grand_total    649,566.08 ZAR

charges:                          amount
  port_dues_basic                 309,853.11
  tug_assistance_per_service      147,073.74
  light_dues_all_other_vessels     60,062.04
  vts_charges                      33,345.00
  pilotage_basic_fee                9,972.72
  running_of_vessel_lines           4,533.46

needs_clarification (1 group):
  cargo_dues  ─ candidates: cargo_dues_dry_bulk, cargo_dues_breakbulk_iron_ore_exports, …
              missing: cargo_direction, cargo_route
```

Reproducibility: three back-to-back runs against the same inputs return the
**same subtotal to the cent**.

---

## 7. Engineering decisions

The major design calls — *why free-form formulas instead of fixed calculators,
why three output states instead of two, why batched parallel LLM calls, why
sibling-context grouping* — are catalogued in [`docs/ADR.md`](docs/ADR.md)
with the alternatives considered for each.

Known risks and their mitigations are in
[`docs/RISK_REGISTER.md`](docs/RISK_REGISTER.md).

---

## 8. Status

* Production-shape architecture: shipped.
* Performance optimisation (caching, parallelism, batching, thinking-mode off): shipped.
* Single-port end-to-end validation (Durban / SUDESTADA): passing within 3.1 % of reference.
* Multi-port validation: pending real-world tariff data from additional ports.

---

## 9. Licence

Copyright © 2026. All rights reserved.
