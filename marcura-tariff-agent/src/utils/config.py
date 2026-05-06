"""
config.py  (src/utils/config.py)
---------------------------------
Central configuration loader for HarbourMind.

Reads settings from environment variables, falling back to a .hmenv.txt file
located at the project root (marcura-tariff-agent/).  Import the `Config`
class anywhere in the project:

    from src.utils.config import Config
    cfg = Config()
"""

from __future__ import annotations

import os
import logging
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Locate and load the .hmenv.txt file from the project root
# (src/utils/config.py  →  parents[2] == marcura-tariff-agent/)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _PROJECT_ROOT / ".hmenv.txt"

# Load .hmenv.txt if it exists, otherwise fall back to .env
# NOTE: .hmenv.txt is gitignored and should contain local development secrets
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)
else:
    # Try .env next
    _ENV_FILE = _PROJECT_ROOT / ".env"
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE)
    # If neither exists, environment variables must be set directly
    # (useful for Cloud Run, Docker, CI/CD with secrets)

logger = logging.getLogger(__name__)


class Config:
    """
    Application configuration resolved from environment variables.

    Instantiate once and pass the object to agents / services that need it:

        cfg = Config()
        agent = VesselQueryParserAgent(config=cfg)
    """

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------
    gemini_api_key: str
    gemini_model: str

    # ------------------------------------------------------------------
    # Document parsing
    # ------------------------------------------------------------------
    llamaparse_api_key: str

    # ------------------------------------------------------------------
    # GCP / Cloud
    # ------------------------------------------------------------------
    gcp_project_id: str
    gcs_bucket_name: str
    cors_origins: str

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    app_env: str
    log_level: str
    data_dir: Path

    def __init__(self) -> None:
        # ── LLM ──────────────────────────────────────────────────────────
        self.gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
        # Use gemini-2.5-flash as default (latest fast model)
        self.gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

        # ── Document parsing ─────────────────────────────────────────────
        self.llamaparse_api_key = os.environ.get("LLAMAPARSE_API_KEY", "")

        # ── GCP / Cloud ──────────────────────────────────────────────────
        self.gcp_project_id = os.environ.get("GCP_PROJECT_ID", "")
        self.gcs_bucket_name = os.environ.get("GCS_BUCKET_NAME", "harbourmind-logs")
        self.cors_origins = os.environ.get("CORS_ORIGINS", "*")

        # ── Application ──────────────────────────────────────────────────
        self.app_env = os.environ.get("APP_ENV", "development")
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        self.data_dir = Path(os.environ.get("DATA_DIR", str(_PROJECT_ROOT / "data")))

        # ── Validation ───────────────────────────────────────────────────
        if not self.gemini_api_key:
            logger.warning(
                "GEMINI_API_KEY is not set. Add it to your .hmenv.txt file or set it as an environment variable."
            )

        if not self.llamaparse_api_key:
            logger.warning(
                "LLAMAPARSE_API_KEY is not set — document parsing will be unavailable."
            )

    def __repr__(self) -> str:
        masked_key = (
            f"{self.gemini_api_key[:6]}...{self.gemini_api_key[-4:]}"
            if len(self.gemini_api_key) > 10
            else "***"
        )
        return (
            f"Config(model={self.gemini_model!r}, "
            f"env={self.app_env!r}, "
            f"project={self.gcp_project_id!r}, "
            f"gemini_api_key={masked_key!r})"
        )
