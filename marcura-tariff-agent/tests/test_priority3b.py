"""
tests/test_priority3b.py
------------------------
Priority 3b verification test:
  - CompletenessValidatorAgent  (checks extraction completeness)
  - SchemaValidatorAgent        (checks vessel data completeness)

Run from the marcura-tariff-agent/ directory:
    python -m pytest tests/test_priority3b.py -v

Or run directly:
    python tests/test_priority3b.py
"""

import sys
import os

# Allow running as a plain script from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine.agents import (
    VesselQueryParserAgent,
    RuleExtractionAgent,
    CompletenessValidatorAgent,
    SchemaValidatorAgent,
)
from src.utils.config import Config
from src.core.models import RuleStore, ExtractedRule, VesselProfile

# ---------------------------------------------------------------------------
# Shared mock data (from Priority 3a)
# ---------------------------------------------------------------------------

MOCK_TARIFF_TEXT = """
PILOTAGE CHARGES
Base Fee: R18,608.61
Incremental: R9.72 per 100 tonnes
Operations: 2 (enter and leave)
Compulsory for all vessels at Durban

PORT DUES
Base: R1,000
Rate: R1.25 per 100 tonnes per 24 hours
Duration: 7 days

TOWAGE
50,001-100,000 GT: R15,000
Includes in/out of port
"""

# Manually create a mock RuleStore to avoid hitting API quota again
MOCK_RULES = RuleStore(
    port_name="durban",
    rules=[
        ExtractedRule(
            charge_type="pilotage",
            calculation_logic="Base fee plus incremental rate per 100 GT, 2 operations",
            extracted_parameters={
                "base_fee": 18608.61,
                "rate_per_100_gt": 9.72,
                "operations": 2,
            },
            extraction_confidence=0.95,
            required_variables=["gross_tonnage"],
            conditions="Compulsory for all vessels",
        ),
        ExtractedRule(
            charge_type="port_dues",
            calculation_logic="Base fee plus rate per 100 GT per 24 hours for 7 days",
            extracted_parameters={
                "base_fee": 1000,
                "rate_per_100_gt": 1.25,
                "days": 7,
            },
            extraction_confidence=0.90,
            required_variables=["gross_tonnage", "days_in_port"],
            conditions="Standard port dues",
        ),
        ExtractedRule(
            charge_type="towage",
            calculation_logic="Fixed rate for GT band 50,001-100,000",
            extracted_parameters={"rate": 15000, "gt_min": 50001, "gt_max": 100000},
            extraction_confidence=0.85,
            required_variables=["gross_tonnage"],
            conditions="Includes in/out of port",
        ),
    ],
)

MOCK_VESSEL = VesselProfile(
    name="SUDESTADA",
    type="Bulk Carrier",
    gross_tonnage=51300,
    port="Durban",
)


def run_tests():
    c = Config()

    print("[OK] Setup: Using mock RuleStore and VesselProfile")

    # ------------------------------------------------------------------
    # Test CompletenessValidatorAgent
    # ------------------------------------------------------------------
    completeness = CompletenessValidatorAgent(config=c)
    completeness.initialize()

    validation = completeness.execute(MOCK_RULES, MOCK_TARIFF_TEXT)

    assert isinstance(validation, dict), "Should return dict validation report"
    assert "all_rules_found" in validation, "Missing all_rules_found field"
    assert "confidence_level" in validation, "Missing confidence_level field"

    print(f"[OK] CompletenessValidator: all_rules_found={validation['all_rules_found']}")
    if validation.get("missed_charges"):
        print(f"     Warning: Missed charges: {validation['missed_charges']}")
    if validation.get("recommendations"):
        print(f"     Recommendations: {validation['recommendations']}")

    # ------------------------------------------------------------------
    # Test SchemaValidatorAgent
    # ------------------------------------------------------------------
    schema = SchemaValidatorAgent(config=c)
    schema.initialize()

    validation = schema.execute(MOCK_VESSEL, MOCK_RULES)

    assert isinstance(validation, dict), "Should return dict validation report"
    assert "valid" in validation, "Missing valid field"
    assert "missing_fields" in validation, "Missing missing_fields field"
    assert "applicable_rules" in validation, "Missing applicable_rules field"

    print(f"[OK] SchemaValidator: valid={validation['valid']}")
    print(f"     Applicable rules: {validation.get('applicable_rules', 0)}")
    if validation.get("missing_fields"):
        print(f"     Warning: Missing fields: {validation['missing_fields']}")
    if validation.get("warnings"):
        print(f"     Warnings: {validation['warnings']}")

    print()
    print("[OK] PRIORITY 3b COMPLETE -- Validators working")


if __name__ == "__main__":
    run_tests()
