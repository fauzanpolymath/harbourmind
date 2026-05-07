"""
per_rule_calculator.py
----------------------
Per-rule calculation: ask Gemini to interpret each extracted rule against
the vessel profile, return {formula, values}, then evaluate the formula
deterministically with simpleeval.

Design:
- Gemini is responsible for SHAPE (which values, what formula).
- We are responsible for ARITHMETIC (deterministic eval, no LLM math).
- Each skipped rule is captured with a reason, never silently dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from simpleeval import SimpleEval, NameNotDefined, InvalidExpression

from src.core.models import (
    CalculatedCharge,
    CalculationResult,
    ExtractedRule,
    RuleStore,
    VesselProfile,
)
from src.utils.config import Config

logger = logging.getLogger(__name__)


_PER_RULE_CALC_PROMPT = """\
You are a maritime tariff calculator. For ONE extracted tariff rule and ONE vessel,
decide the rule's STATUS, and (if computing) produce the formula and input values.

You output JSON only:
{
  "status": "computed" | "not_applicable" | "needs_clarification",
  "category_group": "<short snake_case group name, or null>",
  "missing_inputs": ["<vessel field name>", ...],
  "skip_reason": "<short reason if not computed, else null>",
  "formula": "<arithmetic expression as Python-syntax string>",
  "values": { "<name>": <number>, ... },
  "explanation": "<1-2 sentence human-readable explanation>"
}

STATUS DECISION TREE
====================

DEFAULT BIAS: prefer "computed" when the rule looks like a base/standard rate
that applies to all vessels of the kind being processed. Only escalate to
"needs_clarification" when the rule's own text or the *other rules in the
batch* make it genuinely ambiguous which one applies.

Use "computed" when ANY of these are true:
- The rule is the base/default rate for this charge category (e.g. "port dues
  for vessels at berth" with no qualifier — that IS the default for a vessel
  at berth) AND all needed vessel values are present.
- The rule's conditions are explicitly satisfied by the vessel data.
- The rule is the only one of its kind in the input and has a clear formula.

Use "not_applicable" when:
- The rule clearly does NOT apply (e.g. tanker-only rule, vessel is a bulker).
- The rule explicitly says "rates on application" / "by Harbour Master" / "as
  agreed" — no numeric formula is possible.
- The vessel does not match a hard bracket (e.g. LOA exceeds the table's max).
- A precondition is definitively false (e.g. surcharge "if cancelled" but the
  call wasn't cancelled, drydock fee but vessel isn't drydocking).

Use "needs_clarification" ONLY when the rule itself, or its sibling rules in
the document, define explicit mutually-exclusive categories AND the vessel
data does not pick one. Examples that DO warrant clarification:
- Cargo dues come in routed variants (coastwise / foreign-going / transhipped)
  AND the certificate doesn't declare the route.
- Light dues have explicit alternative schedules (annual SA-registered /
  monthly coaster / per-100-GT default) AND vessel registration status is
  unknown — but compute the per-100-GT default only if no other variant
  is clearly preferred.
- Two extracted rules in the batch are clearly variants of the same charge
  (suffixes like _coaster, _small_vessel, _green_award) AND vessel data
  doesn't disambiguate.

DO NOT use "needs_clarification" for:
- Hypothetical flags that the rule text does not actually condition on.
  If the rule says "vessels at berth: rate X", DO NOT ask for purpose_of_call,
  is_double_hull, green_award, etc. — those are not in the rule.
- Surcharge triggers that should default to false (OWH, late arrival,
  cancellation): if the vessel data does not indicate the trigger condition,
  mark "not_applicable", not "needs_clarification".
- Optional services with no usage signal (launch hire, crane hire, fire
  equipment use): default to "not_applicable" — you may not assume the
  service was used.

CATEGORY_GROUP
==============
- Set this to a short snake_case label that identifies the mutually-exclusive group the rule belongs to. Examples: "cargo_dues", "port_dues", "tug_assistance", "pilotage_surcharges". Use null if the rule stands alone.
- Use this to avoid stacking. If you would compute TWO rules in the same group based on the same partial vessel info, both must be "needs_clarification" instead.

MISSING_INPUTS
==============
- For "needs_clarification" or "not_applicable" due to missing data, list the vessel fields you'd need. Use snake_case names like "cargo_route", "cargo_tonnage", "cargo_form", "duration_hours", "delay_minutes", etc.
- Empty list if no inputs are missing.

RULES FOR `formula` (when status="computed")
=============================================
- Pure arithmetic only. Allowed operators: + - * / // % **, parentheses.
- Allowed names: ONLY the keys you put in `values` plus the helpers min(), max(), ceil(), floor(), round(), abs().
- No variables, no comparisons, no if/else, no comprehensions, no function defs.
- BRACKETS / TIERED RATES: pick the correct bracket yourself based on the vessel's value; write the formula for that bracket only.
  Example (towage 50,001-100,000 GT = 147074.38 flat): formula="bracket_fee", values={"bracket_fee": 147074.38}.
- BASE + INCREMENTAL: write it out, e.g.
  formula="base + (gt / 100) * rate * operations",
  values={"base": 18608.61, "gt": 51300, "rate": 9.72, "operations": 2}.
- PER-PERIOD charges (per 24h): include periods in values; multiply in the formula. Round up partial periods with ceil() if rule says "or part thereof".
- MINIMUMS/MAXIMUMS stated in the rule: bake them in with max(...) / min(...).
  Example: formula="max(0.65 * gt, 25000)" for "0.65/GT, min 25000".
- PERCENTAGE SURCHARGES: write base * pct / 100.

When status != "computed": set formula="0" and values={}.

SIBLING CONTEXT (CRITICAL — read this carefully)
=================================================
The input includes a `context` block:
  {
    "category_key":        "<group identifier, e.g. cargo_dues>",
    "sibling_count":       <int — number of OTHER rules sharing this group>,
    "sibling_charge_types":["...", "..."]
  }

Siblings are OTHER extracted rules whose charge_type starts with the same
two tokens as this rule (e.g. cargo_dues_iron_ore_imports and
cargo_dues_breakbulk_general are siblings under category_key "cargo_dues").

Use sibling context as your primary anti-stacking signal:

- If sibling_count == 0:
  This rule stands alone in its category. Compute it if the vessel data
  supports it; otherwise mark not_applicable. Do NOT mark needs_clarification
  unless the rule itself defines a missing input.

- If sibling_count >= 1:
  You are looking at one of N mutually-exclusive variants of the same
  charge family. You MUST decide collectively:
    a) If the vessel data uniquely picks THIS variant (e.g. rule says
       "imports", vessel has direction="import"), set status="computed".
    b) If the vessel data uniquely picks a DIFFERENT sibling, set
       status="not_applicable" with skip_reason explaining which sibling fits.
    c) If the vessel data does not pick any specific sibling, set
       status="needs_clarification" — NEVER compute one variant and let the
       siblings also compute. Stacking variants of the same family is the
       single biggest failure mode.

Special cases for sibling_count >= 1:
  - cargo_dues_*: requires cargo direction (import/export/coastwise/
    transhipped) and usually cargo form (bulk/breakbulk/container) and
    commodity. If any of these is missing from vessel data → needs_clarification.
  - light_dues_*: requires registration class (foreign-going / SA-registered
    licensed / coaster). If unknown → needs_clarification.
  - port_dues_* with variants like _small_vessel, _double_hull,
    _green_award: pick the base/default variant only when other variants
    are clearly inapplicable (e.g. small_vessel does not apply to a 51,300 GT
    bulker — base variant computes, small_vessel is not_applicable).

GENERAL ANTI-STACKING & DEFAULTS
================================
- Standard mandatory port-call charges (port dues, light dues, pilotage, tug
  assistance, VTS, running lines, mooring) SHOULD compute when the rule is
  the default rate, sibling_count tells you it's the only variant, and vessel
  data supports the formula.
- Surcharges (OWH, cancellation, late arrival, extra tug) → "not_applicable"
  by default. Only fire when explicit trigger data is in vessel profile.
- Drydock charges → "not_applicable" unless vessel profile says drydocking.
- Optional services (launch hire, crane hire, fire equipment) → "not_applicable"
  unless vessel profile shows the service was used.
- CARGO DUES (cargo_dues_*) → ALWAYS "needs_clarification". Cargo dues are
  conventionally billed by the cargo agent (consignee/shipper) on a separate
  invoice, NOT on the vessel's port-call bill. Even if the vessel data
  contains cargo_type / cargo_tonnage / cargo_direction, do NOT compute
  cargo dues — surface them as a clarification with category_group="cargo_dues"
  so the user can decide whether to include them.

PER-SERVICE / PER-OPERATION MULTIPLIER (deterministic rule — apply consistently)
================================================================================
If the rule's calculation_logic OR charge_type indicates the charge is
"per service", "per operation", "per movement", "per call", "in and out",
"entry and exit", or any equivalent phrasing that implies the charge fires
once per port operation:
  - Look for vessel.number_of_operations in the vessel data.
  - If set: multiply the per-service amount by number_of_operations and
    include "operations" (or equivalent name) explicitly in `values`.
    Example: formula="rate * operations", values={"rate": 73537.19, "operations": 2}.
  - If not set: compute for ONE operation only, do NOT multiply.

If the rule does NOT carry per-service language (e.g. "Light Dues per 100 GT"
which is per-call regardless of operation count, or "Port Dues for vessels
at berth" which is per-period not per-service): do NOT multiply by
number_of_operations.

Charges that are typically PER-OPERATION when phrased as such:
  pilotage, tug assistance / towage, mooring, running of vessel lines,
  berthing services.

Charges that are typically PER-CALL (NOT multiplied by operations):
  light dues, port dues, VTS, cargo dues, harbour dues.

Apply this rule consistently — same input must produce the same multiplier
choice across runs.

OUTPUT
======
Return only the JSON. No markdown, no commentary, no code fences.
"""


_FORMULA_RE_ALLOWED = re.compile(r"^[\s0-9a-zA-Z_+\-*/().,%]+$")


def _extract_json(text: str) -> str:
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return text


def _extract_json_array(text: str) -> str:
    """Strip code fences and return the outermost JSON array `[ ... ]`."""
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return text


# The batch prompt re-uses the same per-rule decision tree, but wraps it for
# N items in one call.
def _build_batch_prompt() -> str:
    # Slice the original per-rule prompt from "STATUS DECISION TREE" onwards
    # so we don't duplicate the I/O shape in the batch wrapper.
    marker = "STATUS DECISION TREE"
    idx = _PER_RULE_CALC_PROMPT.find(marker)
    decision_tree_and_below = (
        _PER_RULE_CALC_PROMPT[idx:] if idx >= 0 else _PER_RULE_CALC_PROMPT
    )
    return (
        """\
You are a maritime tariff calculator. You will receive ONE vessel and N tariff
rules in `items`. Decide each rule INDEPENDENTLY using the rules below.

OUTPUT FORMAT (CRITICAL)
========================
Return ONLY a JSON ARRAY of N objects, in the SAME ORDER as input items.
No markdown, no code fences, no commentary. Each object has the exact shape:

{
  "status": "computed" | "not_applicable" | "needs_clarification",
  "category_group": "<short snake_case group name, or null>",
  "missing_inputs": ["<vessel field name>", ...],
  "skip_reason": "<short reason if not computed, else null>",
  "formula": "<arithmetic expression as Python-syntax string>",
  "values": { "<name>": <number>, ... },
  "explanation": "<1-2 sentence human-readable explanation>"
}

The array length MUST equal len(items). The ORDER MUST match.

"""
        + decision_tree_and_below
        + """

REMINDER ON INDEPENDENCE
========================
Treat each item independently. Do not let one item's interpretation bleed
into another. Each item carries its own `context` block — use only that
item's context for its decision.
"""
    )


_PER_RULE_BATCH_PROMPT = _build_batch_prompt()


def _safe_eval(formula: str, values: Dict[str, float]) -> float:
    """
    Evaluate `formula` with `values` substituted, using simpleeval for safety.
    Allowed names: keys of `values` + math helpers (min/max/ceil/floor/round/abs).
    """
    if not _FORMULA_RE_ALLOWED.match(formula):
        # Reject anything with characters we don't expect — defence in depth.
        raise InvalidExpression(
            f"formula contains disallowed characters: {formula!r}"
        )

    evaluator = SimpleEval(
        names={**values},
        functions={
            "min": min,
            "max": max,
            "ceil": math.ceil,
            "floor": math.floor,
            "round": round,
            "abs": abs,
        },
    )
    result = evaluator.eval(formula)
    if not isinstance(result, (int, float)):
        raise InvalidExpression(
            f"formula evaluated to non-numeric: {result!r}"
        )
    return float(result)


class PerRuleCalculator:
    """
    Calculates one charge per ExtractedRule by asking Gemini to produce
    {formula, values} and evaluating it deterministically.
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()
        self._llm: Optional[ChatGoogleGenerativeAI] = None

    def initialize(self) -> None:
        """
        Build a cheap, fast LLM for per-rule processing:
          - Model: gemini-2.0-flash-lite  (~6× cheaper than 2.5-flash overall)
          - thinking_budget=0             (no hidden reasoning tokens billed)
          - temperature=0                 (deterministic shape)
        Rule extraction (in agents.py) keeps gemini-2.5-flash with thinking.
        """
        per_rule_model = os.environ.get(
            "PER_RULE_MODEL", "gemini-2.5-flash"
        )
        self._llm = ChatGoogleGenerativeAI(
            model=per_rule_model,
            google_api_key=self.config.gemini_api_key,
            temperature=0.0,
            convert_system_message_to_human=False,
            thinking_budget=0,        # OFF — task is structured, no reasoning needed
        )
        logger.info(
            "PerRuleCalculator initialised with model=%s, thinking=off",
            per_rule_model,
        )
        print(
            f"[INIT] PerRuleCalculator using model={per_rule_model} thinking=off",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Sibling grouping (computed once per call, fed into every prompt)
    # ------------------------------------------------------------------
    @staticmethod
    def _category_key(charge_type: str) -> str:
        """
        Heuristic group key: first two underscore-separated tokens.
        Examples:
          cargo_dues_breakbulk_iron_ore   → "cargo_dues"
          port_dues_basic_fee             → "port_dues"
          light_dues_per_100_gt           → "light_dues"
          tugs_vessel_assistance_durban   → "tugs_vessel"
          vts_charges_durban_saldanha_bay → "vts_charges"
          pilotage                        → "pilotage"
        """
        parts = charge_type.split("_", 2)
        return "_".join(parts[:2]) if len(parts) >= 2 else parts[0]

    def _build_sibling_index(self, rule_store: RuleStore) -> Dict[str, List[str]]:
        """Map category_key → list of charge_types in that group."""
        index: Dict[str, List[str]] = {}
        for r in rule_store.rules:
            key = self._category_key(r.charge_type)
            index.setdefault(key, []).append(r.charge_type)
        return index

    # ------------------------------------------------------------------
    # Public API (async, parallel)
    # ------------------------------------------------------------------
    async def execute(
        self,
        vessel_profile: VesselProfile,
        rule_store: RuleStore,
        concurrency: int = 10,
        batch_size: int = 5,
    ) -> Tuple[CalculationResult, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Calculate every rule in parallel batches.
          - Rules grouped into batches of `batch_size` (default 5).
          - Up to `concurrency` batches in flight at once.
          - Each batch is one LLM call; LLM returns a JSON array of N outcomes.
        Returns (result, skipped, needs_clarification).
        """
        if self._llm is None:
            raise RuntimeError(
                "PerRuleCalculator is not initialised. Call initialize() first."
            )

        vessel_json = vessel_profile.model_dump(exclude_none=True)
        sibling_index = self._build_sibling_index(rule_store)
        semaphore = asyncio.Semaphore(concurrency)
        t0 = time.monotonic()

        # ── Build batches with original indexes ──────────────────────────
        rules_with_idx = list(enumerate(rule_store.rules))
        batches = [
            rules_with_idx[i : i + batch_size]
            for i in range(0, len(rules_with_idx), batch_size)
        ]

        async def run_batch(batch: List[Tuple[int, ExtractedRule]]) -> List[Dict[str, Any]]:
            async with semaphore:
                try:
                    return await self._process_batch_async(
                        batch, vessel_json, sibling_index
                    )
                except Exception as exc:
                    logger.exception(
                        "Per-rule batch raised on %d rules", len(batch)
                    )
                    # Fail-safe: return one skipped per rule in this batch
                    return [
                        {
                            "_kind": "skipped",
                            "charge_type": rule.charge_type,
                            "reason": f"batch_failed: {type(exc).__name__}: {exc}",
                            "rule_index": idx,
                        }
                        for idx, rule in batch
                    ]

        batch_results = await asyncio.gather(*(run_batch(b) for b in batches))
        outcomes: List[Dict[str, Any]] = [
            o for batch_out in batch_results for o in batch_out
        ]

        elapsed = time.monotonic() - t0
        print(
            f"[PER_RULE] {len(outcomes)} rules in {len(batches)} batches "
            f"(batch_size={batch_size}, concurrency={concurrency}) — {elapsed:.1f}s",
            flush=True,
        )

        # ── Sort outcomes into three buckets ──────────────────────────────
        charges: List[CalculatedCharge] = []
        skipped: List[Dict[str, Any]] = []
        ambiguous: List[Dict[str, Any]] = []
        subtotal = 0.0

        for outcome in outcomes:
            kind = outcome.pop("_kind")
            if kind == "computed":
                charge = outcome["_charge"]
                charges.append(charge)
                subtotal += charge.amount
            elif kind == "needs_clarification":
                ambiguous.append(outcome)
            else:  # "skipped"
                skipped.append(outcome)

        clarifications = self._group_clarifications(ambiguous, rule_store)

        result = CalculationResult(
            vessel_name=vessel_profile.name,
            port_name=rule_store.port_name,
            charges=charges,
            subtotal=round(subtotal, 2),
        )

        logger.info(
            "PerRuleCalculator: %d OK, %d skipped, %d clarifications (from %d ambiguous), subtotal=%.2f, %.1fs",
            len(charges), len(skipped), len(clarifications), len(ambiguous), subtotal, elapsed,
        )
        return result, skipped, clarifications

    # ------------------------------------------------------------------
    # Clarification grouping
    # ------------------------------------------------------------------
    def _group_clarifications(
        self,
        ambiguous: List[Dict[str, Any]],
        rule_store: RuleStore,
    ) -> List[Dict[str, Any]]:
        """
        Group ambiguous rules by category_group. Standalone ambiguities
        (category_group is None) are returned one-per-row.
        """
        # Map rule_index -> rule for amount-range estimation later.
        rules_by_idx = {idx: r for idx, r in enumerate(rule_store.rules)}

        groups: Dict[str, List[Dict[str, Any]]] = {}
        standalone: List[Dict[str, Any]] = []

        for item in ambiguous:
            grp = item.get("category_group")
            if not grp:
                standalone.append(item)
            else:
                groups.setdefault(grp, []).append(item)

        result: List[Dict[str, Any]] = []
        for grp, items in groups.items():
            # Collect missing inputs across all candidates (deduped, ordered).
            missing: List[str] = []
            for it in items:
                for m in it.get("missing_inputs", []) or []:
                    if m not in missing:
                        missing.append(m)
            result.append({
                "category_group": grp,
                "candidates": [it["charge_type"] for it in items],
                "missing_inputs": missing,
                "reason": items[0].get("reason") or "ambiguous_category_match",
                "candidate_count": len(items),
            })

        for s in standalone:
            result.append({
                "category_group": None,
                "candidates": [s["charge_type"]],
                "missing_inputs": s.get("missing_inputs", []) or [],
                "reason": s.get("reason") or "needs_clarification",
                "candidate_count": 1,
            })

        return result

    # ------------------------------------------------------------------
    # Per-rule processing (async)
    # ------------------------------------------------------------------
    async def _process_batch_async(
        self,
        batch: List[Tuple[int, ExtractedRule]],
        vessel_json: Dict[str, Any],
        sibling_index: Dict[str, List[str]],
    ) -> List[Dict[str, Any]]:
        """
        Process a batch of rules in ONE LLM call. The LLM returns a JSON
        array of N outcomes; we map each to a tagged dict.

        Order is preserved by the LLM (we instruct it to). If the LLM
        returns the wrong count or order, we fall back per-rule with a
        clear skip reason.
        """
        # Build the per-rule items in this batch
        items = []
        for idx, rule in batch:
            cat_key = self._category_key(rule.charge_type)
            siblings = [s for s in sibling_index.get(cat_key, [])
                        if s != rule.charge_type]
            items.append({
                "rule_index": idx,
                "rule": {
                    "charge_type": rule.charge_type,
                    "calculation_logic": rule.calculation_logic,
                    "extracted_parameters": rule.extracted_parameters,
                    "required_variables": rule.required_variables,
                    "conditions": rule.conditions,
                    "extraction_confidence": rule.extraction_confidence,
                },
                "context": {
                    "category_key": cat_key,
                    "sibling_count": len(siblings),
                    "sibling_charge_types": siblings,
                },
            })

        prompt_payload = {
            "vessel": vessel_json,
            "items": items,
        }

        messages = [
            SystemMessage(content=_PER_RULE_BATCH_PROMPT),
            HumanMessage(content=json.dumps(prompt_payload, indent=2)),
        ]

        try:
            response = await self._llm.ainvoke(messages)
        except Exception as exc:
            return [
                {
                    "_kind": "skipped",
                    "charge_type": rule.charge_type,
                    "reason": f"llm_call_failed: {type(exc).__name__}: {exc}",
                    "rule_index": idx,
                }
                for idx, rule in batch
            ]

        # Parse a JSON ARRAY (the batch wrapper)
        raw_array = _extract_json_array(response.content)
        try:
            payloads = json.loads(raw_array)
            if not isinstance(payloads, list):
                raise ValueError("response was not a JSON array")
            if len(payloads) != len(batch):
                raise ValueError(
                    f"expected {len(batch)} items, got {len(payloads)}"
                )
        except (json.JSONDecodeError, ValueError) as exc:
            return [
                {
                    "_kind": "skipped",
                    "charge_type": rule.charge_type,
                    "reason": f"batch_response_malformed: {exc}",
                    "rule_index": idx,
                    "raw_excerpt": (raw_array or "")[:300],
                }
                for idx, rule in batch
            ]

        # Process each payload
        results: List[Dict[str, Any]] = []
        for (idx, rule), payload in zip(batch, payloads):
            results.append(self._payload_to_outcome(payload, rule, idx))
        return results

    def _payload_to_outcome(
        self, payload: Dict[str, Any], rule: ExtractedRule, idx: int
    ) -> Dict[str, Any]:
        """Convert a single LLM payload dict into a tagged outcome."""
        outcome = self._payload_to_outcome_inner(payload, rule)
        outcome["rule_index"] = idx
        return outcome

    def _payload_to_outcome_inner(
        self, payload: Dict[str, Any], rule: ExtractedRule
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {
                "_kind": "skipped",
                "charge_type": rule.charge_type,
                "reason": f"item_not_object: {type(payload).__name__}",
            }

        # Accept either new "status" or legacy "applicable"
        status = payload.get("status")
        if status is None:
            status = "computed" if payload.get("applicable") else "not_applicable"

        if status == "needs_clarification":
            return {
                "_kind": "needs_clarification",
                "charge_type": rule.charge_type,
                "category_group": payload.get("category_group"),
                "missing_inputs": payload.get("missing_inputs", []) or [],
                "reason": payload.get("skip_reason") or payload.get("explanation") or "needs_clarification",
            }

        if status != "computed":
            return {
                "_kind": "skipped",
                "charge_type": rule.charge_type,
                "reason": payload.get("skip_reason") or payload.get("explanation") or "marked_not_applicable",
                "category_group": payload.get("category_group"),
                "missing_inputs": payload.get("missing_inputs", []) or [],
            }

        # status == "computed"
        formula = payload.get("formula", "0")
        values = payload.get("values", {}) or {}

        coerced_values: Dict[str, float] = {}
        for k, v in values.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return {
                    "_kind": "skipped",
                    "charge_type": rule.charge_type,
                    "reason": f"non_numeric_value: {k}={v!r}",
                }
            coerced_values[k] = float(v)

        try:
            amount = _safe_eval(formula, coerced_values)
        except (NameNotDefined, InvalidExpression, ZeroDivisionError, SyntaxError) as exc:
            return {
                "_kind": "skipped",
                "charge_type": rule.charge_type,
                "reason": f"formula_eval_failed: {type(exc).__name__}: {exc}",
                "formula": formula,
                "values": coerced_values,
            }

        amount = round(amount, 2)
        charge = CalculatedCharge(
            charge_type=rule.charge_type,
            description=rule.calculation_logic,
            amount=amount,
            trace={
                "formula": formula,
                "values": coerced_values,
                "explanation": payload.get("explanation", ""),
                "category_group": payload.get("category_group"),
                "extraction_confidence": rule.extraction_confidence,
                "unit": "ZAR",
            },
        )
        return {"_kind": "computed", "_charge": charge}
