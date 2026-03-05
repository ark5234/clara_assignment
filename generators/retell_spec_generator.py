"""
RetellSpecGenerator — converts an AgentConfig + generated prompt into a
Retell Agent Draft Spec JSON.

The spec is designed to be either:
  a) Imported into Retell UI manually (free tier — no API needed)
  b) POSTed to the Retell Agents API if/when API access is available
"""
from __future__ import annotations

from datetime import datetime, timezone

from schemas.agent_config import AgentConfig
from schemas.retell_spec import (
    CallTransferProtocol,
    FallbackProtocol,
    RetellAgentSpec,
    TransferTarget,
)
from utils.logger import get_logger

logger = get_logger(__name__)


class RetellSpecGenerator:
    def generate(self, config: AgentConfig, agent_prompt: str) -> RetellAgentSpec:
        logger.debug(f"[{config.config_id}] Generating Retell spec {config.version}")

        agent_name = self._agent_name(config)
        key_variables = self._key_variables(config)
        transfer_protocol = self._transfer_protocol(config)
        fallback_protocol = self._fallback_protocol(config)

        return RetellAgentSpec(
            agent_name=agent_name,
            version=config.version,
            generated_at=datetime.now(timezone.utc).isoformat(),
            account_id=config.config_id,
            voice_style="professional",
            voice_id="11labs-Adrian",
            system_prompt=agent_prompt,
            key_variables=key_variables,
            call_transfer_protocol=transfer_protocol,
            fallback_protocol=fallback_protocol,
            retell_settings={
                "response_engine": "retell-llm",
                "language": "en-US",
                "enable_backchannel": True,
                "ambient_sound": "off",
                "max_call_duration_ms": 300000,
                "reminder_trigger_ms": 10000,
                "reminder_max_count": 1,
                "boosted_keywords": self._boosted_keywords(config),
            },
        )

    # ------------------------------------------------------------------
    def _agent_name(self, config: AgentConfig) -> str:
        company = config.client.name or config.config_id
        return f"Clara — {company} ({config.version})"

    def _key_variables(self, config: AgentConfig) -> dict:
        bh = config.business_hours
        variables: dict = {
            "company_name": config.client.name or "",
            "industry": config.client.industry or "",
            "timezone": bh.timezone if bh else "",
            "business_hours_start": "",
            "business_hours_end": "",
            "business_days": "",
            "office_address": config.client.office_address or "",
        }

        if bh and bh.is_confirmed:
            from schemas.agent_config import TimeSlot
            open_days = []
            for attr in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
                slot: TimeSlot = getattr(bh, attr)
                if not slot.closed:
                    open_days.append(attr.capitalize())
            if open_days:
                variables["business_days"] = ", ".join(open_days)
            mon = bh.monday
            fri = bh.friday
            if mon.open:
                variables["business_hours_start"] = mon.open
            if fri.close:
                variables["business_hours_end"] = fri.close

        # Emergency transfer targets
        for i, ed in enumerate(config.emergency_definitions):
            if ed.transfer_target and ed.transfer_target.phone:
                variables[f"emergency_phone_{i+1}"] = ed.transfer_target.phone
                variables[f"emergency_type_{i+1}"] = ed.type

        # Non-emergency target
        ner = config.non_emergency_routing
        if ner and ner.business_hours_target and ner.business_hours_target.phone:
            variables["office_phone"] = ner.business_hours_target.phone

        return variables

    def _transfer_protocol(self, config: AgentConfig) -> CallTransferProtocol:
        targets = []
        for ed in config.emergency_definitions:
            if ed.transfer_target:
                targets.append(TransferTarget(
                    label=f"Emergency: {ed.type}",
                    phone=ed.transfer_target.phone,
                    type="phone_number",
                ))

        ner = config.non_emergency_routing
        if ner and ner.business_hours_target and ner.business_hours_target.phone:
            targets.append(TransferTarget(
                label="Main office (business hours)",
                phone=ner.business_hours_target.phone,
                type="phone_number",
            ))

        # Default timeout from first emergency def
        timeout = None
        for ed in config.emergency_definitions:
            if ed.transfer_timeout_seconds:
                timeout = ed.transfer_timeout_seconds
                break

        # Build announce message
        announce = (
            "Please hold — I'm connecting you now. "
            "I'll stay on the line until someone picks up."
        )

        return CallTransferProtocol(
            collect_before_transfer=["name", "phone", "address"],
            transfer_targets=targets,
            timeout_seconds=timeout,
            announce_transfer=True,
            announce_message=announce,
        )

    def _fallback_protocol(self, config: AgentConfig) -> FallbackProtocol:
        # Use fallback message from first emergency definition if available
        fallback_msg = None
        for ed in config.emergency_definitions:
            if ed.fallback_on_timeout:
                fallback_msg = ed.fallback_on_timeout
                break

        if not fallback_msg:
            fallback_msg = (
                "I wasn't able to connect you with our team right now. "
                "Your information has been recorded and someone will call you back shortly."
            )

        return FallbackProtocol(
            trigger="transfer_timeout",
            message=fallback_msg,
            actions=["log_caller_info", "send_followup_notification"],
            collect_callback_info=True,
        )

    def _boosted_keywords(self, config: AgentConfig) -> list[str]:
        """Keywords for Retell's speech recognition boost."""
        keywords = []
        for ed in config.emergency_definitions:
            keywords.extend(ed.keywords)
        if config.client.name:
            keywords.append(config.client.name)
        return list(set(keywords))[:20]  # Retell limit
