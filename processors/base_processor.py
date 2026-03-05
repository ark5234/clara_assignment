"""
Base processor — shared utilities for all Clara pipeline processors.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from utils.llm_client import LLMClient
from utils.logger import get_logger

logger = get_logger(__name__)


class BaseProcessor:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _hash_input(text: str) -> str:
        """SHA-256 of input text — used for idempotency checks."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _safe_str(value: Any) -> str | None:
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        return str(value).strip()
