"""
src/api/models.py
-----------------
Pydantic models exposed by the API layer.

Only ChargeOutput is currently used (by /api/v1/calculate-from-pdfs).
Domain models (VesselProfile, RuleStore, ExtractedRule, CalculatedCharge,
CalculationResult) live in src/core/models.py.
"""

from typing import Any, Dict
from pydantic import BaseModel, Field


class ChargeOutput(BaseModel):
    """One line item in the calculation response."""

    charge_type: str = Field(..., description='Type of charge')
    description: str = Field(..., description='Human-readable description')
    amount: float = Field(..., description='Calculated amount in ZAR')
    trace: Dict[str, Any] = Field(
        default_factory=dict,
        description='Audit trail with formula, values, and explanation',
    )
