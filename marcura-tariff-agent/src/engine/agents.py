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
)
from src.utils.config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_VESSEL_PARSER_SYSTEM_PROMPT = """\
You are a maritime vessel data parser for the HarbourMind tariff calculation system.

Your task: Extract vessel and port-call information from ANY input format
(natural language, JSON string, shipping certificate, structured data, etc.)

Return a clean JSON object with all vessel information you can extract.
Use your knowledge of ships to extract what's important for port tariff calculations.

OUTPUT RULES
============
1. Return ONLY a valid JSON object — no markdown, no code fences, no commentary.
2. Include these fields if available:
   name, imo_number, call_sign, type, gross_tonnage, net_tonnage,
   deadweight_tonnage, length_overall, beam, draft, port,
   cargo_type, cargo_tonnage, days_in_port, days_alongside, number_of_operations
3. For any field not in input, use null
4. Preserve numeric values as given (don't round or convert units)
5. All tonnages in metric tonnes, dimensions in metres

EXAMPLE:
Input: Shipping certificate for SUDESTADA calling Durban, 51,300 GT, 3.39 days alongside, 2 operations

Output:
{
  "name": "SUDESTADA",
  "type": "Bulk Carrier",
  "gross_tonnage": 51300,
  "length_overall": 190.5,
  "beam": 32.2,
  "draft": 10.5,
  "port": "Durban",
  "days_alongside": 3.39,
  "number_of_operations": 2,
  ...other fields as null or extracted...
}
"""

_RULE_EXTRACTION_SYSTEM_PROMPT = """\
You are a maritime tariff rule extractor for the HarbourMind port cost calculation system.

Your task: Read a port tariff document and intelligently extract all charges that ports typically levy on vessels.
Use your knowledge of maritime commerce to identify what's important.

Do NOT follow a checklist. Use your intelligence to understand the document and extract what matters.

OUTPUT RULES
============
1. Return ONLY a valid JSON object — no markdown, no code fences, no commentary.
2. The JSON object must have this structure:
   {
     "port_name": "<port name from document — MANDATORY, never null, never empty>",
     "currency": "<ISO-4217 code, e.g. 'ZAR', 'USD', 'SGD', or null>",
     "tax_rate": <number 0.0–1.0, e.g. 0.15 for 15%, or null>,
     "tax_label": "<tax name as written in the tariff, e.g. 'VAT', 'GST', or null>",
     "rules": [ ...list of charge objects... ],
     "extraction_timestamp": null
   }

   `port_name` is MANDATORY. The tariff document always identifies its port —
   check headers, footers, cover page, section titles, schedule captions,
   or rate-table headings. Examples: "Port of Durban Tariff Schedule" → "Durban",
   "Singapore MPA Tariff" → "Singapore", "Rotterdam Port Dues 2025" → "Rotterdam".
   Use the canonical English name. NEVER return null or empty for this field.
3. Each charge object must have:
   {
     "charge_type": "<lowercase snake_case charge name>",
     "calculation_logic": "<how this charge is calculated, from the document>",
     "extracted_parameters": {
       "<param_name>": <value>,
       ... (whatever parameters this charge needs)
     },
     "extraction_confidence": <0.0–1.0>,
     "required_variables": ["<vessel_field>", ...],
     "conditions": "<when this charge applies, or null>"
   }

CURRENCY & TAX RATE EXTRACTION  *(MANDATORY — do not skip)*
============================================================
Before extracting any rules, do ONE pass over the full document looking for
the currency and the local applicable-tax rate. These three top-level fields
are NEVER omitted from your output — even if you have to set them to null.

WHAT TO LOOK FOR
----------------
Currency:
  * ISO-4217 codes printed verbatim: "ZAR", "USD", "EUR", "GBP", "SGD",
    "INR", "JPY", "AUD", "CAD"
  * Written-out names: "South African Rand", "United States Dollar",
    "Singapore Dollar", "Euro", "Pound Sterling", "Indian Rupee"
  * Symbols: "R" prefix (almost always ZAR), "$" (usually USD unless
    qualified — "S$" = SGD, "A$" = AUD, "HK$" = HKD), "€" (EUR),
    "£" (GBP), "¥" (JPY)
  * Phrases like "Tariffs in South African Rand" → currency = "ZAR".

Tax:
  * Phrases: "VAT", "GST", "Sales Tax", "Service Tax", "Octroi"
  * Rate forms: "subject to VAT at 15%", "exclusive of 7% GST",
    "all rates exclude VAT (15%)"
  * If the document says rates are exclusive of tax, you still report the
    tax rate the document declares — it's the rate that would be applied.

WORKED EXAMPLE
--------------
If the tariff contains the line "Tariffs subject to VAT at 15%: Tariffs in
South African Rand" (or similar phrasing in headers/footers), your output's
top-level fields MUST be:

  "currency": "ZAR",
  "tax_rate": 0.15,
  "tax_label": "VAT",

If the document is genuinely silent on currency or tax, set the silent
field(s) to null. Do not guess.

Place these three fields on the TOP-LEVEL JSON object (alongside
`port_name`), NOT on individual rules.

INTELLIGENCE GUIDELINES (Not Rules - Use Your Judgment)
========================================================
- charge_type: lowercase snake_case (whatever makes sense for this charge)
- extracted_parameters: Extract whatever values are relevant for this charge
  * Some charges might need brackets, some need base+rate, some flat fees
  * Structure parameters in whatever way makes calculation sense
  * Example: if there's a table with ranges, extract as brackets
  * Example: if there's a base + per-unit formula, extract both
- required_variables: What vessel info is needed for this charge?
  * Look at the document to understand what vessel dimensions matter
  * Use VesselProfile field names: gross_tonnage, net_tonnage, length_overall,
    beam, draft, cargo_tonnage, days_in_port, days_alongside,
    number_of_operations, cargo_type, type, port
- extraction_confidence: Your confidence in this extraction
  * 0.95+ if explicitly stated in document
  * Lower if you had to interpret or infer

STABILITY (important — same document should produce the same rules every run)
=============================================================================
- One rule per CHARGE, not per row of a rate table. If a charge has a long
  rate table (e.g. cargo dues with 50 commodity rows that share the same
  per-tonne formula), extract it as ONE rule and put the rate table inside
  `extracted_parameters.rate_table`. Do NOT create 50 sibling rules.
- DO extract separate rules when the document defines NAMED VARIANTS with
  DIFFERENT FORMULAS or DIFFERENT CALCULATION SCHEDULES — e.g. "Light Dues —
  Foreign-going (per 100 GT)" vs "Light Dues — SA-registered (annual flat)" vs
  "Light Dues — Coasters (monthly)". Those are three rules with different
  formulas, not one.
- charge_type names MUST be derived from the document's section headings or
  charge labels — never invented or paraphrased.
- For charges that have NO variants in the document (a single port_dues rate,
  a single tug assistance rate), use the simple unqualified name
  (`port_dues`, `tug_assistance`).
- Process the document top-to-bottom in document order so the rules array is
  stable across runs.

SHORT TEST: would the per-rule calculator need to PICK BETWEEN variants?
- If yes (e.g. light dues registration class, cargo dues route): extract them
  as separate rules so the calculator can mark them needs_clarification.
- If no (e.g. 50 commodity rows sharing a per-tonne rate, all selected by
  cargo type only): one rule with the table inside.

THAT'S IT
=========
Read the document. Extract charges. Return JSON.
Use your intelligence—don't follow a script.
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
      - List-typed `content` from multimodal LLM responses
    """
    # Some LangChain LLM responses wrap the content as a list of parts
    # (each part is a string or {"type": "text", "text": "..."} dict).
    # Coerce to a plain string before any regex operations.
    if isinstance(text, list):
        parts = []
        for p in text:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                parts.append(p.get("text") or p.get("content") or "")
        text = "".join(parts)

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
        google_api_key=config.gemini_api_key,
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
        rule_store = agent.execute(tariff_data)
        # rule_store.port_name comes from the document, never from caller
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
    ) -> RuleStore:
        """
        Extract all tariff rules from a document and return a RuleStore.

        The port name is extracted from the document itself by the LLM —
        the application never assumes or hardcodes one.

        Parameters
        ----------
        tariff_data : str | dict
            Full text of the tariff document, or a pre-parsed dict structure.

        Returns
        -------
        RuleStore
            Container with all discovered ExtractedRule objects, plus the
            port name, currency, tax rate, and tax label extracted from
            the document.
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
            HumanMessage(content=f"Tariff document:\n{tariff_str}"),
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

        # Port name is REQUIRED — the LLM must extract it from the document.
        # We never fall back, never assume, never hardcode.
        port_name_extracted = (data.get("port_name") or "").strip()
        if not port_name_extracted:
            raise ValueError(
                "RuleExtractionAgent: LLM did not extract a port name from "
                "the tariff document. The document must identify the port "
                "(check headers, footers, schedule titles, or rate-table captions)."
            )

        rule_store = RuleStore(
            port_name=port_name_extracted,
            rules=rules,
            currency=data.get("currency"),
            tax_rate=data.get("tax_rate"),
            tax_label=data.get("tax_label"),
        )

        logger.info(
            "RuleExtractionAgent extracted %d rules for port '%s' (currency=%s, tax_rate=%s, tax_label=%s).",
            len(rules),
            rule_store.port_name,
            rule_store.currency,
            rule_store.tax_rate,
            rule_store.tax_label,
        )
        return rule_store

