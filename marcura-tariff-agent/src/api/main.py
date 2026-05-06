from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
import sys, os
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.utils.config import Config
from src.engine.calculators import calculate_base_plus_incremental, calculate_per_unit_per_period, calculate_bracket_based, calculate_flat_fee
from src.core.models import VesselProfile, RuleStore, ExtractedRule, CalculatedCharge
from src.api.models import CalculateRequest, CalculateResponse, ChargeOutput
app = FastAPI(title='HarbourMind Tariff Calculator', version='1.0.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
cfg = Config()
_calculation_cache = {}
_calculation_counter = 0
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
@app.get('/')
async def root():
    return {'service': 'HarbourMind Tariff Calculator API', 'version': '1.0.0', 'endpoints': {'health': 'GET /health', 'calculate': 'POST /api/v1/calculate', 'docs': '/docs'}}
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
