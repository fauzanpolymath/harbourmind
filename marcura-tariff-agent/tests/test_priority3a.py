"""
tests/test_priority3a.py
------------------------
Priority 3a verification test:
  - VesselQueryParserAgent  (JSON string input, dict input)
  - RuleExtractionAgent     (mock tariff document)

Run from the marcura-tariff-agent/ directory:
    python -m pytest tests/test_priority3a.py -v

Or run directly:
    python tests/test_priority3a.py
"""

import sys
import os

# Allow running as a plain script from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine.agents import VesselQueryParserAgent, RuleExtractionAgent
from src.utils.config import Config

# ---------------------------------------------------------------------------
# Shared mock tariff document
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


def run_tests():
    c = Config()

    # ------------------------------------------------------------------
    # VesselQueryParserAgent
    # ------------------------------------------------------------------
    vessel_parser = VesselQueryParserAgent(config=c)
    vessel_parser.initialize()

    # Test 1: JSON string input
    vessel_json = '{"type": "Bulk Carrier", "gross_tonnage": 51300, "port": "Durban"}'
    vessel_profile = vessel_parser.execute(vessel_json)
    assert vessel_profile.gross_tonnage == 51300, (
        f"Gross tonnage mismatch: expected 51300, got {vessel_profile.gross_tonnage}"
    )
    assert vessel_profile.type == "Bulk Carrier", (
        f"Type mismatch: expected 'Bulk Carrier', got {vessel_profile.type!r}"
    )
    print("[OK] VesselQueryParserAgent working (JSON string input)")

    # Test 2: dict input
    vessel_dict = {"type": "Bulk Carrier", "gross_tonnage": 51300}
    vessel_profile2 = vessel_parser.execute(vessel_dict)
    assert vessel_profile2.gross_tonnage == 51300, (
        f"Gross tonnage mismatch: expected 51300, got {vessel_profile2.gross_tonnage}"
    )
    assert vessel_profile2.type == "Bulk Carrier", (
        f"Type mismatch: expected 'Bulk Carrier', got {vessel_profile2.type!r}"
    )
    print("[OK] VesselQueryParserAgent handles both JSON and dict")

    # ------------------------------------------------------------------
    # RuleExtractionAgent
    # ------------------------------------------------------------------
    rule_extractor = RuleExtractionAgent(config=c)
    rule_extractor.initialize()

    rules = rule_extractor.execute(MOCK_TARIFF_TEXT, "durban")

    assert len(rules.rules) > 0, "No rules extracted"
    assert any(r.charge_type.lower() == "pilotage" for r in rules.rules), (
        f"Pilotage not found in extracted rules: {rules.charge_types}"
    )

    print(f"[OK] RuleExtractionAgent found {len(rules.rules)} charges")
    for rule in rules.rules:
        print(f"     - {rule.charge_type}: confidence {rule.extraction_confidence:.2f}")

    print()
    print("[OK] PRIORITY 3a COMPLETE -- Vessel & Rule agents working")


if __name__ == "__main__":
    run_tests()
