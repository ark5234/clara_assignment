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
        # Override _merge to use form_dict directly instead of calling the LLM
        v2 = merger._merge(existing_config, form_dict, source="onboarding_form")

        logger.info(
            f"[{case_id}] v2 built from form — "
            f"{len(v2.change_log)} change(s), "
            f"{len(v2.open_unknowns())} remaining unknown(s)"
        )
        return v2
