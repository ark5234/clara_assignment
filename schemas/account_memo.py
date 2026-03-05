"""
Account Memo — the canonical structured output for each Clara AI client account.

This is the "official" output format per the assignment specification.
It is generated from an AgentConfig by the AccountMemoGenerator.
One memo is produced per version (v1, v2).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BusinessHoursMemo(BaseModel):
    """Simplified business hours for the memo (non-per-day format for readability)."""
    days: List[str] = Field(default_factory=list)   # e.g. ["Monday", "Tuesday", ..., "Friday"]
    start: Optional[str] = None                      # "07:30"
    end: Optional[str] = None                        # "17:00"
    timezone: Optional[str] = None
    notes: Optional[str] = None
    is_confirmed: bool = False


class EmergencyRoutingRule(BaseModel):
    emergency_type: str
    description: str
    trigger_keywords: List[str] = Field(default_factory=list)
    collect_before_transfer: List[str] = Field(
        default_factory=lambda: ["name", "phone", "address"]
    )
    transfer_to_name: Optional[str] = None
    transfer_to_phone: Optional[str] = None
    transfer_to_type: Optional[str] = None    # phone_tree | individual | dispatch
    timeout_seconds: Optional[int] = None
    fallback: Optional[str] = None


class NonEmergencyRoutingRule(BaseModel):
    business_hours_action: Optional[str] = None     # transfer | voicemail
    business_hours_target: Optional[str] = None
    business_hours_phone: Optional[str] = None
    after_hours_action: Optional[str] = None         # collect_and_callback | voicemail
    after_hours_collect: List[str] = Field(default_factory=list)
    callback_promise: Optional[str] = None


class CallTransferRules(BaseModel):
    default_timeout_seconds: Optional[int] = None
    collect_before_transfer: List[str] = Field(
        default_factory=lambda: ["name", "phone", "address"]
    )
    on_timeout_message: Optional[str] = None
    on_failure_action: Optional[str] = None


class AccountMemo(BaseModel):
    """
    Structured account memo — the primary human/machine-readable output.

    Required fields per Clara AI assignment spec:
      account_id, company_name, business_hours, office_address,
      services_supported, emergency_definition, emergency_routing_rules,
      non_emergency_routing_rules, call_transfer_rules,
      integration_constraints, after_hours_flow_summary,
      office_hours_flow_summary, questions_or_unknowns, notes
    """
    account_id: str
    version: str
    generated_at: str      # ISO-8601 UTC

    # Client info
    company_name: Optional[str] = None
    industry: Optional[str] = None
    office_address: Optional[str] = None
    services_supported: List[str] = Field(default_factory=list)

    # Hours
    business_hours: Optional[BusinessHoursMemo] = None

    # Emergency
    emergency_definition: List[str] = Field(default_factory=list)
    emergency_routing_rules: List[EmergencyRoutingRule] = Field(default_factory=list)

    # Non-emergency
    non_emergency_routing_rules: Optional[NonEmergencyRoutingRule] = None

    # Transfer
    call_transfer_rules: Optional[CallTransferRules] = None

    # Integration
    integration_constraints: List[str] = Field(default_factory=list)

    # Flow summaries (plain English, for quick review)
    office_hours_flow_summary: Optional[str] = None
    after_hours_flow_summary: Optional[str] = None

    # Unknowns
    questions_or_unknowns: List[Dict[str, Any]] = Field(default_factory=list)

    # Free-form notes
    notes: Optional[str] = None
