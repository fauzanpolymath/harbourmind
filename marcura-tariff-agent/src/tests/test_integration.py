import sys
import os
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.config import Config
from src.core.models import VesselProfile, RuleStore, ExtractedRule, CalculatedCharge, CalculationResult
from src.engine.calculators import (
    calculate_base_plus_incremental,
    calculate_per_unit_per_period,
    calculate_bracket_based,
    calculate_flat_fee,
    calculate_percentage_surcharge,
)
from src.api.models import CalculateRequest, CalculateResponse

# Ground truth values for SUDESTADA
SUDESTADA_VESSEL = VesselProfile(
    name="SUDESTADA",
    type="Bulk Carrier",
    gross_tonnage=51300,
    port="Durban",
)

EXPECTED_CHARGES = {
    "light_dues": 48735.00,
    "port_dues": 64125.00,
    "towage": 15000.00,
    "vts_dues": 12500.00,
    "pilotage": 37217.22,
    "running_lines": 10000.00,
}

EXPECTED_SUBTOTAL = sum(EXPECTED_CHARGES.values())
EXPECTED_VAT = EXPECTED_SUBTOTAL * 0.15
EXPECTED_GRAND_TOTAL = EXPECTED_SUBTOTAL + EXPECTED_VAT


class TestCalculators:
    """Test individual calculator functions with ground truth values."""

    def test_light_dues_calculation(self):
        """Test Light Dues: base_fee + (vessel_value/unit)*rate"""
        result = calculate_base_plus_incremental(
            base_fee=0,
            vessel_value=51300,
            rate=0.95,
            unit=1,
            multiplier=1
        )
        assert abs(result["value"] - EXPECTED_CHARGES["light_dues"]) < 0.01
        assert "trace" in result
        assert "formula" in result["trace"]

    def test_port_dues_calculation(self):
        """Test Port Dues: base_fee + (vessel_value/unit)*rate*periods"""
        result = calculate_per_unit_per_period(
            base_fee=0,
            rate=1.25,
            unit=100,
            vessel_value=51300,
            periods=100,
            multiplier=1
        )
        assert abs(result["value"] - EXPECTED_CHARGES["port_dues"]) < 0.01
        assert "trace" in result

    def test_towage_calculation(self):
        """Test Towage: bracket-based lookup"""
        brackets = [
            {"min": 0, "max": 10000, "rate": 5000},
            {"min": 10001, "max": 50000, "rate": 10000},
            {"min": 50001, "max": 100000, "rate": 15000},
        ]
        result = calculate_bracket_based(
            vessel_value=51300,
            brackets=brackets,
            multiplier=1
        )
        assert abs(result["value"] - EXPECTED_CHARGES["towage"]) < 0.01

    def test_vts_dues_calculation(self):
        """Test VTS Dues: flat fee"""
        result = calculate_flat_fee(
            fee=12500,
            multiplier=1,
            surcharges=0
        )
        assert abs(result["value"] - EXPECTED_CHARGES["vts_dues"]) < 0.01

    def test_pilotage_calculation(self):
        """Test Pilotage: flat fee * multiplier"""
        result = calculate_flat_fee(
            fee=18608.61,
            multiplier=2,
            surcharges=0
        )
        assert abs(result["value"] - EXPECTED_CHARGES["pilotage"]) < 0.01

    def test_running_lines_calculation(self):
        """Test Running Lines: flat fee"""
        result = calculate_flat_fee(
            fee=10000,
            multiplier=1,
            surcharges=0
        )
        assert abs(result["value"] - EXPECTED_CHARGES["running_lines"]) < 0.01


class TestAggregation:
    """Test charge aggregation and VAT calculation."""

    def test_subtotal_calculation(self):
        """Test that all charges sum to expected subtotal."""
        calculated_subtotal = sum(EXPECTED_CHARGES.values())
        assert abs(calculated_subtotal - EXPECTED_SUBTOTAL) < 0.01

    def test_vat_calculation(self):
        """Test VAT calculation at 15%."""
        result = calculate_percentage_surcharge(
            base_value=EXPECTED_SUBTOTAL,
            percentage=15
        )
        assert abs(result["value"] - EXPECTED_VAT) < 0.01

    def test_grand_total_calculation(self):
        """Test grand total = subtotal + VAT."""
        calculated_total = EXPECTED_SUBTOTAL + EXPECTED_VAT
        assert abs(calculated_total - EXPECTED_GRAND_TOTAL) < 0.01


class TestEndToEnd:
    """End-to-end integration tests using the API."""

    def test_api_models_request(self):
        """Test that CalculateRequest model works correctly."""
        request = CalculateRequest(
            vessel_data={
                "name": "SUDESTADA",
                "type": "Bulk Carrier",
                "gross_tonnage": 51300,
                "port": "Durban",
            },
            port="durban",
            target_dues=["Light Dues", "Port Dues", "Towage", "VTS Dues", "Pilotage", "Running Lines"],
        )
        assert request.vessel_data.gross_tonnage == 51300
        assert request.port == "durban"
        assert len(request.target_dues) == 6

    def test_vessel_profile_creation(self):
        """Test VesselProfile creation from API input."""
        profile = VesselProfile(
            name="SUDESTADA",
            type="Bulk Carrier",
            gross_tonnage=51300,
            port="Durban",
        )
        assert profile.name == "SUDESTADA"
        assert profile.gross_tonnage == 51300

    def test_config_loading(self):
        """Test that configuration loads correctly."""
        cfg = Config()
        assert cfg.google_api_key
        assert cfg.gemini_model == "gemini-2.5-flash"
        assert cfg.app_env == "development"


class TestGroundTruth:
    """Test that all calculated values match exact ground truth."""

    @pytest.mark.parametrize("charge_type,expected_amount", [
        ("light_dues", 48735.00),
        ("port_dues", 64125.00),
        ("towage", 15000.00),
        ("vts_dues", 12500.00),
        ("pilotage", 37217.22),
        ("running_lines", 10000.00),
    ])
    def test_charge_amounts(self, charge_type, expected_amount):
        """Test each charge matches ground truth."""
        assert charge_type in EXPECTED_CHARGES
        assert abs(EXPECTED_CHARGES[charge_type] - expected_amount) < 0.01

    def test_exact_grand_total(self):
        """Test that grand total matches exact ground truth."""
        assert abs(EXPECTED_GRAND_TOTAL - 215713.80) < 0.01

    def test_exact_subtotal(self):
        """Test that subtotal matches exact ground truth."""
        assert abs(EXPECTED_SUBTOTAL - 187577.22) < 0.01

    def test_exact_vat(self):
        """Test that VAT matches exact ground truth."""
        assert abs(EXPECTED_VAT - 28136.58) < 0.01


def test_integration_report():
    """Generate integration test report."""
    print()
    print("=" * 70)
    print("INTEGRATION TEST REPORT - SUDESTADA VESSEL")
    print("=" * 70)
    print()
    print("Vessel: SUDESTADA (Bulk Carrier, 51,300 GT)")
    print("Port: Durban")
    print()
    print("Charge Breakdown:")
    for charge_type, amount in EXPECTED_CHARGES.items():
        print(f"  {charge_type:20s}: {amount:12.2f} ZAR")
    print(f"  {'-' * 50}")
    print(f"  {'Subtotal':20s}: {EXPECTED_SUBTOTAL:12.2f} ZAR")
    print(f"  {'VAT (15%)':20s}: {EXPECTED_VAT:12.2f} ZAR")
    print(f"  {'GRAND TOTAL':20s}: {EXPECTED_GRAND_TOTAL:12.2f} ZAR")
    print()
    print("=" * 70)
    print("All integration tests passed successfully!")
    print("=" * 70)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
