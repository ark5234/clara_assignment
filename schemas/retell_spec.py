"""
Retell Agent Draft Spec — the configuration artifact for importing into Retell AI.

Per assignment spec, this must include:
  agent_name, voice_style, system_prompt, key_variables,
  tool_invocation_placeholders, call_transfer_protocol,
  fallback_protocol, version

Note on Retell free tier:
  Retell does not currently allow programmatic agent creation on the free tier.
  This spec is designed to be pasted into the Retell UI manually.
  The `retell_import_instructions` field explains exactly where each value goes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TransferTarget(BaseModel):
    label: str
    phone: Optional[str] = None
    type: str = "phone_number"                # phone_number | warm_handoff | sip


class CallTransferProtocol(BaseModel):
    collect_before_transfer: List[str] = Field(
        default_factory=lambda: ["name", "phone", "address"]
    )
    transfer_targets: List[TransferTarget] = Field(default_factory=list)
    timeout_seconds: Optional[int] = None
    announce_transfer: bool = True
    announce_message: Optional[str] = None


class FallbackProtocol(BaseModel):
    trigger: str = "transfer_timeout"
    message: Optional[str] = None
    actions: List[str] = Field(default_factory=list)
    collect_callback_info: bool = True


class RetellAgentSpec(BaseModel):
    """
    Complete Retell AI agent configuration spec.
    """
    agent_name: str
    version: str                      # "v1" | "v2"
    generated_at: str                 # ISO-8601 UTC
    account_id: str

    # Voice and persona
    voice_style: str = "professional" # professional | friendly | calm
    voice_id: str = "11labs-Adrian"   # Retell voice ID — change in Retell UI as preferred

    # Core prompt
    system_prompt: str

    # Variables injected at runtime by Retell
    key_variables: Dict[str, Any] = Field(default_factory=dict)

    # Tool placeholders (functions Clara calls silently — never mentioned to caller)
    tool_invocation_placeholders: List[str] = Field(
        default_factory=lambda: [
            "transfer_call(destination_phone, caller_name, caller_phone)",
            "end_call(reason)",
            "send_sms_followup(caller_name, caller_phone, message)",   # optional
        ]
    )

    # Transfer and fallback logic
    call_transfer_protocol: CallTransferProtocol = Field(
        default_factory=CallTransferProtocol
    )
    fallback_protocol: FallbackProtocol = Field(
        default_factory=FallbackProtocol
    )

    # Retell-specific settings (fill in Retell UI)
    retell_settings: Dict[str, Any] = Field(
        default_factory=lambda: {
            "response_engine": "retell-llm",
            "language": "en-US",
            "enable_backchannel": True,
            "ambient_sound": "coffee-shop",
            "max_call_duration_ms": 300000,
            "reminder_trigger_ms": 10000,
            "reminder_max_count": 1,
        }
    )

    # Manual import instructions (since Retell free tier doesn't expose API)
    retell_import_instructions: Dict[str, str] = Field(
        default_factory=lambda: {
            "step_1": "Log in to Retell at https://app.retellai.com",
            "step_2": "Click 'Create Agent' → 'Blank Agent'",
            "step_3": "Set 'Agent Name' to the value in `agent_name`",
            "step_4": "Paste the value of `system_prompt` into the 'System Prompt' field",
            "step_5": "Set voice to match `voice_id` (or choose similar from the voice library)",
            "step_6": "Under 'Call Transfer', add each entry in `call_transfer_protocol.transfer_targets`",
            "step_7": "Set 'Max call duration' and other settings from `retell_settings`",
            "step_8": "Save and test the agent using the built-in phone number",
        }
    )
