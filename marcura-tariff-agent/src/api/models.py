from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class VesselDataInput(BaseModel):
    name: Optional[str] = Field(None, description='Vessel name')
    type: Optional[str] = Field(None, description='Vessel type')
    gross_tonnage: Optional[float] = Field(None, description='Gross tonnage in metric tonnes')
    imo_number: Optional[str] = Field(None, description='IMO number')
    call_sign: Optional[str] = Field(None, description='Radio call sign')
    length_overall: Optional[float] = Field(None, description='Length overall in metres')
    beam: Optional[float] = Field(None, description='Beam / width in metres')
    draft: Optional[float] = Field(None, description='Draft in metres')
    net_tonnage: Optional[float] = Field(None, description='Net tonnage')
    deadweight_tonnage: Optional[float] = Field(None, description='Deadweight tonnage')
    cargo_type: Optional[str] = Field(None, description='Type of cargo')
    cargo_tonnage: Optional[float] = Field(None, description='Cargo weight in metric tonnes')
    days_in_port: Optional[float] = Field(None, description='Expected days in port')

class CalculateRequest(BaseModel):
    vessel_data: VesselDataInput = Field(..., description='Vessel details')
    port: str = Field(..., description='Port name (e.g., durban)')
    target_dues: List[str] = Field(default_factory=list, description='Specific charges to calculate (empty = all)')

class ChargeOutput(BaseModel):
    charge_type: str = Field(..., description='Type of charge')
    description: str = Field(..., description='Human-readable description')
    amount: float = Field(..., description='Calculated amount in ZAR')
    trace: Dict[str, Any] = Field(default_factory=dict, description='Audit trail with formula and parameters')

class CalculateResponse(BaseModel):
    charges: List[ChargeOutput] = Field(default_factory=list, description='All calculated charges')
    subtotal: float = Field(0.0, description='Sum of charges before VAT')
    vat_rate: float = Field(0.15, description='VAT rate applied')
    vat_amount: float = Field(0.0, description='VAT amount in ZAR')
    grand_total: float = Field(0.0, description='Subtotal + VAT')
    currency: str = Field('ZAR', description='Currency code')
    calculation_trace: List[str] = Field(default_factory=list, description='Trace log of calculation steps')
    vessel_name: Optional[str] = Field(None, description='Vessel name from request')
    port_name: str = Field(..., description='Port name')

class RefinementRequest(BaseModel):
    calculation_id: str = Field(..., description='ID of previous calculation')
    flagged_charge: str = Field(..., description='Charge type to refine')
    user_selection: int = Field(..., description='Index of selected interpretation')

class ErrorResponse(BaseModel):
    message: str = Field(..., description='Error message')
    code: str = Field(..., description='Error code')
    details: Optional[Dict[str, Any]] = Field(None, description='Additional error details')
