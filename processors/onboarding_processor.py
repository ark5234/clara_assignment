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

        # Sanitize top-level onboarding structure: convert JSON strings to objects
        raw = self._sanitize_onboarding_raw(raw)

        try:
            v2 = self._merge(existing_config, raw, source)
        except Exception as exc:
            # Persist full traceback for debugging
            import traceback, os
            tb = traceback.format_exc()
            base = os.path.join(os.getcwd(), "outputs", "logs")
            os.makedirs(base, exist_ok=True)
            fname = os.path.join(base, f"onboarding_error_{case_id}_{self._now_iso().replace(':','')}.log")
            with open(fname, 'w', encoding='utf-8') as fh:
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

            field = self._safe_str(item.get("field")) or "unknown"
            if field not in existing_fields:
                v2.questions_or_unknowns.append(
                    UnknownItem(
                        field=field,
                        question=self._safe_str(item.get("question")) or "",
                        priority=self._safe_str(item.get("priority")) or "medium",
                        source_stage="onboarding",
                    )
                )

        # ---- Mark resolved unknowns ---------------------------------
        for item in v2.questions_or_unknowns:
            try:
                key = str(item.field)
            except Exception:
                key = ""
            if key in resolved_fields:
                item.resolved = True

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
                if d.strip().lower().startswith("closed"):
                    return TimeSlot(open=None, close=None, closed=True)
                # fallback: attempt to parse a single open-close pair like '08:00-18:00'
                m = re.match(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", d)
                if m:
                    return TimeSlot(open=self._safe_str(m.group(1)), close=self._safe_str(m.group(2)), closed=False)
                return TimeSlot(open=self._safe_str(d), close=None, closed=False)
            if isinstance(d, dict):
                return TimeSlot(
                    open=self._safe_str(d.get("open")),
                    close=self._safe_str(d.get("close")),
                    closed=bool(d.get("closed", False)),
                )
            # fallback
            return TimeSlot(open=None, close=None, closed=False)

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
            # item may be a dict or a JSON/string-encoded object
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except Exception:
                    item = {"type": item}
            # helper to coerce list-like fields to list[str]
            def _list_of_str(x):
                if x is None:
                    return []
                if isinstance(x, list):
                    out = []
                    for v in x:
                        if isinstance(v, str):
                            out.append(v)
                        else:
                            out.append(self._safe_str(v) or "")
                    return [o for o in out if o]
                if isinstance(x, str):
                    # split on commas or semicolons
                    parts = [p.strip() for p in re.split(r"[;,]", x) if p.strip()]
                    return parts
                return []

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

            target = None
            if isinstance(item, dict) and (item.get("transfer_target_phone") or item.get("transfer_target_name") or item.get("transfer_to") or item.get("transfer_target")):
                target = RoutingTarget(
                    name=self._safe_str(item.get("transfer_target_name") or item.get("transfer_target") or item.get("transfer_to")) or "On-call team",
                    phone=self._safe_str(item.get("transfer_target_phone") or item.get("transfer_to")),
                    type=self._safe_str(item.get("transfer_target_type")) or "phone_tree",
                )

            keywords = _list_of_str(item.get("keywords") or item.get("keywords_list") or [])
            collect = _list_of_str(item.get("collect_before_transfer") or item.get("collect") or item.get("collect_fields") or ["name", "phone", "address"])
            timeout = _int_or_none(item.get("transfer_timeout_seconds") or item.get("timeout"))
            fallback = self._safe_str(item.get("fallback_on_timeout") or item.get("fallback"))

            defs.append(
                EmergencyDefinition(
                    type=self._safe_str(item.get("type")) or "unknown",
                    description=self._safe_str(item.get("description")) or "",
                    keywords=keywords,
                    collect_before_transfer=collect or ["name", "phone", "address"],
                    transfer_target=target,
                    transfer_timeout_seconds=timeout,
                    fallback_on_timeout=fallback,
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
            # Item may be a dict or a simple string
            if isinstance(item, str):
                # treat string as system name
                system_name = system_name or item
                constraints.append(
                    IntegrationConstraint(
                        system=item or system_name or "unknown",
                        rule_description="",
                        job_types_excluded=[],
                        job_types_auto_create=[],
                    )
                )
                continue

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
                if ";" in s or ("," in s and "\n" not in s):
                    parts = [p.strip() for p in re.split(r"[;,]", s) if p.strip()]
                    return [_recurse(p) for p in parts]
                return s
            if isinstance(value, dict):
                return {k: _recurse(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_recurse(v) for v in value]
            return value

        return _recurse(raw)
