"""
Demo call processor — converts a demo call transcript into a v1 AgentConfig.

Rules enforced here:
- Extract ONLY explicitly stated information (no hallucination)
- Every missing critical field is added to questions_or_unknowns
- Business hours from a demo are treated as hints, not confirmed values
- No phone numbers or transfer targets are assumed
"""
from __future__ import annotations

import json
from typing import Any
from schemas.agent_config import (
    AgentConfig,
    BusinessHours,
    ChangeLogEntry,
    ClientInfo,
    EmergencyDefinition,
    IntegrationConfig,
    NonEmergencyRouting,
    TimeSlot,
    UnknownItem,
)
from processors.base_processor import BaseProcessor
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# LLM system prompt
# ---------------------------------------------------------------------------
_DEMO_SYSTEM_PROMPT = """
You are a configuration extraction specialist for Clara, an AI voice agent platform
serving service trade businesses (fire protection, HVAC, electrical, alarm, sprinkler).

Your task is to analyze a DEMO CALL transcript and extract configuration data in JSON format.

## STRICT EXTRACTION RULES
1. Extract ONLY information EXPLICITLY stated in the transcript.
2. NEVER infer, assume, or create details that are not mentioned.
3. Set fields to null if not explicitly mentioned.
4. For every critical missing field, add an entry to questions_or_unknowns.
5. This is a DEMO/EXPLORATORY call — incomplete information is EXPECTED and NORMAL.
6. Business hours mentioned in a demo are vague hints — always set is_precise to false.

## CRITICAL FIELDS — always flag as HIGH PRIORITY unknowns if missing:
- Exact business hours with timezone
- Emergency type definitions (what qualifies as emergency)
- After-hours routing targets with phone numbers
- Transfer timeout duration
- Integration system constraints (if a system was mentioned)

## JSON RESPONSE FORMAT
Return ONLY a single valid JSON object with this exact structure:
{
    "client_name": "string or null",
    "industry": "string or null — one of: fire_protection, hvac, electrical, "
    "alarm, sprinkler, facility_maintenance, other",
    "service_types": ["array of service types explicitly mentioned"],
    "pain_points": ["array of pain points explicitly stated by the caller"],
    "emergency_types": [
        {
            "type": "string — concise type name, e.g. sprinkler_leak",
            "description": "string — how the caller described it",
            "keywords": ["trigger keywords mentioned by caller"],
            "routing_hint": "string or null — any routing destination mentioned for this type"
        }
    ],
    "business_hours_hints": {
        "days_mentioned": "string or null — e.g. Monday through Friday, weekdays",
        "hours_mentioned": "string or null — e.g. 8 to 5, standard business hours",
        "timezone_mentioned": "string or null",
        "is_precise": false
    },
    "routing_mentions": [
        {
            "scenario": "string — what triggers this routing",
            "destination": "string or null — where calls should go",
            "method": "string or null — how they are routed"
        }
    ],
    "integration_system": "string or null — e.g. ServiceTrade, Salesforce",
    "after_hours_description": "string or null — how after-hours is described",
    "non_emergency_description": "string or null — how non-emergency calls are described",
    "contact_info": {
        "company_name": "string or null",
        "contact_name": "string or null",
        "phone": "string or null",
        "office_address": "string or null — full mailing address if mentioned",
        "location": "string or null — city/state if full address not given"
    },
    "questions_or_unknowns": [
        {
            "field": "string — dot-notation field identifier, e.g. business_hours.timezone",
            "question": "string — specific actionable question to resolve this gap",
            "priority": "high or medium or low",
            "context": "string or null — why this field matters"
        }
    ],
    "confidence_notes": ["array of notes about confidence in specific extracted values"]
}
"""


class DemoProcessor(BaseProcessor):
    """Processes a demo call transcript and returns a v1 AgentConfig."""

    def process(self, transcript: str, case_id: str) -> AgentConfig:
        logger.info(f"[{case_id}] Processing demo transcript ({len(transcript)} chars)")

        input_hash = self._hash_input(transcript)
        now = self._now_iso()

        raw = self.llm.extract_json(
            system_prompt=_DEMO_SYSTEM_PROMPT,
            user_content=f"DEMO CALL TRANSCRIPT:\n\n{transcript}",
        )
        logger.debug(f"[{case_id}] Raw extraction: {json.dumps(raw, indent=2)[:500]}…")

        config = self._build_v1_config(raw, case_id, input_hash, now)
        logger.info(
            f"[{case_id}] v1 built — "
            f"{len(config.emergency_definitions)} emergency type(s), "
            f"{len(config.questions_or_unknowns)} unknown(s)"
        )
        return config

    # ------------------------------------------------------------------
    # Config builder
    # ------------------------------------------------------------------
    def _build_v1_config(
        self, raw: dict[str, Any], case_id: str, input_hash: str, now: str
    ) -> AgentConfig:
        client = ClientInfo(
            name=self._safe_str(raw.get("client_name")),
            industry=self._safe_str(raw.get("industry")),
            office_address=self._safe_str(
                (raw.get("contact_info") or {}).get("office_address")
            ),
            service_types=raw.get("service_types") or [],
            pain_points=raw.get("pain_points") or [],
        )

        business_hours = self._build_hours_from_hints(raw.get("business_hours_hints"))
        emergency_defs = self._build_emergency_defs(raw.get("emergency_types") or [])
        non_emergency = self._build_non_emergency(raw)
        integration = self._build_integration(raw)
        unknowns = self._build_unknowns(
            raw,
            client,
            business_hours,
            emergency_defs,
            integration,
        )

        # Initial change log entry marks v1 creation
        change_log = [
            ChangeLogEntry(
                timestamp=now,
                version_from="",
                version_to="v1",
                field_path="*",
                old_value=None,
                new_value="v1 created from demo transcript",
                source="demo",
                reason="Initial configuration derived from demo call",
            )
        ]

        return AgentConfig(
            config_id=case_id,
            version="v1",
            created_at=now,
            updated_at=now,
            source="demo",
            client=client,
            business_hours=business_hours,
            emergency_definitions=emergency_defs,
            non_emergency_routing=non_emergency,
            integration=integration,
            questions_or_unknowns=unknowns,
            change_log=change_log,
            input_hash=input_hash,
            raw_extraction=raw,
            processing_notes=raw.get("confidence_notes") or [],
        )

    # ------------------------------------------------------------------
    # Field builders
    # ------------------------------------------------------------------
    def _build_hours_from_hints(self, hints: dict | None) -> BusinessHours | None:
        if not hints:
            return None
        days_mentioned = hints.get("days_mentioned")
        hours_mentioned = hints.get("hours_mentioned")
        if not days_mentioned and not hours_mentioned:
            return None

        bh = BusinessHours(
            timezone=self._safe_str(hints.get("timezone_mentioned")),
            is_confirmed=False,
            notes=f"Demo hints — days: '{days_mentioned}', hours: '{hours_mentioned}'. Not yet confirmed.",
        )
        # Mark weekends closed if "weekdays" or "Monday-Friday" mentioned
        if days_mentioned and any(
            kw in days_mentioned.lower()
            for kw in ("weekday", "monday", "mon-fri", "mon through fri")
        ):
            bh.saturday = TimeSlot(closed=True)
            bh.sunday = TimeSlot(closed=True)
        return bh

    def _build_emergency_defs(self, raw_list: list[dict]) -> list[EmergencyDefinition]:
        defs = []
        for item in raw_list:
            em = EmergencyDefinition(
                type=self._safe_str(item.get("type")) or "unknown",
                description=self._safe_str(item.get("description")) or "",
                keywords=item.get("keywords") or [],
                collect_before_transfer=["name", "phone", "address"],
                transfer_target=None,  # phone numbers not available at demo stage
                transfer_timeout_seconds=None,
                fallback_on_timeout=None,
            )
            defs.append(em)
        return defs

    def _build_non_emergency(self, raw: dict) -> NonEmergencyRouting | None:
        desc = self._safe_str(raw.get("non_emergency_description"))
        routing_mentions = raw.get("routing_mentions") or []
        if not desc and not routing_mentions:
            return None
        return NonEmergencyRouting(
            after_hours_action="collect_and_callback",
            collect_fields=["name", "phone", "description"],
            callback_promise="during business hours",
        )

    def _build_integration(self, raw: dict) -> IntegrationConfig | None:
        system = self._safe_str(raw.get("integration_system"))
        if not system:
            return None
        return IntegrationConfig(
            system=system,
            enabled=True,
            constraints=[],  # constraints confirmed during onboarding
        )

    def _build_unknowns(
        self,
        raw: dict,
        client: ClientInfo,
        business_hours: BusinessHours | None,
        emergency_defs: list,
        integration: IntegrationConfig | None,
    ) -> list[UnknownItem]:
        # Start with what the LLM itself flagged
        unknowns: list[UnknownItem] = []
        for u in raw.get("questions_or_unknowns") or []:
            item = u
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except Exception:
                    # fallback: parse simple 'k: v' pairs
                    parts = [p.strip() for p in item.split(";") if p.strip()]
                    parsed = {}
                    for p in parts:
                        if ":" in p:
                            k, v = p.split(":", 1)
                            parsed[k.strip().lower()] = v.strip()
                    item = parsed
            if not isinstance(item, dict):
                item = {}
            unknowns.append(
                UnknownItem(
                    field=self._safe_str(item.get("field")) or "unknown",
                    question=self._safe_str(item.get("question")) or "",
                    context=self._safe_str(item.get("context")),
                    priority=self._safe_str(item.get("priority")) or "medium",
                    source_stage="demo",
                )
            )

        existing_fields = {u.field for u in unknowns}

        # Enforce mandatory critical unknowns if not already flagged
        def _add_if_missing(field: str, question: str, context: str | None = None) -> None:
            if field not in existing_fields:
                unknowns.append(
                    UnknownItem(
                        field=field,
                        question=question,
                        context=context,
                        priority="high",
                        source_stage="demo",
                    )
                )

        if not business_hours or not business_hours.timezone:
            _add_if_missing(
                "business_hours.timezone",
                "What timezone is the business operating in? (e.g. America/Chicago)",
                "Required to determine when after-hours routing activates.",
            )

        if not business_hours or not business_hours.is_fully_specified():
            _add_if_missing(
                "business_hours.schedule",
                "What are the exact business hours for each day of the week?",
                "Needed to correctly separate business-hours vs after-hours call flows.",
            )

        if not emergency_defs:
            _add_if_missing(
                "emergency_definitions",
                "What types of situations count as emergencies requiring immediate transfer?",
                "Clara needs clear emergency criteria to route calls correctly.",
            )
        else:
            for ed in emergency_defs:
                if not ed.transfer_target:
                    _add_if_missing(
                        f"emergency_definitions.{ed.type}.transfer_target",
                        f"What phone number or destination should emergency '{ed.type}' calls be transferred to?",
                        "Transfer target is required for emergency routing.",
                    )

        _add_if_missing(
            "emergency_routing.transfer_timeout_seconds",
            "How long (in seconds) should Clara wait before declaring a transfer failed?",
            "Determines when fallback logic triggers.",
        )

        _add_if_missing(
            "emergency_routing.fallback_behavior",
            "If an emergency transfer fails, what should Clara do? (e.g. notify dispatch, leave voicemail)",
        )

        if integration:
            _add_if_missing(
                f"integration.{integration.system}.constraints",
                (
                    "Are there any rules about which job types should or should not "
                    f"be automatically created in {integration.system}?"
                ),
                f"{integration.system} was mentioned but no specific constraints were stated.",
            )

        return unknowns
