"""
models.py  (src/core/models.py)
--------------------------------
Core Pydantic domain models for HarbourMind.

  Input  →  VesselProfile        Normalised vessel / call details
  Rules  →  ExtractedRule        A single tariff charge discovered from a document
             RuleStore           Container for all rules extracted for a port
  Output →  CalculatedCharge     One computed line item with formula trace
             CalculationResult   Aggregated result for a vessel/port combination
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Vessel
# ---------------------------------------------------------------------------


class VesselProfile(BaseModel):
    """
    Normalised representation of a vessel and its port-call parameters.

    All numeric dimensions are stored in SI / maritime-standard units:
      - tonnages   in metric tonnes (GT, NT, DWT)
      - lengths    in metres (LOA, beam, draft)
      - cargo      in metric tonnes
    """

    # Identity
    name: Optional[str] = Field(None, description="Vessel name")
    imo_number: Optional[str] = Field(None, description="IMO vessel number")
    call_sign: Optional[str] = Field(None, description="Radio call sign")

    # Classification
    type: Optional[str] = Field(
        None,
        description="Vessel type, e.g. 'Bulk Carrier', 'Container Ship', 'Tanker'",
    )

    # Tonnages
    gross_tonnage: Optional[float] = Field(None, description="Gross tonnage (GT)")
    net_tonnage: Optional[float] = Field(None, description="Net tonnage (NT)")
    deadweight_tonnage: Optional[float] = Field(
        None, description="Deadweight tonnage (DWT)"
    )

    # Dimensions
    length_overall: Optional[float] = Field(None, description="Length overall in metres")
    beam: Optional[float] = Field(None, description="Beam / width in metres")
    draft: Optional[float] = Field(None, description="Draft in metres")

    # Port call
    port: Optional[str] = Field(None, description="Port of call")
    cargo_type: Optional[str] = Field(None, description="Type of cargo")
    cargo_tonnage: Optional[float] = Field(
        None, description="Cargo weight in metric tonnes"
    )
    days_in_port: Optional[float] = Field(
        None, description="Expected duration of port stay in days"
    )

    # Certificate-specific parameters
    days_alongside: Optional[float] = Field(
        None, description="Days alongside in port (from shipping certificate, e.g. 3.39)"
    )
    number_of_operations: Optional[int] = Field(
        None, description="Number of port operations / movements (typically 2: in + out)"
    )

    class Config:
        # Allow extra fields so the LLM can include additional context without
        # causing validation errors.
        extra = "ignore"


# ---------------------------------------------------------------------------
# Extracted tariff rules
# ---------------------------------------------------------------------------


class ExtractedRule(BaseModel):
    """
    A single tariff charge discovered by the RuleExtractionAgent from a port
    tariff document. Rules are discovered, not hardcoded — `charge_type`
    reflects whatever the document actually contains.
    """

    charge_type: str = Field(
        ...,
        description=(
            "Lowercase identifier for the charge type as found in the document, "
            "e.g. 'pilotage', 'port_dues', 'towage', 'berth_hire'."
        ),
    )
    calculation_logic: str = Field(
        ...,
        description=(
            "Plain-English explanation of how the charge is calculated, "
            "e.g. 'Base fee plus incremental rate per 100 GT'."
        ),
    )
    extracted_parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Numeric parameters extracted from the document, "
            "e.g. {'base_fee': 18608.61, 'rate_per_100gt': 9.72}."
        ),
    )
    extraction_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for this extraction (0 = uncertain, 1 = certain).",
    )
    required_variables: List[str] = Field(
        default_factory=list,
        description=(
            "Vessel / call attributes needed to compute this charge, "
            "e.g. ['gross_tonnage', 'days_in_port']."
        ),
    )
    conditions: Optional[str] = Field(
        None,
        description=(
            "Applicability conditions, e.g. 'Compulsory for all vessels' or "
            "'Only for vessels > 50,000 GT'."
        ),
    )

    class Config:
        extra = "ignore"


class RuleStore(BaseModel):
    """
    Container for all tariff rules extracted for a given port. Produced by
    RuleExtractionAgent and consumed by PerRuleCalculator.

    Currency and tax fields are extracted from the tariff document itself —
    they are NOT hardcoded in the application.
    """

    port_name: str = Field(..., description="Port these rules belong to.")
    rules: List[ExtractedRule] = Field(
        default_factory=list,
        description="All discovered tariff rules for this port.",
    )
    currency: Optional[str] = Field(
        None,
        description="ISO-4217 currency code declared by the tariff (e.g. 'ZAR', 'USD', 'SGD').",
    )
    tax_rate: Optional[float] = Field(
        None,
        description="Local applicable-tax rate as a decimal (e.g. 0.15 for 15% VAT). "
                    "Null if the tariff does not declare one — caller decides how to handle.",
    )
    tax_label: Optional[str] = Field(
        None,
        description="Local-tax label as written in the tariff (e.g. 'VAT', 'GST', 'Sales Tax').",
    )
    extraction_timestamp: Optional[datetime] = Field(
        None,
        description="UTC timestamp when extraction was performed.",
    )
    source_document: Optional[str] = Field(
        None,
        description="Identifier / filename of the source tariff document.",
    )

    class Config:
        extra = "ignore"

    @property
    def charge_types(self) -> List[str]:
        """List of all discovered charge type names."""
        return [r.charge_type for r in self.rules]


# ---------------------------------------------------------------------------
# Calculation output
# ---------------------------------------------------------------------------


class CalculatedCharge(BaseModel):
    """A single computed charge with full audit trail."""

    charge_type: str = Field(..., description="Type of charge (e.g. 'pilotage')")
    description: str = Field(..., description="Human-readable description")
    amount: float = Field(..., description="Calculated amount in ZAR")
    trace: Dict[str, Any] = Field(
        default_factory=dict,
        description="Formula, values, explanation, and category group used to derive the amount.",
    )


class CalculationResult(BaseModel):
    """
    Result of a per-rule calculation pass — the successfully computed
    charges for one vessel against one tariff. Skipped and clarification
    rules are returned alongside this object by PerRuleCalculator.execute().
    """

    vessel_name: Optional[str] = Field(None, description="Name of the vessel")
    port_name: str = Field(..., description="Port name")
    charges: List[CalculatedCharge] = Field(
        default_factory=list,
        description="All successfully computed charges",
    )
    subtotal: float = Field(0.0, description="Sum of all charges before VAT")
    calculation_timestamp: Optional[datetime] = Field(
        None, description="When the calculation was performed"
    )
