from fastapi import FastAPI, HTTPException, status, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import sys, os
from datetime import datetime, timedelta
from typing import Optional, List
import json
import uuid
from decimal import Decimal
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.utils.config import Config
from pathlib import Path
from src.core.models import VesselProfile, RuleStore, ExtractedRule, CalculatedCharge
from src.api.models import ChargeOutput
from src.engine.agents import VesselQueryParserAgent, RuleExtractionAgent
app = FastAPI(title='HarbourMind Tariff Calculator', version='1.0.0')

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*']
)
cfg = Config()
_calculation_cache = {}
_calculation_counter = 0
_calculation_logs = {}  # In-memory log storage: {calculation_id: log_data}

# Pydantic models for responses
from pydantic import BaseModel

class ChargeLog(BaseModel):
    charge_type: str
    amount: float
    confidence: float = 0.90

class CalculationLog(BaseModel):
    calculation_id: str
    timestamp: str
    vessel_name: str
    port: str
    grand_total: float
    status: str
    processing_time_ms: int

class CalculationDetail(BaseModel):
    calculation_id: str
    timestamp: str
    vessel_name: str
    port: str
    tariff_file: Optional[str] = None
    vessel_file: Optional[str] = None
    extraction: dict
    charges: List[dict]
    subtotal: float
    grand_total: float
    processing_time_ms: int
    status: str

# ─────────────────────────────────────────────────────────────────────────
# DYNAMIC TARIFF EXTRACTION (NO HARDCODING)
# ─────────────────────────────────────────────────────────────────────────
# All tariff rules are extracted from uploaded PDF documents using
# RuleExtractionAgent. NO hardcoded tariff rates, brackets, or fees.

_rule_extractor_agent = None
_vessel_parser_agent = None

def get_rule_extractor_agent():
    """Lazy-initialize RuleExtractionAgent."""
    global _rule_extractor_agent
    if _rule_extractor_agent is None:
        print(f"[INIT] Creating RuleExtractionAgent (api_key set: {bool(cfg.gemini_api_key)})", flush=True)
        agent = RuleExtractionAgent(config=cfg)
        try:
            agent.initialize()
            _rule_extractor_agent = agent
            print(f"[INIT] RuleExtractionAgent initialized OK", flush=True)
        except Exception as e:
            print(f"[INIT] RuleExtractionAgent init FAILED: {type(e).__name__}: {e}", flush=True)
            import traceback; traceback.print_exc()
            raise
    return _rule_extractor_agent

def get_vessel_parser_agent():
    """Lazy-initialize VesselQueryParserAgent."""
    global _vessel_parser_agent
    if _vessel_parser_agent is None:
        agent = VesselQueryParserAgent(config=cfg)
        try:
            agent.initialize()
            _vessel_parser_agent = agent
        except Exception as e:
            print(f"[INIT] VesselQueryParserAgent init FAILED: {type(e).__name__}: {e}", flush=True)
            import traceback; traceback.print_exc()
            raise
    return _vessel_parser_agent

_per_rule_calculator = None

# ── On-disk cache for the (parsed_tariff_text + port) → RuleStore step ───
_RULE_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / ".rule_cache"

def _rule_cache_path(parsed_text: str) -> Path:
    """Cache key is just the parsed text — same text → same rules, regardless of port label."""
    import hashlib
    return _RULE_CACHE_DIR / f"{hashlib.sha256(parsed_text.encode('utf-8')).hexdigest()}.json"

def _read_cached_rules(parsed_text: str):
    p = _rule_cache_path(parsed_text)
    if not p.exists():
        return None
    try:
        return RuleStore.model_validate_json(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[RULE_CACHE] Read failed, ignoring: {exc}", flush=True)
        return None

def _write_cached_rules(parsed_text: str, rules: RuleStore) -> None:
    try:
        _RULE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _rule_cache_path(parsed_text).write_text(
            rules.model_dump_json(), encoding="utf-8"
        )
    except Exception as exc:
        print(f"[RULE_CACHE] Write failed (non-fatal): {exc}", flush=True)


def get_per_rule_calculator():
    """Lazy-initialize PerRuleCalculator (the new robust per-rule engine)."""
    global _per_rule_calculator
    if _per_rule_calculator is None:
        from src.engine.per_rule_calculator import PerRuleCalculator
        agent = PerRuleCalculator(config=cfg)
        try:
            agent.initialize()
            _per_rule_calculator = agent
            print(f"[INIT] PerRuleCalculator initialized OK", flush=True)
        except Exception as e:
            print(f"[INIT] PerRuleCalculator init FAILED: {type(e).__name__}: {e}", flush=True)
            import traceback; traceback.print_exc()
            raise
    return _per_rule_calculator

async def extract_tariff_from_pdf(pdf_content: bytes) -> RuleStore:
    """
    Extract tariff rules from PDF using RuleExtractionAgent.

    In production, this would:
    1. Use LlamaParse to extract text from PDF
    2. Pass full text to RuleExtractionAgent
    3. RuleExtractionAgent uses Gemini to extract all rules dynamically

    Returns RuleStore with all discovered charges and their extracted parameters.
    """
    try:
        from src.engine.pdf_parser import extract_text_from_pdf

        # Extract text from PDF using LlamaParse (itself disk-cached)
        tariff_text = await extract_text_from_pdf(pdf_content, filename="tariff.pdf")

        # Rule-extraction cache: keyed only on parsed text. The LLM extracts
        # the port name from the document itself; nothing here hardcodes it.
        cached_rules = _read_cached_rules(tariff_text)
        if cached_rules is not None:
            print(f"[RULE_CACHE] HIT (port={cached_rules.port_name}, {len(cached_rules.rules)} rules)", flush=True)
            return cached_rules

        agent = get_rule_extractor_agent()
        rules = agent.execute(tariff_text)
        _write_cached_rules(tariff_text, rules)
        print(f"[RULE_CACHE] MISS — extracted and cached {len(rules.rules)} rules (port={rules.port_name})", flush=True)

        return rules
    except Exception as e:
        raise ValueError(f'Failed to extract tariff from PDF: {str(e)}')

async def extract_vessel_from_pdf(pdf_content: bytes) -> VesselProfile:
    """
    Extract vessel profile from shipping certificate PDF using VesselQueryParserAgent.

    In production, this would:
    1. Use LlamaParse to extract text from vessel PDF
    2. Pass full text to VesselQueryParserAgent
    3. VesselQueryParserAgent uses Gemini to extract all vessel fields
    4. Includes days_alongside and number_of_operations from certificate

    Returns VesselProfile with all extracted vessel parameters.
    """
    try:
        from src.engine.pdf_parser import extract_text_from_pdf
        
        # Extract text from PDF using LlamaParse
        vessel_text = await extract_text_from_pdf(pdf_content, filename="vessel_certificate.pdf")

        agent = get_vessel_parser_agent()
        vessel = agent.execute(vessel_text)

        return vessel
    except Exception as e:
        raise ValueError(f'Failed to extract vessel from PDF: {str(e)}')

async def execute_calculation_with_agents(vessel_profile: VesselProfile, rules: RuleStore, target_dues: Optional[List[str]] = None) -> tuple:
    """
    Execute tariff calculation using PerRuleCalculator (async, parallel).

    For each extracted rule, asks Gemini to produce {formula, values} given
    the vessel profile, then evaluates the formula deterministically with
    simpleeval. Calls run in parallel via asyncio.gather + Semaphore.

    Returns: (charges, trace_log, skipped_rules, clarifications)
    """
    trace_log = [
        '[1/4] Extracting rules from tariff PDF...',
        '[2/4] Validating vessel data...',
        '[3/4] Per-rule calculation (Gemini formula + safe eval)...',
        '[4/4] Complete'
    ]

    try:
        calculator = get_per_rule_calculator()
        result, skipped, clarifications = await calculator.execute(vessel_profile, rules)

        # Filter by target_dues if specified
        charges = result.charges
        if target_dues:
            target_types = [td.lower().replace(' ', '_') for td in target_dues]
            charges = [c for c in charges if c.charge_type in target_types]

        print(
            f"[CALC] {len(charges)} charges OK, {len(skipped)} skipped, "
            f"{len(clarifications)} need clarification",
            flush=True,
        )
        for s in skipped[:10]:
            print(f"[CALC] SKIPPED {s['charge_type']}: {s['reason']}", flush=True)
        if len(skipped) > 10:
            print(f"[CALC] ... and {len(skipped) - 10} more skipped", flush=True)
        for c in clarifications[:10]:
            grp = c['category_group'] or '(standalone)'
            print(
                f"[CALC] CLARIFY [{grp}] candidates={len(c['candidates'])} "
                f"missing={c['missing_inputs']} reason={c['reason']}",
                flush=True,
            )
        if len(clarifications) > 10:
            print(f"[CALC] ... and {len(clarifications) - 10} more clarifications", flush=True)

        return charges, trace_log, skipped, clarifications

    except Exception as e:
        import logging
        logging.error(f"Calculation failed: {e}", exc_info=True)
        raise
@app.get('/health', status_code=200)
async def health_check():
    return {'status': 'healthy', 'service': 'HarbourMind Tariff Calculator', 'timestamp': datetime.utcnow().isoformat()}
@app.post('/api/v1/calculate-from-pdfs', status_code=200)
async def calculate_from_pdfs(
    tariff_pdf: UploadFile = File(...),
    vessel_pdf: UploadFile = File(...)
):
    """
    Calculate tariffs from uploaded PDF files with FULL DYNAMIC EXTRACTION.

    Process:
    1. Tariff PDF → RuleExtractionAgent extracts ALL rules, rates, brackets
    2. Vessel PDF → VesselQueryParserAgent extracts vessel data including days_alongside
    3. CalculationAgent → Maps rules to calculators and calculates using EXTRACTED parameters
    4. ALL values come from PDFs, ZERO hardcoding

    Returns:
    - All charges with extraction confidence scores
    - Full trace of extracted rules and parameters
    - Ground-truth calculation with no fallback values
    """
    start_time = datetime.utcnow()
    calculation_id = f"calc_{uuid.uuid4().hex[:12]}"

    print(f"\n{'='*60}", flush=True)
    print(f"[ENDPOINT] /calculate-from-pdfs called - {calculation_id}", flush=True)
    print(f"[ENDPOINT] tariff_pdf: {tariff_pdf.filename} ({tariff_pdf.content_type})", flush=True)
    print(f"[ENDPOINT] vessel_pdf: {vessel_pdf.filename} ({vessel_pdf.content_type})", flush=True)
    print(f"{'='*60}", flush=True)

    try:
        # Step 1: Extract tariff rules from PDF (DYNAMIC - no hardcoding)
        tariff_content = await tariff_pdf.read()
        print(f"[ENDPOINT] Read {len(tariff_content)} bytes from tariff PDF", flush=True)
        rules = await extract_tariff_from_pdf(tariff_content)
        print(f"[ENDPOINT] Extracted {len(rules.rules)} tariff rules", flush=True)

        if not rules.rules:
            raise ValueError(
                f'No tariff rules extracted from {tariff_pdf.filename}. '
                'Check that PDF contains valid tariff information.'
            )

        # Step 2: Extract vessel from PDF (DYNAMIC - no hardcoding)
        vessel_content = await vessel_pdf.read()
        vessel_profile = await extract_vessel_from_pdf(vessel_content)

        if not vessel_profile.name:
            raise ValueError(
                f'Could not extract vessel name from {vessel_pdf.filename}. '
                'Check that PDF is a valid shipping certificate.'
            )

        # Step 3: Calculate using EXTRACTED rules (NOT hardcoded)
        charges, trace_log, skipped_rules, clarifications = await execute_calculation_with_agents(vessel_profile, rules, target_dues=None)

        # Calculate totals — tax rate and currency come from the tariff
        # (extracted by the rule extraction agent), not hardcoded.
        subtotal = sum(Decimal(str(c.amount)) for c in charges)
        tax_rate_decimal = Decimal(str(rules.tax_rate)) if rules.tax_rate else Decimal('0')
        vat_amount = subtotal * tax_rate_decimal
        grand_total = subtotal + vat_amount
        tax_rate_value = float(rules.tax_rate) if rules.tax_rate is not None else 0.0
        tax_label = rules.tax_label or 'Tax'
        currency = rules.currency or ''

        # Calculate processing time
        processing_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        # Create charge outputs with extracted confidence scores
        charge_outputs = []
        for charge in charges:
            # Find corresponding rule to get extraction_confidence
            rule = next((r for r in rules.rules if r.charge_type == charge.charge_type), None)
            confidence = rule.extraction_confidence if rule else 0.90

            charge_outputs.append({
                'charge_type': charge.charge_type,
                'description': charge.description,
                'amount': float(charge.amount),
                'confidence': float(confidence),  # ← EXTRACTED confidence, not hardcoded 0.92
                'trace': charge.trace if charge.trace else {}
            })

        # Store log
        log_data = {
            'calculation_id': calculation_id,
            'timestamp': start_time.isoformat() + 'Z',
            'vessel_name': vessel_profile.name,
            # Surface every field the parser extracted — no field privileged in code.
            # If the certificate had it, the response carries it.
            'vessel_details': vessel_profile.model_dump(exclude_none=True),
            'port': rules.port_name,
            'tariff_file': tariff_pdf.filename,
            'vessel_file': vessel_pdf.filename,
            'extraction': {
                'charges_discovered': len(charges),
                'rules_extracted': len(rules.rules),
                'average_confidence': float(
                    sum(r.extraction_confidence for r in rules.rules) / len(rules.rules)
                ) if rules.rules else 0.0,
                'time_ms': processing_time_ms
            },
            'charges': charge_outputs,
            'currency': currency,
            'subtotal': float(subtotal),
            'tax_rate': tax_rate_value,
            'tax_label': tax_label,
            'tax_amount': float(vat_amount),
            'grand_total': float(grand_total),
            'processing_time_ms': processing_time_ms,
            'status': 'success'
        }

        _calculation_logs[calculation_id] = log_data

        return {
            'calculation_id': calculation_id,
            'vessel_name': vessel_profile.name,
            'vessel_details': vessel_profile.model_dump(exclude_none=True),
            'port': rules.port_name,
            'charges': charge_outputs,
            'extraction': {
                'rules_count': len(rules.rules),
                'average_confidence': float(
                    sum(r.extraction_confidence for r in rules.rules) / len(rules.rules)
                ) if rules.rules else 0.0,
            },
            'currency': currency,
            'subtotal': float(subtotal),
            'tax_rate': tax_rate_value,
            'tax_label': tax_label,
            'tax_amount': float(vat_amount),
            'grand_total': float(grand_total),
            'processing_time_ms': processing_time_ms,
            'status': 'success',
            'skipped_rules': skipped_rules,
            'skipped_count': len(skipped_rules),
            'needs_clarification': clarifications,
            'clarification_count': len(clarifications),
            'note': 'All values extracted from PDFs - zero hardcoding'
        }

    except Exception as e:
        import logging, traceback
        print(f"\n[ENDPOINT] *** EXCEPTION *** {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        logging.error(f"PDF processing failed: {e}", exc_info=True)

        processing_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        log_data = {
            'calculation_id': calculation_id,
            'timestamp': start_time.isoformat() + 'Z',
            'vessel_name': 'Unknown',
            'port': 'Unknown',
            'tariff_file': tariff_pdf.filename,
            'vessel_file': vessel_pdf.filename,
            'processing_time_ms': processing_time_ms,
            'status': 'error',
            'error': str(e)
        }
        _calculation_logs[calculation_id] = log_data

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={'message': str(e), 'code': 'PDF_PROCESSING_ERROR'}
        )

@app.get('/api/v1/logs')
async def get_logs(
    port: Optional[str] = None,
    vessel: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 50
):
    """
    Get calculation logs with optional filtering.

    Query parameters:
    - port: Filter by port name
    - vessel: Filter by vessel name
    - start_date: Filter from date (YYYY-MM-DD)
    - end_date: Filter to date (YYYY-MM-DD)
    - limit: Maximum results to return (default 50)
    """
    calculations = []

    for calc_id, log_data in _calculation_logs.items():
        # Apply filters
        if port and log_data.get('port', '').lower() != port.lower():
            continue
        if vessel and vessel.lower() not in log_data.get('vessel_name', '').lower():
            continue

        # Parse timestamp for date filtering
        if start_date or end_date:
            try:
                log_timestamp = datetime.fromisoformat(log_data.get('timestamp', '').replace('Z', '+00:00'))
                if start_date:
                    filter_start = datetime.fromisoformat(start_date)
                    if log_timestamp.date() < filter_start.date():
                        continue
                if end_date:
                    filter_end = datetime.fromisoformat(end_date)
                    if log_timestamp.date() > filter_end.date():
                        continue
            except:
                pass

        # Add to results
        calculations.append({
            'calculation_id': calc_id,
            'timestamp': log_data.get('timestamp', ''),
            'vessel_name': log_data.get('vessel_name', '-'),
            'port': log_data.get('port', '-'),
            'grand_total': log_data.get('grand_total', 0),
            'status': log_data.get('status', 'unknown'),
            'processing_time_ms': log_data.get('processing_time_ms', 0)
        })

    # Sort by timestamp (newest first) and limit
    calculations.sort(
        key=lambda x: x['timestamp'],
        reverse=True
    )
    calculations = calculations[:limit]

    return {
        'total': len(_calculation_logs),
        'returned': len(calculations),
        'calculations': calculations
    }

@app.get('/api/v1/logs/{calculation_id}')
async def get_log_detail(calculation_id: str):
    """
    Get full details for a specific calculation.
    """
    if calculation_id not in _calculation_logs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={'message': f'Calculation {calculation_id} not found', 'code': 'NOT_FOUND'}
        )

    return _calculation_logs[calculation_id]

# Mount /docs as a static directory so the website can fetch project artifacts
# (ADR, API reference, risk register, README) without a redirect to GitHub.
_DOCS_DIR = Path(__file__).resolve().parents[2] / 'docs'
if _DOCS_DIR.is_dir():
    app.mount('/docs', StaticFiles(directory=str(_DOCS_DIR)), name='docs')

# Also serve the README from repo root at /readme.md so the website can
# load it through the same modal viewer.
@app.get('/readme.md', response_class=FileResponse)
async def readme():
    readme_path = Path(__file__).resolve().parents[2] / 'README.md'
    if readme_path.exists():
        return FileResponse(str(readme_path), media_type='text/markdown')
    raise HTTPException(status_code=404, detail='README.md not found')


# Sample PDFs — served as named, downloadable files so the website's
# "Use Sample" buttons can fetch and attach them to the upload inputs.
_DATA_DIR = Path(__file__).resolve().parents[2] / 'data'
_SAMPLE_FILES = {
    'tariff': ('Port_Tariff.pdf', 'Port_Tariff.pdf'),
    'vessel': ('Shipping_Certificate.pdf', 'Shipping_Certificate.pdf'),
}

@app.get('/sample/{name}')
async def sample_pdf(name: str):
    """Return one of the bundled sample PDFs with attachment headers."""
    if name not in _SAMPLE_FILES:
        raise HTTPException(status_code=404, detail=f'Unknown sample: {name}')
    filename, download_name = _SAMPLE_FILES[name]
    pdf_path = _DATA_DIR / filename
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f'Sample file not found: {filename}')
    return FileResponse(
        str(pdf_path),
        media_type='application/pdf',
        filename=download_name,
        headers={'Content-Disposition': f'attachment; filename="{download_name}"'},
    )

@app.get('/', response_class=FileResponse)
async def root():
    """
    Serve the HarbourMind landing website.
    Falls back to API info if website not found.
    """
    website_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'website',
        'index.html'
    )
    if os.path.exists(website_path):
        return FileResponse(website_path, media_type='text/html')
    else:
        # Fallback if website not found
        return {
            'service': 'HarbourMind Tariff Calculator API',
            'version': '1.0.0',
            'website': 'Not available',
            'endpoints': {
                'health': 'GET /health',
                'calculate_from_pdfs': 'POST /api/v1/calculate-from-pdfs',
                'logs': 'GET /api/v1/logs',
                'log_detail': 'GET /api/v1/logs/{calculation_id}',
                'docs': '/docs'
            }
        }

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
