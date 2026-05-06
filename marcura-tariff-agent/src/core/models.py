"""
models.py  (src/core/models.py)
--------------------------------
Core Pydantic domain models for HarbourMind.

Model hierarchy:
  Input  →  VesselProfile        Normalised vessel / call details
  Rules  →  ExtractedRule        A single tariff charge discovered from a document
             RuleStore           Container for all rules extracted for a port
  Output →  CostLineItem         One line on the calculated disbursement account
             TariffResult        Final aggregated result returned to the API caller
  Misc   →  Port                 Port identifier + metadata
             TariffSchedule      Raw parsed tariff schedule (pre-rule-extraction)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


class VesselProfile(BaseModel):
    """
    Normalised representation of a vessel and its port-call parameters.

    All numeric dimensions are stored in SI / maritime-standard units:
      - tonnages   → metric tonnes (GT, NT, DWT)
      - lengths    → metres (LOA, beam, draft)
      - cargo      → metric tonnes
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

    class Config:
        # Allow extra fields so the LLM can include additional context without
        # causing validation errors during early development.
        extra = "ignore"


# ---------------------------------------------------------------------------
# Rule / tariff extraction models
# ---------------------------------------------------------------------------


class ExtractedRule(BaseModel):
    """
    A single tariff charge discovered by the RuleExtractionAgent from a
    port tariff document.  Rules are **discovered** — not hardcoded — so
    `charge_type` reflects whatever the document actually contains.
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
            "e.g. {'base_fee': 18608.61, 'rate_per_100gt': 9.72, 'operations': 2}."
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
            "Vessel / call attributes required to compute this charge, "
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
    Container for all tariff rules extracted for a given port.
    Produced by RuleExtractionAgent and consumed by the calculator engine.
    """

    port_name: str = Field(..., description="Port these rules belong to.")
    rules: List[ExtractedRule] = Field(
        default_factory=list,
        description="All discovered tariff rules for this port.",
    )
    extraction_timestamp: Optional[datetime] = Field(
        None,
        description="UTC timestamp when extraction was performed.",
    )
    source_document: Optional[str] = Field(
        None,
        description="Identifier / filename of the source tariff document.",
    )

    @property
    def charge_types(self) -> List[str]:
        """Convenience: list of all discovered charge type names."""
        return [r.charge_type for r in self.rules]


# ---------------------------------------------------------------------------
# Port metadata
# ---------------------------------------------------------------------------


class Port(BaseModel):
    """Port identifier and metadata."""

    name: str = Field(..., description="Port name, e.g. 'Durban'.")
    code: Optional[str] = Field(None, description="UN/LOCODE, e.g. 'ZADUR'.")
    country: Optional[str] = Field(None, description="ISO 3166-1 alpha-2 country code.")
    currency: Optional[str] = Field(
        None, description="ISO 4217 currency code for tariff charges, e.g. 'ZAR'."
    )
    timezone: Optional[str] = Field(
        None, description="IANA timezone, e.g. 'Africa/Johannesburg'."
    )


# ---------------------------------------------------------------------------
# Raw tariff schedule (pre rule-extraction)
# ---------------------------------------------------------------------------


class TariffSchedule(BaseModel):
    """
    Raw structured output from the document parser, before rule extraction.
    Holds the full text and any coarse metadata found in the document.
    """

    port_name: str
    raw_text: str = Field(..., description="Full extracted text of the tariff document.")
    document_date: Optional[str] = Field(
        None, description="Effective date of the tariff schedule if present."
    )
    currency: Optional[str] = Field(None, description="Currency found in document.")
    source_file: Optional[str] = Field(None, description="Source filename.")


# ---------------------------------------------------------------------------
# Output / calculation models
# ---------------------------------------------------------------------------


class CostLineItem(BaseModel):
    """One line on the calculated port disbursement account (DA)."""

    charge_type: str = Field(..., description="Matches ExtractedRule.charge_type.")
    description: str = Field(
        ..., description="Human-readable description of the line item."
    )
    quantity: float = Field(..., description="Applied quantity (e.g. GT, days).")
    unit: str = Field(..., description="Unit label, e.g. 'GT', 'days', 'per 100 GT'.")
    unit_rate: float = Field(..., description="Rate per unit.")
    amount: float = Field(..., description="Total charge for this line: quantity × rate.")
    currency: str = Field(default="ZAR", description="ISO 4217 currency code.")
    notes: Optional[str] = Field(None, description="Any caveats or conditions applied.")


class TariffResult(BaseModel):
    """
    Final aggregated tariff calculation result returned by the engine
    and exposed via the API.
    """

    port_name: str
    vessel_name: Optional[str] = None
    line_items: List[CostLineItem] = Field(default_factory=list)
    subtotal: float = Field(0.0, description="Sum of all line items before tax/fees.")
    currency: str = Field(default="ZAR")
    calculation_notes: Optional[str] = None
    calculated_at: Optional[datetime] = None

    @property
    def total(self) -> float:
        """Recompute total from line items (subtotal alias kept for compatibility)."""
        return sum(item.amount for item in self.line_items)


# ---------------------------------------------------------------------------
# Validation models
# ---------------------------------------------------------------------------


class ValidationReport(BaseModel):
    """
    Structured validation result produced by CompletenessValidatorAgent
    or SchemaValidatorAgent.

    Different validators populate different fields:
      CompletenessValidatorAgent → all_rules_found, missed_charges, confidence_level, recommendations
      SchemaValidatorAgent       → valid, missing_fields, applicable_rules, warnings
    """

    # ── Common validation fields ────────────────────────────────────────
    valid: Optional[bool] = Field(
        None,
        description="Overall validation passed (SchemaValidator).",
    )
    all_rules_found: Optional[bool] = Field(
        None,
        description="All tariff charges were extracted (CompletenessValidator).",
    )
    confidence_level: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Validation confidence score.",
    )

    # ── CompletenessValidator fields ────────────────────────────────────
    missed_charges: List[str] = Field(
        default_factory=list,
        description="Charge types found in document but not extracted.",
    )
    recommendations: List[str] = Field(
        default_factory=list,
        description="Suggestions for improving extraction quality.",
    )

    # ── SchemaValidator fields ──────────────────────────────────────────
    missing_fields: List[str] = Field(
        default_factory=list,
        description="Vessel profile fields required by rules but not present.",
    )
    applicable_rules: Optional[int] = Field(
        None,
        description="Number of rules that apply to the vessel.",
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Non-fatal validation warnings.",
    )

    def dict_for_response(self) -> dict:
        """Return only non-None fields as a clean dict."""
        return {k: v for k, v in self.dict().items() if v not in (None, [], 0)}


# ---------------------------------------------------------------------------
# Calculation & Refinement models (Priority 5)
# ---------------------------------------------------------------------------


class CalculatedCharge(BaseModel):
    """
    A single calculated charge with trace information for audit trail.
    """

    charge_type: str = Field(..., description="Type of charge (e.g. 'pilotage')")
    description: str = Field(..., description="Human-readable description")
    amount: float = Field(..., description="Calculated amount in ZAR")
    trace: Dict[str, Any] = Field(
        default_factory=dict,
        description="Calculator trace with formula, parameters, results",
    )


class CalculationResult(BaseModel):
    """
    Result of CalculationAgent execution.
    Contains all calculated charges for a vessel profile against a rule store.
    """

    vessel_name: Optional[str] = Field(None, description="Name of the vessel")
    port_name: str = Field(..., description="Port name")
    charges: List[CalculatedCharge] = Field(
        default_factory=list,
        description="All calculated charges",
    )
    subtotal: float = Field(0.0, description="Sum of all charges before VAT")
    calculation_timestamp: Optional[datetime] = Field(
        None, description="When calculation was performed"
    )


class ExceptionCharge(BaseModel):
    """
    A charge that has an issue or exception (missing rate, discretionary, etc).
    """

    charge_type: str = Field(..., description="Type of charge")
    description: str = Field(..., description="Description of the charge")
    issue: str = Field(..., description="What went wrong or is uncertain")
    severity: str = Field(
        default="warning",
        description="'warning' or 'error' — whether calculation is blocked",
    )


class ProcessedResult(BaseModel):
    """
    Result of ExceptionHandlerAgent execution.
    Separates successfully calculated charges from those with exceptions.
    """

    calculated_charges: List[CalculatedCharge] = Field(
        default_factory=list,
        description="Charges calculated without issues",
    )
    exceptions: List[ExceptionCharge] = Field(
        default_factory=list,
        description="Charges with missing rates, discretionary fees, etc.",
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Non-fatal warnings about the calculation",
    )
    partial_result: bool = Field(
        default=False,
        description="True if we have exceptions preventing full calculation",
    )
    subtotal_calculated: float = Field(
        0.0, description="Sum of successfully calculated charges"
    )


class ClarificationPrompt(BaseModel):
    """
    Prompt asking user to clarify which interpretation of a charge is correct.
    Used by RefinementLoopAgent to request user feedback.
    """

    charge_type: str = Field(..., description="Which charge needs clarification")
    rule_summary: str = Field(
        ..., description="Summary of the rule from the tariff document"
    )
    current_value: float = Field(..., description="Currently calculated value")
    issue_description: str = Field(
        ..., description="What was unclear about this charge"
    )
    options: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of alternative interpretations, each with: {'interpretation': str, 'expected_value': float}",
    )


class UpdatedResult(BaseModel):
    """
    Result of RefinementLoopAgent.refine() — a recalculated charge.
    """

    charge_type: str = Field(..., description="Which charge was updated")
    original_value: float = Field(..., description="The original calculated value")
    updated_value: float = Field(..., description="The new calculated value")
    interpretation: str = Field(..., description="Which interpretation was chosen")
    trace: Dict[str, Any] = Field(
        default_factory=dict,
        description="Updated calculation trace",
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Confidence in the updated value",
    )
