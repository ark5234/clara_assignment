"""
Form processor — handles structured JSON onboarding forms.

The JSON form schema is the same as the onboarding call extraction schema,
so this processor simply validates the form data and delegates to the same
merge logic used by OnboardingProcessor.
"""
from __future__ import annotations

import json

from schemas.agent_config import AgentConfig
from processors.onboarding_processor import OnboardingProcessor
from processors.base_processor import BaseProcessor
from utils.logger import get_logger

logger = get_logger(__name__)


class FormProcessor(BaseProcessor):
    """
    Processes a structured JSON onboarding form and merges it into v1 config.

    The form must be a JSON object whose schema matches the output of the
    onboarding extraction prompt (see OnboardingProcessor._ONBOARDING_SYSTEM_PROMPT).
    This allows forms to be used interchangeably with call transcripts.
    """

    def process(
        self,
        form_data: dict | str,
        existing_config: AgentConfig,
    ) -> AgentConfig:
        case_id = existing_config.config_id

        # Accept either a dict or a raw JSON string
        if isinstance(form_data, str):
            try:
                form_dict = json.loads(form_data)
            except json.JSONDecodeError as exc:
                raise ValueError(f"[{case_id}] Form data is not valid JSON: {exc}") from exc
        else:
            form_dict = form_data

        logger.info(
            f"[{case_id}] Processing onboarding form "
            f"({len(form_dict)} top-level keys)"
        )

        # Delegate merge logic to OnboardingProcessor (reuse identical merge path)
        merger = OnboardingProcessor(llm=self.llm)
        # Sanitize form content (may contain JSON-encoded strings)
        try:
            form_dict = merger._sanitize_onboarding_raw(form_dict)
        except Exception:
            # Non-critical: proceed with original form_dict
            pass

        # Defensive: if sanitizer returned a non-dict (some forms are strings/lists),
        # persist the raw payload for debugging and raise a clear error.
        if not isinstance(form_dict, dict):
            import os
            import time
            import json as _json

            base = os.path.join(os.getcwd(), "outputs", "logs")
            os.makedirs(base, exist_ok=True)
            fname = os.path.join(base, f"bad_form_{case_id}_{int(time.time())}.json")
            try:
                to_dump = form_data if isinstance(form_data, dict) else form_dict
                with open(fname, "w", encoding="utf-8") as fh:
                    fh.write(_json.dumps(to_dump, indent=2, ensure_ascii=False))
            except Exception:
                # best-effort; ignore any file write errors
                fname = "<failed to write bad form>"

            raise ValueError(
                f"[{case_id}] Sanitized form is not an object; saved raw form to {fname}"
            )

        # Override _merge to use form_dict directly instead of calling the LLM
        v2 = merger._merge(existing_config, form_dict, source="onboarding_form")

        logger.info(
            f"[{case_id}] v2 built from form — "
            f"{len(v2.change_log)} change(s), "
            f"{len(v2.open_unknowns())} remaining unknown(s)"
        )
        return v2
