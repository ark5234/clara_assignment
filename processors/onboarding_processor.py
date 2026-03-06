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
import re
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
        "timezone": null,
        "monday":    {"open": null, "close": null, "closed": false},
        "tuesday":   {"open": null, "close": null, "closed": false},
        "wednesday": {"open": null, "close": null, "closed": false},
        "thursday":  {"open": null, "close": null, "closed": false},
        "friday":    {"open": null, "close": null, "closed": false},
    "saturday":  {"open": null, "close": null, "closed": true},
    "sunday":    {"open": null, "close": null, "closed": true},
        "notes": null
  },
  "emergency_definitions": [
    {
            "type": null,
            "description": null,
            "keywords": [],
      "collect_before_transfer": ["name", "phone", "address"],
            "transfer_target_name": null,
            "transfer_target_phone": null,
            "transfer_target_type": null,
            "transfer_timeout_seconds": null,
            "fallback_on_timeout": null
    }
  ],
  "non_emergency_routing": {
        "business_hours_action": null,
        "business_hours_target_name": null,
        "business_hours_target_phone": null,
        "after_hours_action": null,
    "collect_fields": ["name", "phone", "description"],
        "callback_promise": null
  },
  "integration_constraints": [
    {
            "system": null,
            "rule_description": null,
            "job_types_excluded": [],
            "job_types_auto_create": []
    }
  ],
  "special_rules": ["list of any special rules explicitly stated"],
  "overrides_from_demo": [
    {
            "field": null,
            "demo_assumption": null,
            "confirmed_value": null,
            "reason": null
    }
  ],
  "questions_or_unknowns": [
    {
            "field": null,
            "question": null,
            "priority": "medium"
    }
  ]
}

Use null for any missing scalar. Use [] for any missing list. Do not copy placeholder text into the output."""


_PLACEHOLDER_EXACT = {
    "string",
    "string or null",
    "integer or null",
    "hh:mm or null",
    "high or medium or low",
    "trigger keywords stated",
    "list of any special rules explicitly stated",
}

_PLACEHOLDER_SUBSTRINGS = (
    "concise snake_case type name",
    "how it was described",
    "what to do if transfer fails",
    "list of job types never to auto-create",
    "list of job types that may be auto-created",
    "transfer or voicemail or null",
    "collect_and_callback or voicemail or transfer or null",
    "phone_tree or individual or voicemail or dispatch or null",
    "iana timezone string or null",
    "what was assumed before",
)

_BUSINESS_HOURS_ACTIONS = {"transfer", "voicemail"}
_AFTER_HOURS_ACTIONS = {"collect_and_callback", "voicemail", "transfer"}
_TRANSFER_TARGET_TYPES = {"phone_tree", "individual", "voicemail", "dispatch"}
_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


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

        # Sanitize top-level onboarding structure: convert JSON strings to objects
        raw = self._sanitize_onboarding_raw(raw)

        try:
            v2 = self._merge(existing_config, raw, source)
        except Exception as exc:
            # Persist full traceback for debugging
            import traceback
            import os

            tb = traceback.format_exc()
            base = os.path.join(os.getcwd(), "outputs", "logs")
            os.makedirs(base, exist_ok=True)
            fname = os.path.join(
                base,
                f"onboarding_error_{case_id}_{self._now_iso().replace(':', '')}.log",
            )
            with open(fname, "w", encoding="utf-8") as fh:
                fh.write(tb)
            logger.error(f"[{case_id}] Onboarding processing failed: {exc}")
            logger.error(f"Traceback saved to {fname}")
            raise
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
        existing_emergency_fields = {
            str(u.field)
            for u in v2.questions_or_unknowns
            if str(getattr(u, "field", "")).startswith("emergency_definitions.")
        }

        # Defensive normalization: ensure existing unknown items use string fields
        for u in v2.questions_or_unknowns:
            try:
                u.field = str(u.field) if u.field is not None else "unknown"
            except Exception:
                u.field = "unknown"
            try:
                u.question = self._safe_str(u.question) or ""
            except Exception:
                u.question = ""
            try:
                u.priority = self._safe_str(u.priority) or "medium"
            except Exception:
                u.priority = "medium"

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

        client_updates = self._extract_client_updates(onboarding)
        if client_updates:
            old_client = v2.client.model_dump()
            for key, value in client_updates.items():
                setattr(v2.client, key, value)
            _log(
                "client",
                old_client,
                v2.client.model_dump(),
                reason="Confirmed client profile details in onboarding",
            )

        # ---- Business hours ----------------------------------------
        bh_raw = onboarding.get("business_hours")
        if bh_raw:
            new_bh = self._build_business_hours(bh_raw)
            if new_bh and self._business_hours_has_confirmed_values(new_bh):
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
                if new_bh.timezone:
                    resolved_fields |= {
                        str(item.field)
                        for item in v2.questions_or_unknowns
                        if "timezone" in str(item.field).lower()
                    }

        # ---- Emergency definitions ----------------------------------
        emerg_raw = onboarding.get("emergency_definitions") or []
        if emerg_raw:
            new_emerg = self._build_emergency_defs(emerg_raw)
            if new_emerg:
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
                resolved_fields |= existing_emergency_fields
                resolved_fields |= {
                    str(item.field)
                    for item in v2.questions_or_unknowns
                    if "dispatch" in str(item.field).lower()
                    or "transfer_target" in str(item.field).lower()
                }
                resolved_fields.add("emergency_routing.transfer_timeout_seconds")
                resolved_fields.add("emergency_routing.fallback_behavior")

        # ---- Non-emergency routing ----------------------------------
        ner_raw = onboarding.get("non_emergency_routing")
        if ner_raw:
            new_ner = self._build_non_emergency(ner_raw)
            if self._non_emergency_has_confirmed_values(new_ner):
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
            if self._integration_has_confirmed_values(new_int):
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
        special_raw = onboarding.get("special_rules") or []
        # Normalize special rules to strings
        special = []
        for s in special_raw:
            if isinstance(s, str):
                if s.strip():
                    special.append(s.strip())
            elif isinstance(s, dict):
                # try to extract a readable string
                txt = s.get("rule") or s.get("description") or None
                if txt:
                    special.append(self._safe_str(txt) or str(txt))
        if special:
            old_rules = list(v2.special_rules)
            new_rules = list(dict.fromkeys(old_rules + special))
            if new_rules != old_rules:
                _log("special_rules", old_rules, new_rules)
            v2.special_rules = new_rules

        # ---- Append any NEW unknowns from the onboarding call -------
        existing_fields = {str(u.field) for u in v2.questions_or_unknowns}
        for u in (onboarding.get("questions_or_unknowns") or []):
            # Normalize item: accept dict, JSON string, or simple text
            item = u
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except Exception:
                    # fallback: attempt simple parsing 'field:..., question:..., priority:...'
                    parts = [p.strip() for p in re.split(r"[;,]", item) if p.strip()]
                    parsed = {}
                    for p in parts:
                        if ":" in p:
                            k, v = p.split(":", 1)
                            parsed[k.strip().lower()] = v.strip()
                    item = parsed
            if not isinstance(item, dict):
                item = {}

            field = self._clean_str(item.get("field"))
            question = self._clean_str(item.get("question"))
            priority = self._clean_str(item.get("priority")) or "medium"
            if (not field and not question) or (field == "unknown" and not question):
                continue
            field = field or "unknown"
            if field not in existing_fields:
                v2.questions_or_unknowns.append(
                    UnknownItem(
                        field=field,
                        question=question or "",
                        priority=priority,
                        source_stage="onboarding",
                    )
                )
                existing_fields.add(field)

        # ---- Mark resolved unknowns ---------------------------------
        for item in v2.questions_or_unknowns:
            try:
                key = str(item.field)
            except Exception:
                key = ""
            if key in resolved_fields:
                item.resolved = True

        self._retire_superseded_unknowns(v2, onboarding)
        self._prune_empty_unknowns(v2)

        v2.change_log = change_log
        return v2

    # ------------------------------------------------------------------
    # Field builders (confirmed / precise)
    # ------------------------------------------------------------------
    def _build_business_hours(self, raw: dict) -> BusinessHours:
        def _slot(day: str) -> TimeSlot:
            d = raw.get(day) or {}
            # defensive: if day value is a string like 'closed', handle it
            if isinstance(d, str):
                cleaned = self._clean_str(d)
                if not cleaned:
                    return TimeSlot(open=None, close=None, closed=False)
                if cleaned.lower().startswith("closed"):
                    return TimeSlot(open=None, close=None, closed=True)
                start, end = self._extract_time_range(cleaned)
                if start or end:
                    return TimeSlot(open=start, close=end, closed=False)
                return TimeSlot(open=self._normalize_time_token(cleaned), close=None, closed=False)
            if isinstance(d, dict):
                open_value = self._clean_str(d.get("open"))
                close_value = self._clean_str(d.get("close"))
                if open_value and not close_value:
                    start, end = self._extract_time_range(open_value)
                    if start or end:
                        open_value, close_value = start, end
                if close_value and not open_value:
                    start, end = self._extract_time_range(close_value)
                    if start or end:
                        open_value, close_value = start, end
                return TimeSlot(
                    open=self._normalize_time_token(open_value),
                    close=self._normalize_time_token(close_value),
                    closed=bool(d.get("closed", False)),
                )
            # fallback
            return TimeSlot(open=None, close=None, closed=False)

        return BusinessHours(
            timezone=self._normalize_timezone(raw.get("timezone")),
            monday=_slot("monday"),
            tuesday=_slot("tuesday"),
            wednesday=_slot("wednesday"),
            thursday=_slot("thursday"),
            friday=_slot("friday"),
            saturday=_slot("saturday"),
            sunday=_slot("sunday"),
            notes=self._clean_str(raw.get("notes")),
            is_confirmed=True,
        )

    def _build_emergency_defs(self, raw_list: list[dict]) -> list[EmergencyDefinition]:
        defs = []
        for item in raw_list:
            # item may be a dict or a JSON/string-encoded object
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except Exception:
                    item = {"type": item}

            def _int_or_none(x):
                if x is None:
                    return None
                if isinstance(x, int):
                    return x
                if isinstance(x, str):
                    m = re.search(r"(\d+)", x)
                    if m:
                        try:
                            return int(m.group(1))
                        except Exception:
                            return None
                    return None
                try:
                    return int(x)
                except Exception:
                    return None

            item_type = self._clean_str(item.get("type"))
            description = self._clean_str(item.get("description")) or ""
            target = None
            if isinstance(item, dict) and (
                item.get("transfer_target_phone")
                or item.get("transfer_target_name")
                or item.get("transfer_to")
                or item.get("transfer_target")
            ):
                target_name = self._clean_str(
                    item.get("transfer_target_name")
                    or item.get("transfer_target")
                    or item.get("transfer_to")
                )
                target_phone = self._clean_str(item.get("transfer_target_phone") or item.get("transfer_to"))
                target_type = self._enum_or_none(item.get("transfer_target_type"), _TRANSFER_TARGET_TYPES)
                if target_name or target_phone or target_type:
                    target = RoutingTarget(
                        name=target_name or "On-call team",
                        phone=target_phone,
                        type=target_type or "phone_tree",
                    )

            keywords = self._list_of_clean_str(
                item.get("keywords") or item.get("keywords_list") or []
            )
            collect = self._list_of_clean_str(
                item.get("collect_before_transfer")
                or item.get("collect")
                or item.get("collect_fields")
                or ["name", "phone", "address"]
            )
            timeout = _int_or_none(item.get("transfer_timeout_seconds") or item.get("timeout"))
            fallback = self._clean_str(item.get("fallback_on_timeout") or item.get("fallback"))

            if not item_type and not description and not keywords and not target and timeout is None and not fallback:
                continue

            defs.append(
                EmergencyDefinition(
                    type=item_type or "unknown",
                    description=description,
                    keywords=keywords,
                    collect_before_transfer=collect or ["name", "phone", "address"],
                    transfer_target=target,
                    transfer_timeout_seconds=timeout,
                    fallback_on_timeout=fallback,
                )
            )
        return defs

    def _build_non_emergency(self, raw: dict) -> NonEmergencyRouting:
        target_name = self._clean_str(raw.get("business_hours_target_name"))
        target_phone = self._clean_str(raw.get("business_hours_target_phone"))
        target = None
        if target_name or target_phone:
            target = RoutingTarget(
                name=target_name or "Office",
                phone=target_phone,
                type="individual",
            )
        return NonEmergencyRouting(
            business_hours_action=self._enum_or_none(raw.get("business_hours_action"), _BUSINESS_HOURS_ACTIONS),
            business_hours_target=target,
            after_hours_action=self._enum_or_none(raw.get("after_hours_action"), _AFTER_HOURS_ACTIONS),
            collect_fields=self._list_of_clean_str(raw.get("collect_fields")) or ["name", "phone", "description"],
            callback_promise=self._clean_str(raw.get("callback_promise")),
        )

    def _build_integration(
        self, raw_list: list[dict], existing: IntegrationConfig | None
    ) -> IntegrationConfig:
        constraints = []
        system_name = self._clean_str(existing.system) if existing else None
        for item in raw_list:
            # Item may be a dict or a simple string
            if isinstance(item, str):
                cleaned = self._clean_str(item)
                if not cleaned:
                    continue
                system_name = system_name or cleaned
                constraints.append(
                    IntegrationConstraint(
                        system=cleaned,
                        rule_description="",
                        job_types_excluded=[],
                        job_types_auto_create=[],
                    )
                )
                continue

            system_value = self._clean_str(item.get("system")) or system_name
            rule_description = self._clean_str(item.get("rule_description")) or ""
            job_types_excluded = self._list_of_clean_str(item.get("job_types_excluded"))
            job_types_auto_create = self._list_of_clean_str(item.get("job_types_auto_create"))
            if not system_value and not rule_description and not job_types_excluded and not job_types_auto_create:
                continue
            system_name = system_name or system_value
            constraints.append(
                IntegrationConstraint(
                    system=system_value or system_name or "unknown",
                    rule_description=rule_description,
                    job_types_excluded=job_types_excluded,
                    job_types_auto_create=job_types_auto_create,
                )
            )
        return IntegrationConfig(
            system=system_name,
            enabled=bool(system_name or constraints),
            constraints=constraints,
        )

    def _extract_client_updates(self, onboarding: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        company_name = self._clean_str(onboarding.get("company_name"))
        industry = self._clean_str(onboarding.get("industry"))
        office_address = self._clean_str(onboarding.get("office_address"))
        service_types = self._list_of_clean_str(onboarding.get("service_types"))
        if company_name:
            updates["name"] = company_name
        if industry:
            updates["industry"] = industry
        if office_address:
            updates["office_address"] = office_address
        if service_types:
            updates["service_types"] = service_types
        return updates

    def _business_hours_has_confirmed_values(self, business_hours: BusinessHours | None) -> bool:
        if not business_hours:
            return False
        if business_hours.timezone:
            return True
        for day_name in _DAY_NAMES[:5]:
            slot = getattr(business_hours, day_name)
            if slot.closed or slot.open or slot.close:
                return True
        for day_name in _DAY_NAMES[5:]:
            slot = getattr(business_hours, day_name)
            if slot.open or slot.close:
                return True
        return False

    def _non_emergency_has_confirmed_values(self, routing: NonEmergencyRouting | None) -> bool:
        if not routing:
            return False
        if routing.business_hours_action or routing.after_hours_action or routing.callback_promise:
            return True
        if routing.business_hours_target and (
            routing.business_hours_target.name or routing.business_hours_target.phone
        ):
            return True
        return bool(routing.collect_fields)

    def _integration_has_confirmed_values(self, integration: IntegrationConfig | None) -> bool:
        if not integration:
            return False
        if integration.system:
            return True
        return bool(integration.constraints)

    def _retire_superseded_unknowns(self, config: AgentConfig, onboarding: dict[str, Any]) -> None:
        if onboarding.get("emergency_definitions"):
            current_types = {ed.type for ed in config.emergency_definitions}
            for item in config.questions_or_unknowns:
                field = str(item.field)
                if not field.startswith("emergency_definitions."):
                    continue
                parts = field.split(".")
                if len(parts) < 3:
                    item.resolved = True
                    continue
                if parts[1] not in current_types:
                    item.resolved = True

    def _prune_empty_unknowns(self, config: AgentConfig) -> None:
        cleaned: list[UnknownItem] = []
        for item in config.questions_or_unknowns:
            item.field = self._clean_str(item.field) or "unknown"
            item.question = self._clean_str(item.question) or ""
            item.priority = self._clean_str(item.priority) or "medium"
            if item.field == "unknown" and not item.question:
                continue
            cleaned.append(item)
        config.questions_or_unknowns = cleaned

    def _enum_or_none(self, value: Any, allowed: set[str]) -> str | None:
        cleaned = self._clean_str(value)
        if not cleaned:
            return None
        lowered = cleaned.lower()
        return lowered if lowered in allowed else None

    def _list_of_clean_str(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            cleaned = []
            for item in value:
                if isinstance(item, list):
                    cleaned.extend(self._list_of_clean_str(item))
                    continue
                text = self._clean_str(item)
                if text:
                    cleaned.append(text)
            return cleaned
        if isinstance(value, str):
            parts = [p.strip() for p in re.split(r"[;,]", value) if p.strip()]
            return [text for text in (self._clean_str(part) for part in parts) if text]
        return [text for text in [self._clean_str(value)] if text]

    def _clean_str(self, value: Any) -> str | None:
        cleaned = self._safe_str(value)
        if cleaned is None:
            return None
        return None if self._is_placeholder_text(cleaned) else cleaned

    def _is_placeholder_text(self, value: str | None) -> bool:
        if value is None:
            return False
        normalized = re.sub(r"\s+", " ", value.strip()).lower()
        if normalized in _PLACEHOLDER_EXACT:
            return True
        return any(fragment in normalized for fragment in _PLACEHOLDER_SUBSTRINGS)

    def _normalize_time_token(self, value: Any) -> str | None:
        cleaned = self._clean_str(value)
        if not cleaned:
            return None
        token = cleaned.strip()
        lowered = token.lower()
        if lowered == "noon":
            return "12:00"
        if lowered == "midnight":
            return "00:00"
        match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)?", lowered)
        if not match:
            return token
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3)
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    def _extract_time_range(self, value: str | None) -> tuple[str | None, str | None]:
        cleaned = self._clean_str(value)
        if not cleaned:
            return None, None
        normalized = cleaned.replace("–", "-").replace("—", "-")
        match = re.search(
            (
                r"(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)\s*(?:to|-)\s*"
                r"(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?|noon|midnight)"
            ),
            normalized,
        )
        if not match:
            return None, None
        return (
            self._normalize_time_token(match.group(1)),
            self._normalize_time_token(match.group(2)),
        )

    def _normalize_timezone(self, value: Any) -> str | None:
        cleaned = self._clean_str(value)
        if not cleaned:
            return None
        lowered = cleaned.lower()
        mapping = {
            "central time": "America/Chicago",
            "mountain time": "America/Denver",
            "pacific time": "America/Los_Angeles",
            "eastern time": "America/New_York",
        }
        return mapping.get(lowered, cleaned)

    def _normalize_form_alternates(self, raw: dict) -> dict:
        """Normalize common alternate form keys/shapes into the canonical onboarding schema.

        - 'emergency_types' -> 'emergency_definitions' with renamed fields
        - compact 'integration' dict -> 'integration_constraints' list
        - business_hours day string 'closed' -> canonical dict
        """
        out = deepcopy(raw)

        # emergency_types -> emergency_definitions
        if isinstance(out, dict) and "emergency_types" in out and "emergency_definitions" not in out:
            defs = []
            for it in out.pop("emergency_types") or []:
                if isinstance(it, dict):
                    item = dict(it)
                    # rename known keys
                    if "collect" in item:
                        item["collect_before_transfer"] = item.pop("collect")
                    if "transfer_to" in item:
                        item["transfer_target_phone"] = item.pop("transfer_to")
                    if "timeout" in item:
                        item["transfer_timeout_seconds"] = item.pop("timeout")
                    if "fallback" in item:
                        item["fallback_on_timeout"] = item.pop("fallback")
                    defs.append(item)
                else:
                    defs.append(it)
            out["emergency_definitions"] = defs

        # integration -> integration_constraints
        if isinstance(out, dict) and "integration" in out and "integration_constraints" not in out:
            integ = out.pop("integration")
            if isinstance(integ, dict):
                rule = integ.get("note") or integ.get("rule") or ""
                system = integ.get("system") or "unknown"
                auto_create = integ.get("auto_create_jobs")
                job_auto = []
                if isinstance(auto_create, bool) and auto_create:
                    job_auto = []
                out["integration_constraints"] = [
                    {
                        "system": system,
                        "rule_description": rule,
                        "job_types_excluded": [],
                        "job_types_auto_create": job_auto,
                    }
                ]

        # business_hours day string -> canonical dict
        bh = out.get("business_hours")
        if isinstance(bh, dict):
            for day, val in list(bh.items()):
                if isinstance(val, str) and val.strip().lower().startswith("closed"):
                    bh[day] = {"open": None, "close": None, "closed": True}
                # if day is dict but missing keys, ensure keys exist
                elif isinstance(val, dict):
                    if "open" not in val and "close" not in val:
                        # keep as-is; further sanitization will handle types
                        pass
            out["business_hours"] = bh

        return out

    def _sanitize_onboarding_raw(self, raw: dict) -> Any:
        """Attempt to normalize any string-encoded JSON or simple text entries.

        This handles cases where LLM returns strings instead of structured objects.
        """
        # First, normalize common form alternates into canonical keys
        try:
            if isinstance(raw, dict):
                raw = self._normalize_form_alternates(raw)
        except Exception:
            # non-fatal; proceed with original raw
            pass

        def _recurse(value):
            # Convert string-encoded JSON to objects where reasonable
            if isinstance(value, str):
                s = value.strip()
                if s.startswith("{") or s.startswith("["):
                    try:
                        parsed = json.loads(s)
                        return _recurse(parsed)
                    except Exception:
                        pass
                if ";" in s:
                    parts = [p.strip() for p in re.split(r"[;,]", s) if p.strip()]
                    return [_recurse(p) for p in parts]
                return self._clean_str(s)
            if isinstance(value, dict):
                return {k: _recurse(v) for k, v in value.items()}
            if isinstance(value, list):
                cleaned = []
                for item in value:
                    normalized = _recurse(item)
                    if normalized is None:
                        continue
                    if isinstance(normalized, list):
                        cleaned.extend([entry for entry in normalized if entry is not None])
                        continue
                    cleaned.append(normalized)
                return cleaned
            return value

        return _recurse(raw)
