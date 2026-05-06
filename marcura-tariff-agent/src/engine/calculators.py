"""
calculators.py  (src/engine/calculators.py)
--------------------------------------------
Core tariff calculation engine for HarbourMind.

Five calculator functions implement different fee structures:
  1. calculate_base_plus_incremental   —  Base + rate*(value/unit)
  2. calculate_per_unit_per_period     —  Base + rate*(value/unit)*periods
  3. calculate_bracket_based           —  Lookup and apply bracket rate
  4. calculate_flat_fee                —  Fixed amount * multiplier
  5. calculate_percentage_surcharge    —  Percentage of base value

Each returns a dict with:
  {
    "value": float,            # Final calculated amount
    "trace": {
      "formula": str,          # Plain-text formula used
      "parameters": dict,      # Input parameters
      "result": float,         # Intermediate results
      "applied_min_max": str,  # Min/max constraint applied if any
      "unit": str              # Unit of calculation
    }
  }

All calculations use Decimal arithmetic internally for precision,
rounded to 2 decimal places for ZAR currency.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# Precision & Rounding
# ─────────────────────────────────────────────────────────────────────────

def _round_to_cents(value: float) -> float:
    """Round to 2 decimal places (ZAR cents)."""
    d = Decimal(str(value))
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ─────────────────────────────────────────────────────────────────────────
# Calculator 1: Base + Incremental
# ─────────────────────────────────────────────────────────────────────────

def calculate_base_plus_incremental(
    base_fee: float,
    vessel_value: float,
    rate: float,
    unit: float,
    multiplier: float = 1.0,
    min_charge: float = 0.0,
    max_charge: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Calculate charge as: (base_fee + (vessel_value / unit) * rate) * multiplier

    Apply min/max bounds after calculation.

    Parameters
    ----------
    base_fee : float
        Fixed base charge amount (e.g., pilotage base fee)
    vessel_value : float
        Vessel metric (e.g., gross tonnage, length)
    rate : float
        Rate per unit
    unit : float
        Divisor for vessel_value (e.g., 100 for "per 100 tonnes")
    multiplier : float
        Final multiplier (e.g., 2 for round-trip)
    min_charge : float
        Minimum charge amount
    max_charge : float, optional
        Maximum charge amount

    Returns
    -------
    dict
        {"value": final_amount, "trace": {...}}

    Examples
    --------
    Pilotage (base 18608.61, incremental 9.72 per 100 GT, 2 operations):
        >>> calc = calculate_base_plus_incremental(
        ...     base_fee=18608.61,
        ...     vessel_value=51300,
        ...     rate=9.72,
        ...     unit=100,
        ...     multiplier=2
        ... )
    """
    # ── Calculation ─────────────────────────────────────────────────────
    incremental_part = (vessel_value / unit) * rate if unit != 0 else 0
    subtotal = base_fee + incremental_part
    result = subtotal * multiplier

    # ── Apply bounds ────────────────────────────────────────────────────
    final_value = result
    applied_bounds = "none"

    if min_charge > 0 and final_value < min_charge:
        final_value = min_charge
        applied_bounds = f"min: {min_charge}"

    if max_charge is not None and final_value > max_charge:
        final_value = max_charge
        applied_bounds = f"max: {max_charge}"

    final_value = _round_to_cents(final_value)

    # ── Trace ───────────────────────────────────────────────────────────
    trace = {
        "formula": (
            f"(base_fee + (vessel_value / unit) * rate) * multiplier = "
            f"({base_fee} + ({vessel_value} / {unit}) * {rate}) * {multiplier}"
        ),
        "parameters": {
            "base_fee": base_fee,
            "vessel_value": vessel_value,
            "rate": rate,
            "unit": unit,
            "multiplier": multiplier,
            "min_charge": min_charge,
            "max_charge": max_charge,
        },
        "result": {
            "incremental_part": _round_to_cents(incremental_part),
            "subtotal": _round_to_cents(subtotal),
            "before_bounds": _round_to_cents(result),
            "after_bounds": final_value,
        },
        "applied_min_max": applied_bounds,
        "unit": "ZAR",
    }

    return {"value": final_value, "trace": trace}


# ─────────────────────────────────────────────────────────────────────────
# Calculator 2: Per Unit Per Period
# ─────────────────────────────────────────────────────────────────────────

def calculate_per_unit_per_period(
    base_fee: float,
    rate: float,
    unit: float,
    vessel_value: float,
    periods: float,
    multiplier: float = 1.0,
    min_charge: float = 0.0,
    max_charge: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Calculate charge as: (base_fee + (vessel_value / unit) * rate * periods) * multiplier

    Apply min/max bounds after calculation.

    Parameters
    ----------
    base_fee : float
        Fixed base charge (e.g., daily port dues base)
    rate : float
        Rate per unit per period
    unit : float
        Divisor for vessel_value
    vessel_value : float
        Vessel metric (GT, DWT, etc.)
    periods : float
        Number of periods (days, hours, operations)
    multiplier : float
        Final multiplier
    min_charge : float
        Minimum charge
    max_charge : float, optional
        Maximum charge

    Returns
    -------
    dict
        {"value": final_amount, "trace": {...}}

    Examples
    --------
    Port Dues (base 1000, rate 1.25 per 100 GT per day, 7 days):
        >>> calc = calculate_per_unit_per_period(
        ...     base_fee=1000,
        ...     rate=1.25,
        ...     unit=100,
        ...     vessel_value=51300,
        ...     periods=7,
        ...     multiplier=1
        ... )
    """
    # ── Calculation ─────────────────────────────────────────────────────
    incremental_part = (vessel_value / unit) * rate * periods if unit != 0 else 0
    subtotal = base_fee + incremental_part
    result = subtotal * multiplier

    # ── Apply bounds ────────────────────────────────────────────────────
    final_value = result
    applied_bounds = "none"

    if min_charge > 0 and final_value < min_charge:
        final_value = min_charge
        applied_bounds = f"min: {min_charge}"

    if max_charge is not None and final_value > max_charge:
        final_value = max_charge
        applied_bounds = f"max: {max_charge}"

    final_value = _round_to_cents(final_value)

    # ── Trace ───────────────────────────────────────────────────────────
    trace = {
        "formula": (
            f"(base_fee + (vessel_value / unit) * rate * periods) * multiplier = "
            f"({base_fee} + ({vessel_value} / {unit}) * {rate} * {periods}) * {multiplier}"
        ),
        "parameters": {
            "base_fee": base_fee,
            "rate": rate,
            "unit": unit,
            "vessel_value": vessel_value,
            "periods": periods,
            "multiplier": multiplier,
            "min_charge": min_charge,
            "max_charge": max_charge,
        },
        "result": {
            "incremental_part": _round_to_cents(incremental_part),
            "subtotal": _round_to_cents(subtotal),
            "before_bounds": _round_to_cents(result),
            "after_bounds": final_value,
        },
        "applied_min_max": applied_bounds,
        "unit": "ZAR",
    }

    return {"value": final_value, "trace": trace}


# ─────────────────────────────────────────────────────────────────────────
# Calculator 3: Bracket-Based
# ─────────────────────────────────────────────────────────────────────────

def calculate_bracket_based(
    vessel_value: float,
    brackets: List[Dict[str, float]],
    multiplier: float = 1.0,
) -> Dict[str, Any]:
    """
    Find matching bracket for vessel_value and apply its rate.

    Each bracket has: {"min": X, "max": Y, "rate": Z}
    Returns the rate for the bracket where min <= vessel_value <= max.

    Parameters
    ----------
    vessel_value : float
        Vessel metric (GT, etc.)
    brackets : list of dict
        List of brackets, each with min, max, rate
    multiplier : float
        Final multiplier

    Returns
    -------
    dict
        {"value": final_amount, "trace": {...}}

    Examples
    --------
    Towage (51,300 GT falls in 50k-100k bracket = 15,000):
        >>> brackets = [
        ...     {"min": 0, "max": 10000, "rate": 5000},
        ...     {"min": 10001, "max": 50000, "rate": 10000},
        ...     {"min": 50001, "max": 100000, "rate": 15000}
        ... ]
        >>> calc = calculate_bracket_based(vessel_value=51300, brackets=brackets)
    """
    # ── Find matching bracket ───────────────────────────────────────────
    matched_bracket = None
    for bracket in brackets:
        min_val = bracket.get("min", 0)
        max_val = bracket.get("max", float("inf"))
        if min_val <= vessel_value <= max_val:
            matched_bracket = bracket
            break

    if matched_bracket is None:
        # No match found; use rate=0 and report
        result = 0
        matched_bracket = {"min": None, "max": None, "rate": 0}
        matched_bracket_str = "NONE (vessel_value out of range)"
    else:
        result = matched_bracket.get("rate", 0) * multiplier
        matched_bracket_str = (
            f"{matched_bracket.get('min')}-{matched_bracket.get('max')} GT, "
            f"rate={matched_bracket.get('rate')}"
        )

    final_value = _round_to_cents(result)

    # ── Trace ───────────────────────────────────────────────────────────
    trace = {
        "formula": f"Bracket rate for {vessel_value} GT * {multiplier}",
        "parameters": {
            "vessel_value": vessel_value,
            "brackets": brackets,
            "multiplier": multiplier,
        },
        "matched_bracket": matched_bracket_str,
        "result": {
            "bracket_rate": matched_bracket.get("rate", 0),
            "before_multiplier": matched_bracket.get("rate", 0),
            "after_multiplier": final_value,
        },
        "applied_min_max": "none",
        "unit": "ZAR",
    }

    return {"value": final_value, "trace": trace}


# ─────────────────────────────────────────────────────────────────────────
# Calculator 4: Flat Fee
# ─────────────────────────────────────────────────────────────────────────

def calculate_flat_fee(
    fee: float,
    multiplier: float = 1.0,
    surcharges: float = 0.0,
) -> Dict[str, Any]:
    """
    Calculate simple flat fee: fee * multiplier + surcharges

    Parameters
    ----------
    fee : float
        Base fee amount
    multiplier : float
        Multiplier (e.g., 2 for round-trip)
    surcharges : float
        Additional charges to add

    Returns
    -------
    dict
        {"value": final_amount, "trace": {...}}

    Examples
    --------
    VTS Dues (flat 12,500):
        >>> calc = calculate_flat_fee(fee=12500, multiplier=1)

    Running Lines (flat 10,000):
        >>> calc = calculate_flat_fee(fee=10000, multiplier=1)
    """
    result = (fee * multiplier) + surcharges
    final_value = _round_to_cents(result)

    # ── Trace ───────────────────────────────────────────────────────────
    trace = {
        "formula": f"fee * multiplier + surcharges = {fee} * {multiplier} + {surcharges}",
        "parameters": {
            "fee": fee,
            "multiplier": multiplier,
            "surcharges": surcharges,
        },
        "result": {
            "before_surcharges": _round_to_cents(fee * multiplier),
            "after_surcharges": final_value,
        },
        "applied_min_max": "none",
        "unit": "ZAR",
    }

    return {"value": final_value, "trace": trace}


# ─────────────────────────────────────────────────────────────────────────
# Calculator 5: Percentage Surcharge
# ─────────────────────────────────────────────────────────────────────────

def calculate_percentage_surcharge(
    base_value: float,
    percentage: float,
) -> Dict[str, Any]:
    """
    Calculate percentage surcharge: (base_value * percentage) / 100

    Parameters
    ----------
    base_value : float
        Base amount to apply surcharge to
    percentage : float
        Surcharge percentage (e.g., 50 for 50%)

    Returns
    -------
    dict
        {"value": final_amount, "trace": {...}}

    Examples
    --------
    50% surcharge on Pilotage (e.g., for after-hours operations):
        >>> calc = calculate_percentage_surcharge(
        ...     base_value=37217.22,
        ...     percentage=50
        ... )
    """
    result = (base_value * percentage) / 100
    final_value = _round_to_cents(result)

    # ── Trace ───────────────────────────────────────────────────────────
    trace = {
        "formula": f"(base_value * percentage) / 100 = ({base_value} * {percentage}) / 100",
        "parameters": {
            "base_value": base_value,
            "percentage": percentage,
        },
        "result": {
            "surcharge": final_value,
        },
        "applied_min_max": "none",
        "unit": "ZAR",
    }

    return {"value": final_value, "trace": trace}
