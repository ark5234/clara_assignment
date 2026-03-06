"""
AccountMemoGenerator — converts an AgentConfig into the canonical AccountMemo JSON.

The AccountMemo is the primary structured output required by the assignment spec.
It is human-readable, machine-parseable, and version-stamped.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from schemas.agent_config import AgentConfig, BusinessHours
from schemas.account_memo import (
    AccountMemo,
    BusinessHoursMemo,
    CallTransferRules,
    EmergencyRoutingRule,
    NonEmergencyRoutingRule,
)
from utils.logger import get_logger

logger = get_logger(__name__)

_DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class AccountMemoGenerator:
    def generate(self, config: AgentConfig) -> AccountMemo:
        logger.debug(f"[{config.config_id}] Generating AccountMemo {config.version}")
        return AccountMemo(
            account_id=config.config_id,
            version=config.version,
            generated_at=datetime.now(timezone.utc).isoformat(),
            company_name=config.client.name,
            industry=config.client.industry,
            office_address=config.client.office_address,
            services_supported=config.client.service_types,
            business_hours=self._build_hours(config.business_hours),
            emergency_definition=self._build_emergency_defs(config),
            emergency_routing_rules=self._build_emergency_rules(config),
            non_emergency_routing_rules=self._build_non_emergency(config),
            call_transfer_rules=self._build_transfer_rules(config),
            integration_constraints=self._build_integration_constraints(config),
            office_hours_flow_summary=self._build_office_hours_summary(config),
            after_hours_flow_summary=self._build_after_hours_summary(config),
            questions_or_unknowns=[
                {"field": u.field, "question": u.question, "priority": u.priority}
                for u in config.open_unknowns()
            ],
            notes=self._build_notes(config),
        )

    # ------------------------------------------------------------------
    def _build_hours(self, bh: BusinessHours | None) -> BusinessHoursMemo | None:
        if not bh:
            return None
        open_days = []
        schedule_pairs = []
        for attr, label in zip(_DAY_NAMES, _DAY_LABELS):
            slot = getattr(bh, attr)
            if not slot.closed:
                open_days.append(label)
                if slot.open or slot.close:
                    schedule_pairs.append((slot.open, slot.close))
        start, end = None, None
        if schedule_pairs:
            start, end = Counter(schedule_pairs).most_common(1)[0][0]
        return BusinessHoursMemo(
            days=open_days,
            start=start,
            end=end,
            timezone=bh.timezone,
            notes=bh.notes,
            is_confirmed=bh.is_confirmed,
        )

    def _build_emergency_defs(self, config: AgentConfig) -> list[str]:
        return [
            f"{ed.type}: {ed.description}"
            for ed in config.emergency_definitions
        ]

    def _build_emergency_rules(self, config: AgentConfig) -> list[EmergencyRoutingRule]:
        rules = []
        for ed in config.emergency_definitions:
            rules.append(EmergencyRoutingRule(
                emergency_type=ed.type,
                description=ed.description,
                trigger_keywords=ed.keywords,
                collect_before_transfer=ed.collect_before_transfer,
                transfer_to_name=ed.transfer_target.name if ed.transfer_target else None,
                transfer_to_phone=ed.transfer_target.phone if ed.transfer_target else None,
                transfer_to_type=ed.transfer_target.type if ed.transfer_target else None,
                timeout_seconds=ed.transfer_timeout_seconds,
                fallback=ed.fallback_on_timeout,
            ))
        return rules

    def _build_non_emergency(self, config: AgentConfig) -> NonEmergencyRoutingRule | None:
        ner = config.non_emergency_routing
        if not ner:
            return None
        target = ner.business_hours_target
        return NonEmergencyRoutingRule(
            business_hours_action=ner.business_hours_action,
            business_hours_target=target.name if target else None,
            business_hours_phone=target.phone if target else None,
            after_hours_action=ner.after_hours_action,
            after_hours_collect=ner.collect_fields,
            callback_promise=ner.callback_promise,
        )

    def _build_transfer_rules(self, config: AgentConfig) -> CallTransferRules | None:
        timeouts = [e.transfer_timeout_seconds for e in config.emergency_definitions if e.transfer_timeout_seconds]
        fallbacks = [e.fallback_on_timeout for e in config.emergency_definitions if e.fallback_on_timeout]
        if not timeouts and not fallbacks:
            return None
        return CallTransferRules(
            default_timeout_seconds=timeouts[0] if timeouts else None,
            collect_before_transfer=["name", "phone", "address"],
            on_timeout_message=fallbacks[0] if fallbacks else None,
        )

    def _build_integration_constraints(self, config: AgentConfig) -> list[str]:
        if not config.integration or not config.integration.constraints:
            return []
        lines = []
        for c in config.integration.constraints:
            lines.append(c.rule_description)
            for jt in c.job_types_excluded:
                lines.append(f"  — Never auto-create {c.system} job for: {jt}")
            for jt in c.job_types_auto_create:
                lines.append(f"  — May auto-create {c.system} job for: {jt}")
        return lines

    def _build_office_hours_summary(self, config: AgentConfig) -> str:
        bh = config.business_hours
        if not bh or not bh.is_confirmed:
            return (
                "Business hours flow: greet → identify purpose → collect name & phone "
                "→ transfer to main office → fallback if transfer fails → wrap up"
            )

        parts = [
            f"During business hours ({bh.timezone or 'local time'}):",
            "greet → identify purpose → collect name & phone → transfer to main line",
            "if transfer fails: log info and assure callback → ask if anything else → close",
        ]
        return " ".join(parts)

    def _build_after_hours_summary(self, config: AgentConfig) -> str:
        parts = ["After-hours flow: greet (closed) → identify purpose → confirm emergency"]
        if config.emergency_definitions:
            types = ", ".join(ed.type for ed in config.emergency_definitions)
            parts.append(f"→ if emergency ({types}): collect name/phone/address → transfer to on-call")
            parts.append("→ if transfer fails: apologise, assure <30 min callback")
        parts.append("→ if non-emergency: collect info, confirm next-business-day callback → wrap up")
        return " ".join(parts)

    def _build_notes(self, config: AgentConfig) -> str | None:
        notes = []
        seen = set()

        def _push(value: str | None) -> None:
            if not value:
                return
            cleaned = value.strip().strip(";,")
            if not cleaned or not any(ch.isalnum() for ch in cleaned):
                return
            if cleaned in seen:
                return
            seen.add(cleaned)
            notes.append(cleaned)

        for note in config.processing_notes:
            _push(note)
        if config.special_rules:
            for rule in config.special_rules:
                _push(rule)
        if config.integration and config.integration.system:
            _push(f"Integration: {config.integration.system}")
        return "; ".join(notes) if notes else None
