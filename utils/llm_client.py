"""
Multi-provider LLM client for Clara AI pipeline.

All providers are accessed through the OpenAI-compatible API interface,
which means the same `openai` Python package works for all of them \u2014
just with different base_url and api_key values.

Zero-cost providers:
  - gemini  : Google Gemini 1.5 Flash  (15 RPM free, no credit card)
  - groq    : Groq Llama 3.3 70B       (6K TPM free, extremely fast)
  - ollama  : Ollama local models       (completely free, runs offline)

Paid (for reference):
  - openai  : GPT-4o

Design goals:
- temperature=0 by default (determinism / idempotency)
- json_object response format for all extraction calls
- Graceful fallback when json_object mode is unsupported
- Clear error messages with actionable hints per provider
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict

import config
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
_PROVIDERS: dict[str, dict] = {
    "openai": {
        "base_url": None,
        "api_key_fn": lambda: config.OPENAI_API_KEY,
        "default_model": config.OPENAI_MODEL,
        "supports_json_mode": True,
        "setup_hint": "Set OPENAI_API_KEY in .env (paid service).",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_fn": lambda: config.GEMINI_API_KEY,
        "default_model": config.GEMINI_MODEL,
        "supports_json_mode": True,
        "setup_hint": (
            "Set GEMINI_API_KEY in .env. "
            "Get a free key (no credit card) at: https://aistudio.google.com/apikey"
        ),
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_fn": lambda: config.GROQ_API_KEY,
        "default_model": config.GROQ_MODEL,
        "supports_json_mode": True,
        "setup_hint": (
            "Set GROQ_API_KEY in .env. "
            "Get a free key at: https://console.groq.com"
        ),
    },
    "ollama": {
        "base_url": f"{config.OLLAMA_BASE_URL}/v1",
        "api_key_fn": lambda: "ollama",  # Ollama doesn't require a real key
        "default_model": config.OLLAMA_MODEL,
        "supports_json_mode": False,  # model-dependent; disabled for safety
        "setup_hint": (
            "Install Ollama at https://ollama.ai then run: "
            f"ollama pull {config.OLLAMA_MODEL}"
        ),
    },
}


class LLMClient:
    """
    Universal LLM client that speaks to any provider through the
    OpenAI-compatible API interface.

    Usage:
        client = LLMClient()                       # uses LLM_PROVIDER from config
        client = LLMClient(provider="groq")        # override provider
        client = LLMClient(model="llama3.2")       # override model
    """

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        self.provider = (provider or config.LLM_PROVIDER).lower()
        if self.provider not in _PROVIDERS:
            raise ValueError(
                f"Unknown LLM provider: '{self.provider}'. "
                f"Valid options: {list(_PROVIDERS.keys())}"
            )
        spec = _PROVIDERS[self.provider]
        self.model = model or spec["default_model"]
        self.temperature = temperature if temperature is not None else config.OPENAI_TEMPERATURE
        self._supports_json_mode: bool = spec["supports_json_mode"]
        self._client = None
        logger.debug(f"LLMClient initialised: provider={self.provider}, model={self.model}")

    # ------------------------------------------------------------------
    # Lazy-init so import works even without API key (useful for tests)
    # ------------------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            spec = _PROVIDERS[self.provider]
            api_key: str = spec["api_key_fn"]()
            if not api_key:
                raise EnvironmentError(
                    f"API key for provider '{self.provider}' is not set. "
                    f"{spec['setup_hint']}"
                )
            from openai import OpenAI  # deferred import
            kwargs: dict = {"api_key": api_key}
            if spec["base_url"]:
                kwargs["base_url"] = spec["base_url"]
            self._client = OpenAI(**kwargs)
        return self._client

    # ------------------------------------------------------------------
    # Core extraction call
    # ------------------------------------------------------------------
    def extract_json(
        self,
        system_prompt: str,
        user_content: str,
    ) -> Dict[str, Any]:
        """
        Send system_prompt + user_content to the LLM and return parsed JSON.

        If the provider supports json_object response format, it is used
        directly.  Otherwise, the prompt is supplemented with an instruction
        to respond in JSON, and the output is parsed with a lenient extractor.
        """
        logger.debug(
            f"LLM call \u2192 provider={self.provider}, model={self.model}, "
            f"temp={self.temperature}, json_mode={self._supports_json_mode}"
        )

        if self._supports_json_mode:
            return self._call_with_json_mode(system_prompt, user_content)
        else:
            return self._call_with_json_instruction(system_prompt, user_content)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _call_with_json_mode(
        self, system_prompt: str, user_content: str
    ) -> Dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        raw_content: str = response.choices[0].message.content or "{}"
        try:
            self._save_raw_response(raw_content)
        except Exception:
            logger.debug("Failed to persist raw LLM response for debugging")
        return self._parse_json(raw_content)

    def _call_with_json_instruction(
        self, system_prompt: str, user_content: str
    ) -> Dict[str, Any]:
        """For providers that don\u2019t support json_object mode natively."""
        augmented_system = (
            system_prompt
            + "\n\n"
            + "IMPORTANT: Your response MUST be a single valid JSON object. "
            "Do not include any text before or after the JSON. "
            "Do not use markdown code fences."
        )
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": augmented_system},
                {"role": "user", "content": user_content},
            ],
        )
        raw_content: str = response.choices[0].message.content or "{}"
        try:
            self._save_raw_response(raw_content)
        except Exception:
            logger.debug("Failed to persist raw LLM response for debugging")
        return self._parse_json(raw_content)

    @staticmethod
    def _save_raw_response(raw: str) -> None:
        """Save raw LLM responses to `outputs/logs/` for offline inspection."""
        try:
            import os
            from datetime import datetime
            import hashlib

            base = os.path.join(os.getcwd(), "outputs", "logs")
            os.makedirs(base, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            short = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
            filename = f"llm_raw_{ts}_{short}.txt"
            path = os.path.join(base, filename)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(raw)
            logger.debug(f"Saved raw LLM response to {path}")
        except Exception as exc:
            logger.debug(f"Unable to save raw LLM response: {exc}")

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, Any]:
        """Parse JSON from LLM output, stripping markdown fences if present."""
        def _extract_first_brace_block(text: str) -> str | None:
            # Find the first balanced { ... } block in text
            start = None
            depth = 0
            for i, ch in enumerate(text):
                if ch == '{':
                    if start is None:
                        start = i
                    depth += 1
                elif ch == '}':
                    if depth > 0:
                        depth -= 1
                        if depth == 0 and start is not None:
                            return text[start : i + 1]
            return None

        def _try_load(s: str) -> Dict[str, Any]:
            # Try to load JSON, with a last-ditch fix for trailing commas
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                # Remove trailing commas before } or ]
                fixed = re.sub(r",\s*(\}|\])", r"\1", s)
                return json.loads(fixed)

        def _normalize(obj: Any) -> Any:
            # Recursively normalize values:
            # - strings like 'null'/'none' -> None
            # - strings like 'integer or null' -> None
            # - purely numeric strings -> int
            if isinstance(obj, dict):
                return {k: _normalize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_normalize(v) for v in obj]
            if isinstance(obj, str):
                s = obj.strip()
                lower = s.lower()
                if lower in ("null", "none"):
                    return None
                if "integer or null" in lower or "int or null" in lower:
                    return None
                # common human-written placeholders
                if lower in ("integer", "int", "number"):
                    return None
                # numeric strings => int
                if re.fullmatch(r"-?\d+", s):
                    try:
                        return int(s)
                    except Exception:
                        return s
                return s
            return obj

        raw = raw or ""
        # 1) Strip common fenced code blocks and leading text
        text = raw.strip()
        # Remove a leading prose line like 'Here is the JSON response:'
        # but only if the text does NOT start with a JSON object/array
        if not text.lstrip().startswith(("{", "[")):
            text = re.sub(r"^[^\n]*?\n+", "", text, count=1)
        # Remove triple-backtick wrappers if present
        if text.startswith("```") and text.endswith("```"):
            inner = text[3:-3].strip()
            # If inner starts with json tag, strip it
            inner = re.sub(r"^json\s*", "", inner, flags=re.I)
            text = inner

        # 2) Try to parse the whole text first
        try:
            parsed = _try_load(text)
            return _normalize(parsed)
        except Exception:
            pass

        # 3) Extract the first {...} block and try to parse it
        block = _extract_first_brace_block(text)
        if block:
            try:
                parsed = _try_load(block)
                return _normalize(parsed)
            except Exception:
                logger.error(f"Failed parsing extracted JSON block from LLM output: {block[:400]}")

        # 4) Give up with a clear error including a short excerpt
        logger.error(f"LLM returned non-JSON content: {raw[:800]}")
        raise ValueError("LLM response was not valid JSON or contained no JSON object")
