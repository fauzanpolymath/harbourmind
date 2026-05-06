"""
test_model_availability.py
--------------------------
Quick test to verify gemini-2.5-flash model loads and can make API calls.

Run from marcura-tariff-agent/ directory:
    python test_model_availability.py
"""

import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.config import Config
from langchain_google_genai import ChatGoogleGenerativeAI


def test_config_loading():
    """Test that config loads correctly with gemini-2.5-flash."""
    print()
    print("=" * 70)
    print("TEST 1: Config Loading")
    print("=" * 70)
    print()

    try:
        cfg = Config()
        print(f"[OK] Config loaded successfully")
        print(f"  API Key: {cfg.google_api_key[:6]}...{cfg.google_api_key[-4:]}")
        print(f"  Model: {cfg.gemini_model}")
        print(f"  App Env: {cfg.app_env}")
        print()

        assert cfg.gemini_model == "gemini-2.5-flash", f"Expected gemini-2.5-flash, got {cfg.gemini_model}"
        print(f"[OK] Model is correctly set to: {cfg.gemini_model}")
        print()

        return cfg
    except Exception as e:
        print(f"[ERROR] Failed to load config: {e}")
        sys.exit(1)


def test_model_initialization(cfg):
    """Test that the LLM model can be initialized."""
    print("=" * 70)
    print("TEST 2: Model Initialization")
    print("=" * 70)
    print()

    try:
        llm = ChatGoogleGenerativeAI(
            model=cfg.gemini_model,
            google_api_key=cfg.google_api_key,
            temperature=0.0,
        )
        print(f"[OK] ChatGoogleGenerativeAI initialized successfully")
        print(f"  Model: {cfg.gemini_model}")
        print(f"  Temperature: 0.0")
        print()

        return llm
    except Exception as e:
        print(f"[ERROR] Failed to initialize model: {e}")
        sys.exit(1)


def test_simple_api_call(llm):
    """Test that a simple API call works."""
    print("=" * 70)
    print("TEST 3: Simple API Call")
    print("=" * 70)
    print()

    try:
        print("Sending test prompt to gemini-2.5-flash...")
        response = llm.invoke("What is 2 + 2? Reply with just the number.")

        print(f"[OK] API call successful")
        print(f"  Response: {response.content}")
        print()

        return response.content
    except Exception as e:
        print(f"[ERROR] API call failed: {e}")
        print()
        print("This could indicate:")
        print("  - Invalid or expired GOOGLE_API_KEY")
        print("  - Model gemini-2.5-flash not available in your account")
        print("  - API quota exhausted")
        print()
        sys.exit(1)


def test_json_extraction(llm):
    """Test JSON extraction (used in actual agents)."""
    print("=" * 70)
    print("TEST 4: JSON Extraction (Agent Pattern)")
    print("=" * 70)
    print()

    try:
        import json

        test_prompt = """
        Extract the following information as JSON:

        Vessel: SUDESTADA
        Port: Durban
        Tonnage: 51,300 GT

        Respond ONLY with valid JSON, no markdown, no extra text:
        {"vessel_name": "...", "port": "...", "gross_tonnage": ...}
        """

        print("Sending JSON extraction test...")
        response = llm.invoke(test_prompt)

        # Try to parse as JSON
        json_str = response.content.strip()
        if json_str.startswith("```"):
            # Strip markdown code block if present
            json_str = json_str.split("```")[1]
            if json_str.startswith("json"):
                json_str = json_str[4:]
            json_str = json_str.strip()

        parsed = json.loads(json_str)
        print(f"[OK] JSON extraction successful")
        print(f"  Parsed data: {parsed}")
        print()

        return parsed
    except json.JSONDecodeError as e:
        print(f"[ERROR] Failed to parse JSON response: {e}")
        print(f"  Raw response: {response.content}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] JSON extraction test failed: {e}")
        sys.exit(1)


def main():
    """Run all model availability tests."""
    print()
    print("=" * 70)
    print("GEMINI-2.5-FLASH MODEL AVAILABILITY TEST")
    print("=" * 70)

    # Test 1: Load config
    cfg = test_config_loading()

    # Test 2: Initialize model
    llm = test_model_initialization(cfg)

    # Test 3: Simple API call
    response = test_simple_api_call(llm)

    # Test 4: JSON extraction pattern
    parsed = test_json_extraction(llm)

    # Summary
    print("=" * 70)
    print("[SUCCESS] ALL TESTS PASSED")
    print("=" * 70)
    print()
    print("[OK] Config loads correctly with gemini-2.5-flash")
    print("[OK] Model initializes successfully")
    print("[OK] API calls work correctly")
    print("[OK] JSON extraction pattern works")
    print()
    print("Ready to run Priority 3a/3b/5 tests with live LLM calls.")
    print()


if __name__ == "__main__":
    main()
