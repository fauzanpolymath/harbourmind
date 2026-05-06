"""
config.py  (src/core/config.py)
---------------------------------
Re-exports the canonical Config class from src.utils.config so that both
import paths work:

    from src.utils.config import Config   # primary (used in tests)
    from src.core.config import Config    # alternate (used in core modules)
"""

from src.utils.config import Config  # noqa: F401  (re-export)

__all__ = ["Config"]
