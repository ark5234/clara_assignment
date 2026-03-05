"""
Onboarding processor — merges confirmed operational data into an existing v1 config
to produce a v2 AgentConfig.

Key contract:
- Never overwrite fields that aren't addressed in the onboarding data
- Always generate a ChangeLogEntry for every field that changes
- Flag and log conflicts between demo assumptions and onboarding facts
- Resolve and mark unknowns that the onboarding data answers
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from schemas.agent_config import (
    AgentConfig,
    BusinessHours,
    ChangeLogEntry,
    EmergencyDefinition,
    IntegrationConfig,
    IntegrationConstraint,
    NonEmergencyRouting,
    RoutingTarget,
    TimeSlot,
    UnknownItem,
)
from processors.base_processor import BaseProcessor
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# LLM system prompt
# ---------------------------------------------------------------------------
_ONBOARDING_SYSTEM_PROMPT = """You are a configuration extraction specialist for Clara, an AI voice agent platform.

Your task is to analyze an ONBOARDING CALL transcript and extract CONFIRMED operational configuration in JSON format.

## STRICT EXTRACTION RULES
1. Extract ONLY information EXPLICITLY confirmed in the transcript.
2. Be PRECISE — capture exact times (HH:MM 24h), phone numbers, durations, and rule text.
3. NEVER hallucinate or assume missing details.
4. If information was not stated, set it to null.
5. Note any information that CONFLICTS with typical demo assumptions.

## THIS IS AN ONBOARDING CALL — expect precise operational details:
- Business hours should be exact with timezone
- Emergency definitions should be clearly defined
- Transfer timeouts should be specified
- Integration rules should be explicit
- Phone numbers should be captured exactly as stated

## JSON RESPONSE FORMAT
Return ONLY a single valid JSON object:
{
  "business_hours": {
    "timezone": "IANA timezone string or null — e.g. America/Chicago",
    "monday":    {"open": "HH:MM or null", "close": "HH:MM or null", "closed": false},
    "tuesday":   {"open": "HH:MM or null", "close": "HH:MM or null", "closed": false},
    "wednesday": {"open": "HH:MM or null", "close": "HH:MM or null", "closed": false},
    "thursday":  {"open": "HH:MM or null", "close": "HH:MM or null", "closed": false},
    "friday":    {"open": "HH:MM or null", "close": "HH:MM or null", "closed": false},
    "saturday":  {"open": null, "close": null, "closed": true},
    "sunday":    {"open": null, "close": null, "closed": true},
    "notes": "string or null"
  },
  "emergency_definitions": [
    {
      "type": "string — concise snake_case type name",
      "description": "string — how it was described",
      "keywords": ["trigger keywords stated"],
      "collect_before_transfer": ["name", "phone", "address"],
      "transfer_target_name": "string or null",
      "transfer_target_phone": "string or null",
      "transfer_target_type": "phone_tree or individual or voicemail or dispatch or null",
      "transfer_timeout_seconds": "integer or null",
      "fallback_on_timeout": "string or null — what to do if transfer fails"
    }
  ],
  "non_emergency_routing": {
    "business_hours_action": "transfer or voicemail or null",
    "business_hours_target_name": "string or null",
    "business_hours_target_phone": "string or null",
    "after_hours_action": "collect_and_callback or voicemail or transfer or null",
    "collect_fields": ["name", "phone", "description"],
    "callback_promise": "string or null"
  },
  "integration_constraints": [
    {
      "system": "string — e.g. ServiceTrade",
      "rule_description": "string — exact rule as stated",
      "job_types_excluded": ["list of job types NEVER to auto-create"],
      "job_types_auto_create": ["list of job types that MAY be auto-created"]
    }
  ],
  "special_rules": ["list of any special rules explicitly stated"],
  "overrides_from_demo": [
    {
      "field": "string — what changed",
      "demo_assumption": "string or null — what was assumed before",
      "confirmed_value": "string — what was confirmed",
      "reason": "string or null"
    }
  ],
  "questions_or_unknowns": [
    {
      "field": "string",
      "question": "string",
      "priority": "high or medium or low"
    }
  ]
}"""


class OnboardingProcessor(BaseProcessor):
    """
    Takes an existing v1 AgentConfig and onboarding data (transcript or parsed form)
    and produces a v2 AgentConfig with full change log.
    """

    def process(
        self,
        input_text: str,
        existing_config: AgentConfig,
        source: str = "onboarding_call",
    ) -> AgentConfig:
        case_id = existing_config.config_id
        logger.info(f"[{case_id}] Processing onboarding ({source}, {len(input_text)} chars)")

        raw = self.llm.extract_json(
            system_prompt=_ONBOARDING_SYSTEM_PROMPT,
            user_content=f"ONBOARDING TRANSCRIPT:\n\n{input_text}",
        )
        logger.debug(f"[{case_id}] Raw extraction: {json.dumps(raw, indent=2)[:500]}…")

        v2 = self._merge(existing_config, raw, source)
        logger.info(
            f"[{case_id}] v2 built — "
            f"{len(v2.change_log)} change(s), "
            f"{len(v2.open_unknowns())} remaining unknown(s)"
        )
        return v2

    # ------------------------------------------------------------------
    # Merge logic
    # ------------------------------------------------------------------
    def _merge(
        self, v1: AgentConfig, onboarding: dict[str, Any], source: str
    ) -> AgentConfig:
        now = self._now_iso()
        # Deep copy so v1 is never mutated
        v2 = v1.model_copy(deep=True)
        v2.version = "v2"
        v2.updated_at = now
        v2.source = source

        change_log = list(v2.change_log)

        def _log(field_path: str, old: Any, new: Any, reason: str | None = None, conflict: bool = False) -> None:
            change_log.append(
                ChangeLogEntry(
                    timestamp=now,
                    version_from="v1",
                    version_to="v2",
                    field_path=field_path,
                    old_value=old,
                    new_value=new,
                    source=source,
                    reason=reason or "Confirmed in onboarding",
                    conflict_noted=conflict,
                )
            )

        resolved_fields: set[str] = set()

        # ---- Business hours ----------------------------------------
        bh_raw = onboarding.get("business_hours")
        if bh_raw:
            new_bh = self._build_business_hours(bh_raw)
            old_bh_repr = v2.business_hours.model_dump() if v2.business_hours else None
            conflict: bool = bool(
                v2.business_hours is not None
                and v2.business_hours.timezone
                and new_bh.timezone
                and v2.business_hours.timezone.lower() != new_bh.timezone.lower()
            )
            _log(
                "business_hours",
                old_bh_repr,
                new_bh.model_dump(),
                reason="Confirmed exact hours in onboarding" + (" — TIMEZONE CONFLICT" if conflict else ""),
                conflict=conflict,
            )
            v2.business_hours = new_bh
            resolved_fields |= {"business_hours.timezone", "business_hours.schedule"}

        # ---- Emergency definitions ----------------------------------
        emerg_raw = onboarding.get("emergency_definitions") or []
        if emerg_raw:
            new_emerg = self._build_emergency_defs(emerg_raw)
            _log(
                "emergency_definitions",
                [e.model_dump() for e in v2.emergency_definitions],
                [e.model_dump() for e in new_emerg],
                reason="Emergency types and routing confirmed in onboarding",
            )
            v2.emergency_definitions = new_emerg
            # Mark transfer target and timeout as resolved for each type
            for ed in new_emerg:
                resolved_fields.add(f"emergency_definitions.{ed.type}.transfer_target")
            resolved_fields.add("emergency_routing.transfer_timeout_seconds")
            resolved_fields.add("emergency_routing.fallback_behavior")

        # ---- Non-emergency routing ----------------------------------
        ner_raw = onboarding.get("non_emergency_routing")
        if ner_raw:
            new_ner = self._build_non_emergency(ner_raw)
            _log(
                "non_emergency_routing",
                v2.non_emergency_routing.model_dump() if v2.non_emergency_routing else None,
                new_ner.model_dump(),
            )
            v2.non_emergency_routing = new_ner

        # ---- Integration constraints --------------------------------
        int_raw = onboarding.get("integration_constraints") or []
        if int_raw:
            new_int = self._build_integration(int_raw, v2.integration)
            system = new_int.system or ""
            _log(
                "integration",
                v2.integration.model_dump() if v2.integration else None,
                new_int.model_dump(),
                reason="Integration constraints confirmed in onboarding",
            )
            v2.integration = new_int
            resolved_fields.add(f"integration.{system}.constraints")

        # ---- Special rules -----------------------------------------
        special = onboarding.get("special_rules") or []
        if special:
            old_rules = list(v2.special_rules)
            new_rules = list(set(old_rules) | set(special))
            if new_rules != old_rules:
                _log("special_rules", old_rules, new_rules)
            v2.special_rules = new_rules

        # ---- Append any NEW unknowns from the onboarding call -------
        existing_fields = {u.field for u in v2.questions_or_unknowns}
        for u in onboarding.get("questions_or_unknowns") or []:
            field = u.get("field", "unknown")
            if field not in existing_fields:
                v2.questions_or_unknowns.append(
                    UnknownItem(
                        field=field,
                        question=u.get("question", ""),
                        priority=u.get("priority", "medium"),
                        source_stage="onboarding",
                    )
                )

        # ---- Mark resolved unknowns ---------------------------------
        for item in v2.questions_or_unknowns:
            if item.field in resolved_fields:
                item.resolved = True

        v2.change_log = change_log
        return v2

    # ------------------------------------------------------------------
    # Field builders (confirmed / precise)
    # ------------------------------------------------------------------
    def _build_business_hours(self, raw: dict) -> BusinessHours:
        def _slot(day: str) -> TimeSlot:
            d = raw.get(day) or {}
            return TimeSlot(
                open=self._safe_str(d.get("open")),
                close=self._safe_str(d.get("close")),
                closed=bool(d.get("closed", False)),
            )

        return BusinessHours(
            timezone=self._safe_str(raw.get("timezone")),
            monday=_slot("monday"),
            tuesday=_slot("tuesday"),
            wednesday=_slot("wednesday"),
            thursday=_slot("thursday"),
            friday=_slot("friday"),
            saturday=_slot("saturday"),
            sunday=_slot("sunday"),
            notes=self._safe_str(raw.get("notes")),
            is_confirmed=True,
        )

    def _build_emergency_defs(self, raw_list: list[dict]) -> list[EmergencyDefinition]:
        defs = []
        for item in raw_list:
            target = None
            if item.get("transfer_target_phone") or item.get("transfer_target_name"):
                target = RoutingTarget(
                    name=self._safe_str(item.get("transfer_target_name")) or "On-call team",
                    phone=self._safe_str(item.get("transfer_target_phone")),
                    type=self._safe_str(item.get("transfer_target_type")) or "phone_tree",
                )
            defs.append(
                EmergencyDefinition(
                    type=self._safe_str(item.get("type")) or "unknown",
                    description=self._safe_str(item.get("description")) or "",
                    keywords=item.get("keywords") or [],
                    collect_before_transfer=item.get("collect_before_transfer") or ["name", "phone", "address"],
                    transfer_target=target,
                    transfer_timeout_seconds=item.get("transfer_timeout_seconds"),
                    fallback_on_timeout=self._safe_str(item.get("fallback_on_timeout")),
                )
            )
        return defs

    def _build_non_emergency(self, raw: dict) -> NonEmergencyRouting:
        target = None
        if raw.get("business_hours_target_name") or raw.get("business_hours_target_phone"):
            target = RoutingTarget(
                name=self._safe_str(raw.get("business_hours_target_name")) or "Office",
                phone=self._safe_str(raw.get("business_hours_target_phone")),
                type="individual",
            )
        return NonEmergencyRouting(
            business_hours_action=self._safe_str(raw.get("business_hours_action")),
            business_hours_target=target,
            after_hours_action=self._safe_str(raw.get("after_hours_action")),
            collect_fields=raw.get("collect_fields") or ["name", "phone", "description"],
            callback_promise=self._safe_str(raw.get("callback_promise")),
        )

    def _build_integration(
        self, raw_list: list[dict], existing: IntegrationConfig | None
    ) -> IntegrationConfig:
        constraints = []
        system_name = existing.system if existing else None
        for item in raw_list:
            system_name = system_name or self._safe_str(item.get("system"))
            constraints.append(
                IntegrationConstraint(
                    system=self._safe_str(item.get("system")) or system_name or "unknown",
                    rule_description=self._safe_str(item.get("rule_description")) or "",
                    job_types_excluded=item.get("job_types_excluded") or [],
                    job_types_auto_create=item.get("job_types_auto_create") or [],
                )
            )
        return IntegrationConfig(
            system=system_name,
            enabled=True,
            constraints=constraints,
        )
