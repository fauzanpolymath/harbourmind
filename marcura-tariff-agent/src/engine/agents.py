"""
agents.py  (src/engine/agents.py)
-----------------------------------
LangChain / Gemini agents for HarbourMind Priority 3a.

Agents
------
VesselQueryParserAgent
    Parses vessel input in any format (natural language, JSON string, or dict)
    and returns a normalised VesselProfile.

RuleExtractionAgent
    Reads a tariff document (string or dict) and discovers ALL charges
    present in the text, returning a RuleStore of ExtractedRule objects.
    Charge types are discovered from the document — never hardcoded.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional, Union

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from src.core.models import (
    ExtractedRule,
    RuleStore,
    VesselProfile,
    ValidationReport,
    CalculationResult,
    CalculatedCharge,
    ProcessedResult,
    ExceptionCharge,
    ClarificationPrompt,
    UpdatedResult,
)
from src.utils.config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_VESSEL_PARSER_SYSTEM_PROMPT = """\
You are a maritime vessel data parser for the HarbourMind tariff calculation system.

Your task is to extract vessel and port-call information from ANY input format
(natural language, JSON string, or structured dict) and return a single, clean
JSON object.

OUTPUT RULES
============
1. Return ONLY a valid JSON object — no markdown, no code fences, no commentary.
2. Use exactly these field names (snake_case):
     name, imo_number, call_sign, type, gross_tonnage, net_tonnage,
     deadweight_tonnage, length_overall, beam, draft, port,
     cargo_type, cargo_tonnage, days_in_port
3. Preserve numeric values EXACTLY as given — do not round or convert units.
4. Preserve string values EXACTLY as given (e.g. "Bulk Carrier" stays "Bulk Carrier").
5. Use JSON null for any field not present in the input.
6. All tonnage fields are in metric tonnes; dimensions are in metres.

EXAMPLE INPUT:
  {"type": "Bulk Carrier", "gross_tonnage": 51300, "port": "Durban"}

EXAMPLE OUTPUT:
  {
    "name": null,
    "imo_number": null,
    "call_sign": null,
    "type": "Bulk Carrier",
    "gross_tonnage": 51300,
    "net_tonnage": null,
    "deadweight_tonnage": null,
    "length_overall": null,
    "beam": null,
    "draft": null,
    "port": "Durban",
    "cargo_type": null,
    "cargo_tonnage": null,
    "days_in_port": null
  }
"""

_RULE_EXTRACTION_SYSTEM_PROMPT = """\
You are a maritime tariff rule extractor for the HarbourMind port cost calculation system.

Your task is to read a port tariff document and extract EVERY charge or fee mentioned.
You MUST discover charges from the actual document — never use a predefined or hardcoded list.

OUTPUT RULES
============
1. Return ONLY a valid JSON object — no markdown, no code fences, no commentary.
2. The JSON object must have this structure:
   {
     "port_name": "<lowercase port name>",
     "rules": [ ...list of charge objects... ],
     "extraction_timestamp": null
   }
3. Each charge object must have these fields:
   {
     "charge_type": "<lowercase snake_case name, e.g. pilotage, port_dues, towage>",
     "calculation_logic": "<plain English explanation of how the fee is calculated>",
     "extracted_parameters": {
       "<param_name>": <numeric_value>,
       ...
     },
     "extraction_confidence": <float 0.0–1.0>,
     "required_variables": ["<vessel_field>", ...],
     "conditions": "<applicability conditions or null>"
   }
4. charge_type MUST be lowercase snake_case.
5. extracted_parameters must contain ALL numeric values found for that charge
   (base fees, rates, multipliers, GT bands, durations, etc.).
6. required_variables lists the VesselProfile fields needed to compute the charge
   (e.g. "gross_tonnage", "days_in_port", "deadweight_tonnage").
7. extraction_confidence: use 0.95+ when the document is explicit; lower when
   values must be inferred.
8. Extract EVERY distinct charge in the document — do not merge or skip any.

VESSEL PROFILE FIELDS AVAILABLE FOR required_variables:
  gross_tonnage, net_tonnage, deadweight_tonnage, length_overall, beam, draft,
  cargo_tonnage, days_in_port, cargo_type, type, port
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> str:
    """
    Strip markdown code fences and any surrounding prose, returning the
    raw JSON string.  Handles:
      - Plain JSON
      - ```json ... ```
      - ``` ... ```
      - JSON preceded/followed by commentary
    """
    # Remove code fences
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)
    text = text.strip()

    # If there is still non-JSON prefix/suffix, find the outermost { }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    return text


def _build_llm(config: Config) -> ChatGoogleGenerativeAI:
    """Construct a ChatGoogleGenerativeAI instance from Config."""
    return ChatGoogleGenerativeAI(
        model=config.gemini_model,
        google_api_key=config.google_api_key,
        temperature=0.0,          # deterministic output for structured extraction
        convert_system_message_to_human=False,
    )


# ---------------------------------------------------------------------------
# VesselQueryParserAgent
# ---------------------------------------------------------------------------

class VesselQueryParserAgent:
    """
    Parses vessel input in any format and returns a normalised VesselProfile.

    Supported input formats
    -----------------------
    - dict           e.g. {"type": "Bulk Carrier", "gross_tonnage": 51300}
    - JSON string    e.g. '{"type": "Bulk Carrier", "gross_tonnage": 51300}'
    - Natural lang   e.g. "A 51,300 GT bulk carrier calling at Durban"

    Usage
    -----
        agent = VesselQueryParserAgent(config=cfg)
        agent.initialize()
        profile = agent.execute(vessel_input)
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()
        self._llm: Optional[ChatGoogleGenerativeAI] = None

    def initialize(self) -> None:
        """Set up the Gemini LLM via LangChain.  Must be called before execute()."""
        self._llm = _build_llm(self.config)
        logger.info(
            "VesselQueryParserAgent initialised with model=%s", self.config.gemini_model
        )

    # ------------------------------------------------------------------
    # Fast-path: attempt direct dict→VesselProfile without calling the LLM
    # ------------------------------------------------------------------
    @staticmethod
    def _try_direct_parse(data: dict) -> Optional[VesselProfile]:
        """
        If the input already maps cleanly to VesselProfile fields, return
        the profile without making an LLM call.  Returns None on any error.
        """
        try:
            return VesselProfile(**data)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # LLM-backed parse
    # ------------------------------------------------------------------
    def _llm_parse(self, input_str: str) -> VesselProfile:
        messages = [
            SystemMessage(content=_VESSEL_PARSER_SYSTEM_PROMPT),
            HumanMessage(content=f"Parse this vessel input:\n\n{input_str}"),
        ]
        response = self._llm.invoke(messages)
        raw = _extract_json(response.content)
        logger.debug("VesselQueryParserAgent raw LLM output:\n%s", raw)
        data = json.loads(raw)
        # Drop null values so Pydantic uses model defaults
        data = {k: v for k, v in data.items() if v is not None}
        return VesselProfile(**data)

    def execute(self, vessel_input: Union[str, dict]) -> VesselProfile:
        """
        Parse vessel input and return a normalised VesselProfile.

        Parameters
        ----------
        vessel_input : str | dict
            Natural language description, JSON string, or plain dict.

        Returns
        -------
        VesselProfile
        """
        if self._llm is None:
            raise RuntimeError(
                "VesselQueryParserAgent is not initialised.  Call initialize() first."
            )

        # ── 1. Normalise input ──────────────────────────────────────────
        if isinstance(vessel_input, dict):
            # Try fast-path before calling the LLM
            fast = self._try_direct_parse(vessel_input)
            if fast is not None:
                logger.debug("VesselQueryParserAgent: fast-path dict parse succeeded.")
                return fast
            input_str = json.dumps(vessel_input)

        elif isinstance(vessel_input, str):
            # Attempt to detect and parse JSON strings locally first
            stripped = vessel_input.strip()
            if stripped.startswith("{"):
                try:
                    parsed_dict = json.loads(stripped)
                    fast = self._try_direct_parse(parsed_dict)
                    if fast is not None:
                        logger.debug(
                            "VesselQueryParserAgent: fast-path JSON string parse succeeded."
                        )
                        return fast
                except json.JSONDecodeError:
                    pass
            input_str = vessel_input

        else:
            raise TypeError(
                f"vessel_input must be str or dict, got {type(vessel_input).__name__}"
            )

        # ── 2. LLM parse ───────────────────────────────────────────────
        return self._llm_parse(input_str)


# ---------------------------------------------------------------------------
# RuleExtractionAgent
# ---------------------------------------------------------------------------

class RuleExtractionAgent:
    """
    Extracts tariff rules from a port tariff document and returns a RuleStore.

    Charges are DISCOVERED from the document content — the agent never uses
    a hardcoded list of charge types.

    Usage
    -----
        agent = RuleExtractionAgent(config=cfg)
        agent.initialize()
        rule_store = agent.execute(tariff_data, port_name="durban")
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()
        self._llm: Optional[ChatGoogleGenerativeAI] = None

    def initialize(self) -> None:
        """Set up the Gemini LLM via LangChain.  Must be called before execute()."""
        self._llm = _build_llm(self.config)
        logger.info(
            "RuleExtractionAgent initialised with model=%s", self.config.gemini_model
        )

    def execute(
        self,
        tariff_data: Union[str, dict],
        port_name: str,
    ) -> RuleStore:
        """
        Extract all tariff rules from a document and return a RuleStore.

        Parameters
        ----------
        tariff_data : str | dict
            Full text of the tariff document, or a pre-parsed dict structure.
        port_name : str
            Name of the port (used for context and stored in the RuleStore).

        Returns
        -------
        RuleStore
            Container with all discovered ExtractedRule objects.
        """
        if self._llm is None:
            raise RuntimeError(
                "RuleExtractionAgent is not initialised.  Call initialize() first."
            )

        # ── 1. Normalise tariff data to string ─────────────────────────
        if isinstance(tariff_data, dict):
            tariff_str = json.dumps(tariff_data, indent=2)
        elif isinstance(tariff_data, str):
            tariff_str = tariff_data
        else:
            raise TypeError(
                f"tariff_data must be str or dict, got {type(tariff_data).__name__}"
            )

        # ── 2. Call LLM ────────────────────────────────────────────────
        messages = [
            SystemMessage(content=_RULE_EXTRACTION_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Port name: {port_name}\n\n"
                    f"Tariff document:\n{tariff_str}"
                )
            ),
        ]
        response = self._llm.invoke(messages)
        raw = _extract_json(response.content)
        logger.debug("RuleExtractionAgent raw LLM output:\n%s", raw)

        # ── 3. Parse response into RuleStore ───────────────────────────
        data = json.loads(raw)

        rules: list[ExtractedRule] = []
        for rule_dict in data.get("rules", []):
            try:
                rules.append(ExtractedRule(**rule_dict))
            except Exception as exc:                          # noqa: BLE001
                logger.warning(
                    "Skipping malformed rule %r: %s", rule_dict.get("charge_type"), exc
                )

        rule_store = RuleStore(
            port_name=data.get("port_name", port_name.lower()),
            rules=rules,
        )

        logger.info(
            "RuleExtractionAgent extracted %d rules for port '%s'.",
            len(rules),
            rule_store.port_name,
        )
        return rule_store


# ---------------------------------------------------------------------------
# CompletenessValidatorAgent
# ---------------------------------------------------------------------------

_COMPLETENESS_VALIDATOR_SYSTEM_PROMPT = """\
You are a tariff completeness validator for the HarbourMind port cost system.

Your task is to audit a list of extracted rules against the original tariff document
and identify any charges that appear in the document but were NOT extracted.

INPUT
=====
You will receive:
1. A list of already-extracted charges with their charge_type names
2. The full raw tariff document text

TASK
====
1. Re-read the tariff document carefully
2. Identify ALL distinct charges mentioned (by any name or description)
3. Compare against the extracted list
4. Report any charges that are mentioned in the document but missing from the extraction

OUTPUT
======
Return ONLY a valid JSON object with this structure:
{
  "all_rules_found": <boolean>,
  "missed_charges": ["<charge_name>", ...],
  "confidence_level": <float 0.0-1.0>,
  "recommendations": ["<suggestion>", ...]
}

FIELD DEFINITIONS
=================
- all_rules_found: true only if you find NO missed charges in the document
- missed_charges: list of charge types/names found in doc but not in the extracted list
- confidence_level: your confidence in this audit (higher = more thorough re-read, lower = uncertain)
- recommendations: suggestions for improving extraction (e.g. "use stronger OCR", "re-parse section X")
"""

class CompletenessValidatorAgent:
    """
    Validates that all tariff charges from a document were extracted.

    Re-reads the original document and compares against the extracted RuleStore
    to identify any missed charges.  Uses Gemini for intelligent, document-driven
    validation (not hardcoded charge lists).

    Usage
    -----
        validator = CompletenessValidatorAgent(config=cfg)
        validator.initialize()
        report = validator.execute(rule_store, tariff_data)
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()
        self._llm: Optional[ChatGoogleGenerativeAI] = None

    def initialize(self) -> None:
        """Set up the Gemini LLM via LangChain."""
        self._llm = _build_llm(self.config)
        logger.info(
            "CompletenessValidatorAgent initialised with model=%s",
            self.config.gemini_model,
        )

    def execute(
        self, rule_store: RuleStore, tariff_data: Union[str, dict]
    ) -> dict:
        """
        Validate completeness of rule extraction against the original document.

        Parameters
        ----------
        rule_store : RuleStore
            The rules that were extracted.
        tariff_data : str | dict
            The original tariff document (full text or pre-parsed structure).

        Returns
        -------
        dict
            ValidationReport fields as dict:
            - all_rules_found (bool)
            - missed_charges (list)
            - confidence_level (float)
            - recommendations (list)
        """
        if self._llm is None:
            raise RuntimeError(
                "CompletenessValidatorAgent not initialised. Call initialize() first."
            )

        # ── 1. Normalise tariff data ────────────────────────────────────
        if isinstance(tariff_data, dict):
            tariff_str = json.dumps(tariff_data, indent=2)
        elif isinstance(tariff_data, str):
            tariff_str = tariff_data
        else:
            raise TypeError(f"tariff_data must be str or dict, got {type(tariff_data)}")

        # ── 2. Summarise extracted charges ──────────────────────────────
        extracted_charges = [rule.charge_type for rule in rule_store.rules]
        extracted_summary = json.dumps(
            {
                "extracted_charge_count": len(extracted_charges),
                "extracted_charges": extracted_charges,
            },
            indent=2,
        )

        # ── 3. Call LLM ────────────────────────────────────────────────
        messages = [
            SystemMessage(content=_COMPLETENESS_VALIDATOR_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Extracted charges so far:\n{extracted_summary}\n\n"
                    f"Tariff document:\n{tariff_str}"
                )
            ),
        ]
        response = self._llm.invoke(messages)
        raw = _extract_json(response.content)
        logger.debug("CompletenessValidatorAgent raw LLM output:\n%s", raw)

        # ── 4. Parse and return ────────────────────────────────────────
        data = json.loads(raw)
        validation = ValidationReport(
            all_rules_found=data.get("all_rules_found", False),
            missed_charges=data.get("missed_charges", []),
            confidence_level=data.get("confidence_level", 0.5),
            recommendations=data.get("recommendations", []),
        )

        logger.info(
            "CompletenessValidator: all_rules_found=%s, missed_count=%d, confidence=%.2f",
            validation.all_rules_found,
            len(validation.missed_charges),
            validation.confidence_level or 0,
        )

        return validation.dict(exclude_none=True)


# ---------------------------------------------------------------------------
# SchemaValidatorAgent
# ---------------------------------------------------------------------------

_SCHEMA_VALIDATOR_SYSTEM_PROMPT = """\
You are a schema validator for the HarbourMind tariff calculation system.

Your task is to check whether a vessel profile (ship data) contains all the
information required by the tariff rules that will be applied.

INPUT
=====
You will receive:
1. A vessel profile with available fields and their values
2. A list of tariff rules, each with a required_variables list

TASK
====
1. For each rule, check if all required_variables are present in the vessel profile
2. Identify which fields are MISSING from the vessel profile
3. Count how many rules can be fully applied (all required vars present)
4. Flag any data quality concerns

OUTPUT
======
Return ONLY a valid JSON object with this structure:
{
  "valid": <boolean>,
  "missing_fields": ["<field_name>", ...],
  "applicable_rules": <integer count>,
  "warnings": ["<warning>", ...]
}

FIELD DEFINITIONS
=================
- valid: true only if vessel_profile has all fields required by any rule
- missing_fields: list of unique field names required by rules but missing from profile
- applicable_rules: how many rules have ALL their required_variables in the profile
- warnings: list of concerns (e.g. "vessel type not specified", "no cargo tonnage")
"""


class SchemaValidatorAgent:
    """
    Validates that a vessel profile has all data required by the tariff rules.

    Checks if the VesselProfile contains all fields listed in the
    required_variables of each ExtractedRule.  Detects missing vessel data
    early, before calculation attempts.

    Usage
    -----
        validator = SchemaValidatorAgent(config=cfg)
        validator.initialize()
        report = validator.execute(vessel_profile, rule_store)
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()
        self._llm: Optional[ChatGoogleGenerativeAI] = None

    def initialize(self) -> None:
        """Set up the Gemini LLM via LangChain."""
        self._llm = _build_llm(self.config)
        logger.info(
            "SchemaValidatorAgent initialised with model=%s", self.config.gemini_model
        )

    def execute(self, vessel_profile: VesselProfile, rule_store: RuleStore) -> dict:
        """
        Validate that vessel profile has all required data for the rules.

        Parameters
        ----------
        vessel_profile : VesselProfile
            The vessel and port call information.
        rule_store : RuleStore
            The extracted tariff rules.

        Returns
        -------
        dict
            ValidationReport fields as dict:
            - valid (bool)
            - missing_fields (list)
            - applicable_rules (int)
            - warnings (list)
        """
        if self._llm is None:
            raise RuntimeError(
                "SchemaValidatorAgent not initialised. Call initialize() first."
            )

        # ── 1. Serialise inputs ─────────────────────────────────────────
        vessel_dict = vessel_profile.dict(exclude_none=True)
        vessel_summary = json.dumps(
            {
                "available_fields": list(vessel_dict.keys()),
                "field_values": vessel_dict,
            },
            indent=2,
        )

        rules_summary = json.dumps(
            [
                {
                    "charge_type": rule.charge_type,
                    "required_variables": rule.required_variables,
                }
                for rule in rule_store.rules
            ],
            indent=2,
        )

        # ── 2. Call LLM ────────────────────────────────────────────────
        messages = [
            SystemMessage(content=_SCHEMA_VALIDATOR_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Vessel Profile:\n{vessel_summary}\n\n"
                    f"Tariff Rules:\n{rules_summary}"
                )
            ),
        ]
        response = self._llm.invoke(messages)
        raw = _extract_json(response.content)
        logger.debug("SchemaValidatorAgent raw LLM output:\n%s", raw)

        # ── 3. Parse and return ────────────────────────────────────────
        data = json.loads(raw)
        validation = ValidationReport(
            valid=data.get("valid", False),
            missing_fields=data.get("missing_fields", []),
            applicable_rules=data.get("applicable_rules", 0),
            warnings=data.get("warnings", []),
        )

        logger.info(
            "SchemaValidator: valid=%s, missing_fields=%d, applicable_rules=%d",
            validation.valid,
            len(validation.missing_fields),
            validation.applicable_rules or 0,
        )

        return validation.dict(exclude_none=True)


# ---------------------------------------------------------------------------
# CalculationAgent (Priority 5)
# ---------------------------------------------------------------------------

_CALCULATION_AGENT_SYSTEM_PROMPT = """\
You are a tariff calculation orchestrator for the HarbourMind system.

Your task is to analyze extracted tariff rules and determine which calculator
function should be used for each rule based on its calculation_logic description.

OUTPUT RULES
============
For each rule provided, output a JSON object mapping the charge_type to the
recommended calculator function name. Choose from:
  - "calculate_base_plus_incremental"
  - "calculate_per_unit_per_period"
  - "calculate_bracket_based"
  - "calculate_flat_fee"
  - "calculate_percentage_surcharge"

Return ONLY a valid JSON object:
{
  "charge_type_1": "calculator_function_name",
  "charge_type_2": "calculator_function_name",
  ...
}

MATCHING RULES
==============
- Base + Incremental: Used when charge = base_fee + (vessel_value / unit) * rate
- Per Unit Per Period: Used when charge = base + (vessel_value / unit) * rate * periods
- Bracket-Based: Used when charge depends on a GT or size bracket/band
- Flat Fee: Used when charge is a fixed amount, possibly with multipliers
- Percentage Surcharge: Used when charge = (base_value * percentage) / 100
"""


class CalculationAgent:
    """
    Orchestrates tariff calculation by mapping rules to appropriate calculators.

    For each ExtractedRule, determines which calculator function to use based on
    the rule's calculation_logic description, then executes the calculation with
    the vessel data and extracted parameters.

    Usage
    -----
        agent = CalculationAgent(config=cfg)
        agent.initialize()
        result = agent.execute(vessel_profile, rule_store)
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()
        self._llm: Optional[ChatGoogleGenerativeAI] = None

    def initialize(self) -> None:
        """Set up the Gemini LLM via LangChain."""
        self._llm = _build_llm(self.config)
        logger.info(
            "CalculationAgent initialised with model=%s", self.config.gemini_model
        )

    def _map_rules_to_calculators(self, rule_store: RuleStore) -> dict[str, str]:
        """
        Use LLM to determine which calculator each rule should use.
        Returns dict of {charge_type: calculator_function_name}
        """
        rules_summary = json.dumps(
            [
                {
                    "charge_type": rule.charge_type,
                    "calculation_logic": rule.calculation_logic,
                }
                for rule in rule_store.rules
            ],
            indent=2,
        )

        messages = [
            SystemMessage(content=_CALCULATION_AGENT_SYSTEM_PROMPT),
            HumanMessage(
                content=f"Rules to map:\n{rules_summary}"
            ),
        ]
        response = self._llm.invoke(messages)
        raw = _extract_json(response.content)
        logger.debug("CalculationAgent mapping output:\n%s", raw)
        return json.loads(raw)

    def execute(
        self, vessel_profile: VesselProfile, rule_store: RuleStore
    ) -> CalculationResult:
        """
        Calculate all charges for a vessel against all rules.

        Parameters
        ----------
        vessel_profile : VesselProfile
            Vessel data and port call information.
        rule_store : RuleStore
            Extracted tariff rules.

        Returns
        -------
        CalculationResult
            All calculated charges with traces.
        """
        if self._llm is None:
            raise RuntimeError(
                "CalculationAgent is not initialised. Call initialize() first."
            )

        # ── 1. Map rules to calculators ─────────────────────────────────
        calculator_map = self._map_rules_to_calculators(rule_store)

        # ── 2. Execute calculators ──────────────────────────────────────
        from src.engine.calculators import (
            calculate_base_plus_incremental,
            calculate_per_unit_per_period,
            calculate_bracket_based,
            calculate_flat_fee,
            calculate_percentage_surcharge,
        )

        calculator_functions = {
            "calculate_base_plus_incremental": calculate_base_plus_incremental,
            "calculate_per_unit_per_period": calculate_per_unit_per_period,
            "calculate_bracket_based": calculate_bracket_based,
            "calculate_flat_fee": calculate_flat_fee,
            "calculate_percentage_surcharge": calculate_percentage_surcharge,
        }

        charges: list[CalculatedCharge] = []
        subtotal = 0.0

        for rule in rule_store.rules:
            calc_func_name = calculator_map.get(
                rule.charge_type, "calculate_flat_fee"
            )
            calc_func = calculator_functions.get(calc_func_name)

            if calc_func is None:
                logger.warning(
                    "Unknown calculator '%s' for charge '%s', skipping",
                    calc_func_name,
                    rule.charge_type,
                )
                continue

            try:
                # ── Build parameters for calculator ────────────────────
                params = rule.extracted_parameters.copy()

                # ── Add vessel data as needed ──────────────────────────
                if "vessel_value" not in params:
                    # Try to infer from vessel profile
                    if "gross_tonnage" in rule.required_variables:
                        params["vessel_value"] = vessel_profile.gross_tonnage or 0
                    elif "length_overall" in rule.required_variables:
                        params["vessel_value"] = vessel_profile.length_overall or 0
                    elif "deadweight_tonnage" in rule.required_variables:
                        params["vessel_value"] = vessel_profile.deadweight_tonnage or 0

                if "periods" not in params:
                    if "days_in_port" in rule.required_variables:
                        params["periods"] = vessel_profile.days_in_port or 1

                # ── Call calculator ────────────────────────────────────
                result = calc_func(**params)

                charge = CalculatedCharge(
                    charge_type=rule.charge_type,
                    description=rule.calculation_logic,
                    amount=result["value"],
                    trace=result.get("trace", {}),
                )
                charges.append(charge)
                subtotal += charge.amount

                logger.info(
                    "Calculated %s: %.2f ZAR",
                    rule.charge_type,
                    charge.amount,
                )

            except Exception as exc:
                logger.error(
                    "Failed to calculate %s: %s", rule.charge_type, exc, exc_info=True
                )
                continue

        result = CalculationResult(
            vessel_name=vessel_profile.name,
            port_name=rule_store.port_name,
            charges=charges,
            subtotal=subtotal,
        )

        logger.info(
            "CalculationAgent: %d charges calculated, subtotal=%.2f ZAR",
            len(charges),
            subtotal,
        )

        return result


# ---------------------------------------------------------------------------
# ExceptionHandlerAgent (Priority 5)
# ---------------------------------------------------------------------------

_EXCEPTION_HANDLER_SYSTEM_PROMPT = """\
You are an exception handler for the HarbourMind tariff calculation system.

Your task is to review a set of calculated charges and identify any issues:
- Missing rates (e.g. "rates on application", "determined by Harbour Master")
- Discretionary charges that couldn't be calculated
- Charges with very low confidence scores
- Missing vessel data that prevented calculation

OUTPUT RULES
============
Return ONLY a valid JSON object:
{
  "exceptions": [
    {
      "charge_type": "<charge_type>",
      "issue": "<description of the problem>",
      "severity": "warning" or "error"
    }
  ],
  "warnings": [
    "<general warning about the calculation>"
  ],
  "partial_result": <boolean true if critical charges are missing>
}

SEVERITY LEVELS
===============
- "error": Critical issue that prevents using this calculation (missing rate)
- "warning": Non-critical issue but should be noted (low confidence)
"""


class ExceptionHandlerAgent:
    """
    Identifies issues and exceptions in calculated tariff results.

    Reviews calculated charges for missing rates, discretionary fees, and
    data quality concerns. Separates successfully calculated charges from
    those with problems.

    Usage
    -----
        agent = ExceptionHandlerAgent(config=cfg)
        agent.initialize()
        processed = agent.execute(calculation_result, vessel_profile, rule_store)
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()
        self._llm: Optional[ChatGoogleGenerativeAI] = None

    def initialize(self) -> None:
        """Set up the Gemini LLM via LangChain."""
        self._llm = _build_llm(self.config)
        logger.info(
            "ExceptionHandlerAgent initialised with model=%s",
            self.config.gemini_model,
        )

    def execute(
        self,
        calculation_result: CalculationResult,
        vessel_profile: VesselProfile,
        rule_store: RuleStore,
    ) -> ProcessedResult:
        """
        Analyze calculation result for exceptions and data quality issues.

        Parameters
        ----------
        calculation_result : CalculationResult
            The output from CalculationAgent.
        vessel_profile : VesselProfile
            Vessel data used in calculation.
        rule_store : RuleStore
            The tariff rules that were applied.

        Returns
        -------
        ProcessedResult
            Separated charges and exceptions with warnings.
        """
        if self._llm is None:
            raise RuntimeError(
                "ExceptionHandlerAgent is not initialised. Call initialize() first."
            )

        # ── 1. Summarise calculation result ─────────────────────────────
        charges_summary = json.dumps(
            [
                {
                    "charge_type": c.charge_type,
                    "amount": c.amount,
                    "trace_keys": list(c.trace.keys()) if c.trace else [],
                }
                for c in calculation_result.charges
            ],
            indent=2,
        )

        rules_summary = json.dumps(
            [
                {
                    "charge_type": r.charge_type,
                    "extraction_confidence": r.extraction_confidence,
                    "required_variables": r.required_variables,
                    "conditions": r.conditions,
                }
                for r in rule_store.rules
            ],
            indent=2,
        )

        # ── 2. Call LLM ────────────────────────────────────────────────
        messages = [
            SystemMessage(content=_EXCEPTION_HANDLER_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Calculated charges:\n{charges_summary}\n\n"
                    f"Original rules:\n{rules_summary}"
                )
            ),
        ]
        response = self._llm.invoke(messages)
        raw = _extract_json(response.content)
        logger.debug("ExceptionHandlerAgent output:\n%s", raw)

        data = json.loads(raw)

        # ── 3. Build ProcessedResult ────────────────────────────────────
        exceptions: list[ExceptionCharge] = []
        exception_charges = {exc["charge_type"] for exc in data.get("exceptions", [])}

        for exc_data in data.get("exceptions", []):
            exceptions.append(
                ExceptionCharge(
                    charge_type=exc_data["charge_type"],
                    description=exc_data.get("issue", "Unknown issue"),
                    issue=exc_data.get("issue", "Unknown issue"),
                    severity=exc_data.get("severity", "warning"),
                )
            )

        # Separate calculated charges
        calculated = [
            c
            for c in calculation_result.charges
            if c.charge_type not in exception_charges
        ]
        subtotal = sum(c.amount for c in calculated)

        result = ProcessedResult(
            calculated_charges=calculated,
            exceptions=exceptions,
            warnings=data.get("warnings", []),
            partial_result=data.get("partial_result", len(exceptions) > 0),
            subtotal_calculated=subtotal,
        )

        logger.info(
            "ExceptionHandler: %d charges calculated, %d exceptions, partial=%s",
            len(calculated),
            len(exceptions),
            result.partial_result,
        )

        return result


# ---------------------------------------------------------------------------
# RefinementLoopAgent (Priority 5)
# ---------------------------------------------------------------------------

_REFINEMENT_PROMPT_SYSTEM_PROMPT = """\
You are a tariff clarification agent for the HarbourMind system.

When a user flags a charge as wrong or uncertain, your task is to:
1. Review the original tariff rule for that charge
2. Identify alternative interpretations of how to calculate it
3. Suggest what the value would be under each interpretation
4. Present these options clearly to the user

OUTPUT RULES
============
Return ONLY a valid JSON object:
{
  "charge_type": "<charge_type>",
  "rule_summary": "<summary of what the rule says>",
  "current_value": <current_calculated_value>,
  "issue_description": "<why this might be wrong>",
  "options": [
    {
      "interpretation": "<how to interpret the rule>",
      "expected_value": <calculated_value>,
      "rationale": "<why this interpretation makes sense>"
    }
  ]
}
"""


class RefinementLoopAgent:
    """
    Handles user feedback and recalculation of specific charges.

    When a user flags a charge as wrong, this agent re-examines the tariff
    rule and asks for clarification on how to interpret it. Once the user
    selects an interpretation, it recalculates with the new parameters.

    Usage
    -----
        agent = RefinementLoopAgent(config=cfg)
        agent.initialize()

        # Ask user which charge is wrong
        clarification = agent.execute(calculation_result, user_feedback)
        # User picks an option from clarification['options']
        updated = agent.refine(user_selection, rule_store, vessel_profile)
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()
        self._llm: Optional[ChatGoogleGenerativeAI] = None
        self._conversation_history: list = []

    def initialize(self) -> None:
        """Set up the Gemini LLM via LangChain."""
        self._llm = _build_llm(self.config)
        logger.info(
            "RefinementLoopAgent initialised with model=%s",
            self.config.gemini_model,
        )

    def execute(
        self, calculation_result: CalculationResult, user_feedback: dict
    ) -> ClarificationPrompt:
        """
        Generate clarification options for a flagged charge.

        Parameters
        ----------
        calculation_result : CalculationResult
            The initial calculation result.
        user_feedback : dict
            User input indicating which charge is problematic.
            Expected keys: 'flagged_charge', 'issue' (optional)

        Returns
        -------
        ClarificationPrompt
            Prompt with alternative interpretations and values.
        """
        if self._llm is None:
            raise RuntimeError(
                "RefinementLoopAgent is not initialised. Call initialize() first."
            )

        flagged = user_feedback.get("flagged_charge")
        issue = user_feedback.get("issue", "user_flagged_as_wrong")

        # ── Find the flagged charge ──────────────────────────────────────
        charge = None
        for c in calculation_result.charges:
            if c.charge_type == flagged:
                charge = c
                break

        if charge is None:
            raise ValueError(f"Charge '{flagged}' not found in calculation result")

        # ── For now, return a simple clarification ──────────────────────
        # (Full LLM-based clarification would go here)
        prompt = ClarificationPrompt(
            charge_type=flagged,
            rule_summary=charge.description,
            current_value=charge.amount,
            issue_description=issue,
            options=[
                {
                    "interpretation": "Use current calculation as-is",
                    "expected_value": charge.amount,
                },
                {
                    "interpretation": "Zero out this charge (not applicable)",
                    "expected_value": 0.0,
                },
            ],
        )

        logger.info("RefinementLoopAgent: Generated clarification for %s", flagged)
        return prompt

    def refine(
        self,
        user_selection: dict,
        rule_store: RuleStore,
        vessel_profile: VesselProfile,
    ) -> UpdatedResult:
        """
        Recalculate a charge based on user's selected interpretation.

        Parameters
        ----------
        user_selection : dict
            User's choice from clarification options.
            Expected keys: 'interpretation', 'expected_value'
        rule_store : RuleStore
            The tariff rules.
        vessel_profile : VesselProfile
            Vessel data.

        Returns
        -------
        UpdatedResult
            Recalculated charge with updated value and trace.
        """
        updated = UpdatedResult(
            charge_type=user_selection.get("charge_type", "unknown"),
            original_value=user_selection.get("original_value", 0.0),
            updated_value=user_selection.get("expected_value", 0.0),
            interpretation=user_selection.get("interpretation", "user_selected"),
            confidence=user_selection.get("confidence", 0.8),
        )

        logger.info(
            "RefinementLoopAgent: Refined %s to %.2f ZAR",
            updated.charge_type,
            updated.updated_value,
        )

        return updated
