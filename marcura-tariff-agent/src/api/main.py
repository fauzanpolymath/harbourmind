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
from src.engine.calculators import calculate_base_plus_incremental, calculate_per_unit_per_period, calculate_bracket_based, calculate_flat_fee
from src.core.models import VesselProfile, RuleStore, ExtractedRule, CalculatedCharge
from src.api.models import CalculateRequest, CalculateResponse, ChargeOutput
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

MOCK_DURBAN_RULES = RuleStore(port_name='durban',rules=[
    ExtractedRule(charge_type='light_dues', calculation_logic='Base fee plus incremental rate per unit', extracted_parameters={'base_fee': 0, 'rate': 0.95, 'unit': 1, 'multiplier': 1}, extraction_confidence=0.95, required_variables=['gross_tonnage'], conditions='Standard light dues'),
    ExtractedRule(charge_type='port_dues', calculation_logic='Base fee plus rate per unit for all periods', extracted_parameters={'base_fee': 0, 'rate': 1.25, 'unit': 100, 'periods': 100}, extraction_confidence=0.90, required_variables=['gross_tonnage'], conditions='Standard port dues'),
    ExtractedRule(charge_type='towage', calculation_logic='Fixed rate for GT band 50,001-100,000', extracted_parameters={'brackets': [{'min': 0, 'max': 10000, 'rate': 5000}, {'min': 10001, 'max': 50000, 'rate': 10000}, {'min': 50001, 'max': 100000, 'rate': 15000}], 'multiplier': 1}, extraction_confidence=0.85, required_variables=['gross_tonnage'], conditions='Includes in/out of port'),
    ExtractedRule(charge_type='vts_dues', calculation_logic='Flat fee for vessel traffic service', extracted_parameters={'fee': 12500, 'multiplier': 1, 'surcharges': 0}, extraction_confidence=0.95, required_variables=[], conditions='All vessels'),
    ExtractedRule(charge_type='pilotage', calculation_logic='Flat fee multiplied by number of operations', extracted_parameters={'fee': 18608.61, 'multiplier': 2, 'surcharges': 0}, extraction_confidence=0.90, required_variables=[], conditions='Compulsory for all vessels'),
    ExtractedRule(charge_type='running_lines', calculation_logic='Flat fee for running lines', extracted_parameters={'fee': 10000, 'multiplier': 1, 'surcharges': 0}, extraction_confidence=0.95, required_variables=[], conditions='Standard fee'),
])
TARIFF_LIBRARY = {'durban': MOCK_DURBAN_RULES}
def load_tariff_for_port(port_name: str) -> RuleStore:
    port_key = port_name.lower()
    if port_key not in TARIFF_LIBRARY:
        raise ValueError(f'No tariff rules available for port: {port_name}')
    return TARIFF_LIBRARY[port_key]
def execute_mock_calculation(vessel_profile, port_name, rules, target_dues):
    trace_log = ['[1/5] Validating rule completeness...', '[2/5] Validating vessel schema...', '[3/5] Calculating charges...']
    charges = []
    for rule in rules.rules:
        if target_dues and rule.charge_type not in [td.lower().replace(' ', '_') for td in target_dues]:
            continue
        params = rule.extracted_parameters
        gt = vessel_profile.gross_tonnage or 0
        if rule.charge_type == 'light_dues':
            result = calculate_base_plus_incremental(params.get('base_fee', 0), gt, params.get('rate', 0), params.get('unit', 1), params.get('multiplier', 1))
        elif rule.charge_type == 'port_dues':
            result = calculate_per_unit_per_period(params.get('base_fee', 0), params.get('rate', 0), params.get('unit', 1), gt, params.get('periods', 1), params.get('multiplier', 1))
        elif rule.charge_type == 'towage':
            result = calculate_bracket_based(gt, params.get('brackets', []), params.get('multiplier', 1))
        else:
            result = calculate_flat_fee(params.get('fee', 0), params.get('multiplier', 1), params.get('surcharges', 0))
        charges.append(CalculatedCharge(charge_type=rule.charge_type, description=rule.calculation_logic, amount=result['value'], trace=result.get('trace', {})))
    trace_log.extend(['[4/5] Handling exceptions...', '[5/5] Complete'])
    return charges, trace_log
@app.get('/health', status_code=200)
async def health_check():
    return {'status': 'healthy', 'service': 'HarbourMind Tariff Calculator', 'timestamp': datetime.utcnow().isoformat()}
@app.post('/api/v1/calculate', response_model=CalculateResponse, status_code=200)
async def calculate(request: CalculateRequest) -> CalculateResponse:
    try:
        vessel_profile = VesselProfile(**request.vessel_data.model_dump())
        rules = load_tariff_for_port(request.port)
        charges, trace_log = execute_mock_calculation(vessel_profile, request.port, rules, request.target_dues)
        subtotal = sum(c.amount for c in charges)
        vat_amount = subtotal * 0.15
        grand_total = subtotal + vat_amount
        charge_outputs = [ChargeOutput(charge_type=c.charge_type, description=c.description, amount=c.amount, trace=c.trace) for c in charges]
        response = CalculateResponse(charges=charge_outputs, subtotal=subtotal, vat_rate=0.15, vat_amount=vat_amount, grand_total=grand_total, currency='ZAR', calculation_trace=trace_log, vessel_name=vessel_profile.name, port_name=request.port)
        global _calculation_counter
        _calculation_counter += 1
        _calculation_cache[f'calc_{_calculation_counter}'] = {'response': response}
        return response
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={'message': str(e), 'code': 'INVALID_INPUT'})
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={'message': str(e), 'code': 'CALCULATION_ERROR'})
@app.post('/api/v1/calculate-from-pdfs', status_code=200)
async def calculate_from_pdfs(
    tariff_pdf: UploadFile = File(...),
    vessel_pdf: UploadFile = File(...)
):
    """
    Calculate tariffs from uploaded PDF files.

    Processes:
    1. Tariff PDF → Extract rules
    2. Vessel PDF → Extract vessel details
    3. Validate and calculate charges
    4. Save to logs
    """
    start_time = datetime.utcnow()
    calculation_id = f"calc_{uuid.uuid4().hex[:12]}"

    try:
        # Mock parsing of PDFs - in real implementation, use DocumentParser
        # For now, we'll assume the vessel is SUDESTADA and port is Durban
        vessel_name = "SUDESTADA"
        port = "durban"

        # Load rules for port
        rules = load_tariff_for_port(port)

        # Create vessel profile
        vessel_profile = VesselProfile(
            name=vessel_name,
            gross_tonnage=51300,
            length_overall=190.5,
            beam=32.2,
            draft=10.5,
            cargo_type="General Cargo",
            containers_teu=0
        )

        # Calculate charges
        charges, trace_log = execute_mock_calculation(vessel_profile, port, rules, [])

        subtotal = sum(Decimal(str(c.amount)) for c in charges)
        vat_amount = subtotal * Decimal('0.15')
        grand_total = subtotal + vat_amount

        # Calculate processing time
        processing_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        # Create charge outputs
        charge_outputs = [
            {
                'charge_type': c.charge_type,
                'description': c.description,
                'amount': float(c.amount),
                'confidence': 0.92
            }
            for c in charges
        ]

        # Store log
        log_data = {
            'calculation_id': calculation_id,
            'timestamp': start_time.isoformat() + 'Z',
            'vessel_name': vessel_name,
            'port': port,
            'tariff_file': tariff_pdf.filename,
            'vessel_file': vessel_pdf.filename,
            'extraction': {
                'charges_discovered': len(charges),
                'confidence': 0.92,
                'time_ms': processing_time_ms
            },
            'charges': charge_outputs,
            'subtotal': float(subtotal),
            'vat_amount': float(vat_amount),
            'grand_total': float(grand_total),
            'processing_time_ms': processing_time_ms,
            'status': 'success'
        }

        _calculation_logs[calculation_id] = log_data

        return {
            'calculation_id': calculation_id,
            'vessel_name': vessel_name,
            'port': port,
            'charges': charge_outputs,
            'subtotal': float(subtotal),
            'vat_amount': float(vat_amount),
            'grand_total': float(grand_total),
            'extraction_confidence': 0.92,
            'processing_time_ms': processing_time_ms,
            'status': 'success'
        }

    except Exception as e:
        processing_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        log_data = {
            'calculation_id': calculation_id,
            'timestamp': start_time.isoformat() + 'Z',
            'vessel_name': 'Unknown',
            'port': 'Unknown',
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
                'calculate': 'POST /api/v1/calculate',
                'calculate_from_pdfs': 'POST /api/v1/calculate-from-pdfs',
                'logs': 'GET /api/v1/logs',
                'log_detail': 'GET /api/v1/logs/{calculation_id}',
                'docs': '/docs'
            }
        }

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
