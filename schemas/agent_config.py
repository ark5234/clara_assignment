"""
Core data models for Clara AI agent configuration.

All data flowing through the pipeline is typed using these Pydantic models.
Versioning, change tracking, and unknown-flagging are first-class concerns.
"""
from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Primitive building blocks
# ---------------------------------------------------------------------------

class TimeSlot(BaseModel):
    """Open/close times for a single day in 24-hour HH:MM format."""
    open: Optional[str] = None   # e.g. "07:30"
    close: Optional[str] = None  # e.g. "17:00"
    closed: bool = False


class BusinessHours(BaseModel):
    """Weekly business hours schedule with timezone."""
    timezone: Optional[str] = None  # IANA tz: "America/Chicago"
    monday: TimeSlot = Field(default_factory=TimeSlot)
    tuesday: TimeSlot = Field(default_factory=TimeSlot)
    wednesday: TimeSlot = Field(default_factory=TimeSlot)
    thursday: TimeSlot = Field(default_factory=TimeSlot)
    friday: TimeSlot = Field(default_factory=TimeSlot)
    saturday: TimeSlot = Field(default_factory=lambda: TimeSlot(closed=True))
    sunday: TimeSlot = Field(default_factory=lambda: TimeSlot(closed=True))
    notes: Optional[str] = None
    # Tracks whether hours came from vague demo hints vs. confirmed onboarding data
    is_confirmed: bool = False

    def is_fully_specified(self) -> bool:
        """Return True only when timezone and all weekday slots are complete."""
        if not self.timezone:
            return False
        for day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
            slot: TimeSlot = getattr(self, day)
            if not slot.closed and (not slot.open or not slot.close):
                return False
        return True

    def human_readable(self) -> str:
        """Return a short human-readable summary of the schedule."""
        days_map = {
            "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed",
            "thursday": "Thu", "friday": "Fri", "saturday": "Sat", "sunday": "Sun",
        }
        lines = []
        for attr, label in days_map.items():
            slot: TimeSlot = getattr(self, attr)
            if slot.closed:
                lines.append(f"{label}: Closed")
            elif slot.open and slot.close:
                lines.append(f"{label}: {slot.open} – {slot.close}")
            else:
                lines.append(f"{label}: Hours not confirmed")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class RoutingTarget(BaseModel):
    name: str
    phone: Optional[str] = None
    type: str = "unknown"  # "phone_tree" | "individual" | "voicemail" | "dispatch"


class NonEmergencyRouting(BaseModel):
    business_hours_action: Optional[str] = None   # "transfer" | "voicemail"
    business_hours_target: Optional[RoutingTarget] = None
    after_hours_action: Optional[str] = None      # "collect_and_callback" | "voicemail" | "transfer"
    collect_fields: List[str] = Field(default_factory=list)  # ["name", "phone", "description"]
    callback_promise: Optional[str] = None         # e.g. "during business hours"


# ---------------------------------------------------------------------------
# Emergencies
# ---------------------------------------------------------------------------

class EmergencyDefinition(BaseModel):
    type: str
    description: str
    keywords: List[str] = Field(default_factory=list)
    collect_before_transfer: List[str] = Field(
        default_factory=lambda: ["name", "phone", "address"]
    )
    transfer_target: Optional[RoutingTarget] = None
    transfer_timeout_seconds: Optional[int] = None
    fallback_on_timeout: Optional[str] = None


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

class IntegrationConstraint(BaseModel):
    system: str
    rule_description: str
    job_types_excluded: List[str] = Field(default_factory=list)
    job_types_auto_create: List[str] = Field(default_factory=list)


class IntegrationConfig(BaseModel):
    system: Optional[str] = None   # "ServiceTrade"
    enabled: bool = False
    constraints: List[IntegrationConstraint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Version control / audit trail
# ---------------------------------------------------------------------------

class ChangeLogEntry(BaseModel):
    """Records every field transition between versions."""
    timestamp: str
    version_from: str
    version_to: str
    field_path: str          # dot-notation path, e.g. "business_hours.timezone"
    old_value: Any = None
    new_value: Any = None
    source: str              # "demo" | "onboarding_call" | "onboarding_form"
    reason: Optional[str] = None
    conflict_noted: bool = False


class UnknownItem(BaseModel):
    """Represents a missing or unresolved configuration detail."""
    field: str               # machine-readable identifier
    question: str            # human-readable question to resolve this
    context: Optional[str] = None
    priority: str = "medium"  # "high" | "medium" | "low"
    source_stage: str = "demo"  # "demo" | "onboarding"
    resolved: bool = False


# ---------------------------------------------------------------------------
# Client profile
# ---------------------------------------------------------------------------

class ClientInfo(BaseModel):
    name: Optional[str] = None
    industry: Optional[str] = None  # "fire_protection" | "hvac" | "electrical" | etc.
    office_address: Optional[str] = None
    service_types: List[str] = Field(default_factory=list)
    pain_points: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Root configuration document
# ---------------------------------------------------------------------------

class AgentConfig(BaseModel):
    """
    The canonical configuration for a Clara voice agent instance.

    v1  — derived from demo call transcript (directional, may have unknowns)
    v2  — derived from v1 + onboarding call/form (confirmed operational config)
    """
    config_id: str
    version: str                 # "v1" | "v2"
    created_at: str              # ISO-8601 UTC
    updated_at: str              # ISO-8601 UTC
    source: str                  # "demo" | "onboarding_call" | "onboarding_form"

    client: ClientInfo = Field(default_factory=ClientInfo)
    business_hours: Optional[BusinessHours] = None
    emergency_definitions: List[EmergencyDefinition] = Field(default_factory=list)
    non_emergency_routing: Optional[NonEmergencyRouting] = None
    integration: Optional[IntegrationConfig] = None
    special_rules: List[str] = Field(default_factory=list)

    # Audit / metadata
    questions_or_unknowns: List[UnknownItem] = Field(default_factory=list)
    change_log: List[ChangeLogEntry] = Field(default_factory=list)
    processing_notes: List[str] = Field(default_factory=list)

    # SHA-256 of source input(s) — used for idempotency checks
    input_hash: Optional[str] = None

    # Generated output
    agent_prompt: Optional[str] = None

    # Raw LLM extraction stored for auditability
    raw_extraction: Optional[dict] = None

    def open_unknowns(self) -> List[UnknownItem]:
        return [u for u in self.questions_or_unknowns if not u.resolved]

    def high_priority_unknowns(self) -> List[UnknownItem]:
        return [u for u in self.open_unknowns() if u.priority == "high"]
