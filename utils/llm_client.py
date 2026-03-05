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
        return self._parse_json(raw_content)

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, Any]:
        """Parse JSON from LLM output, stripping markdown fences if present."""
        # Strip ```json ... ``` wrappers that some models add
        stripped = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        stripped = re.sub(r"\s*```$", "", stripped)
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            logger.error(f"LLM returned non-JSON content: {raw[:400]}")
            raise ValueError(f"LLM response was not valid JSON: {exc}") from exc
