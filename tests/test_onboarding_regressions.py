import json
from datetime import datetime, timezone
from pathlib import Path

from processors.onboarding_processor import OnboardingProcessor
from schemas.agent_config import AgentConfig, BusinessHours, EmergencyDefinition, TimeSlot, UnknownItem


def _base_config() -> AgentConfig:
    now = datetime.now(timezone.utc).isoformat()
    return AgentConfig(
        config_id="case_test",
        version="v1",
        created_at=now,
        updated_at=now,
        source="demo",
        business_hours=BusinessHours(
            timezone="standard business hours",
            monday=TimeSlot(open=None, close=None, closed=False),
            tuesday=TimeSlot(open=None, close=None, closed=False),
            wednesday=TimeSlot(open=None, close=None, closed=False),
            thursday=TimeSlot(open=None, close=None, closed=False),
            friday=TimeSlot(open=None, close=None, closed=False),
            saturday=TimeSlot(closed=True),
            sunday=TimeSlot(closed=True),
            is_confirmed=False,
        ),
        emergency_definitions=[
            EmergencyDefinition(type="legacy_emergency", description="Legacy emergency from demo")
        ],
        questions_or_unknowns=[
            UnknownItem(
                field="emergency_definitions.legacy_emergency.transfer_target",
                question="What number should legacy emergencies transfer to?",
                priority="high",
            )
        ],
    )


def test_placeholder_schema_values_do_not_replace_existing_v1_data():
    proc = OnboardingProcessor()
    v1 = _base_config()

    placeholder_payload = {
        "business_hours": {
            "timezone": "IANA timezone string or null — e.g. America/Chicago",
            "monday": {"open": "HH:MM or null", "close": "HH:MM or null", "closed": False},
            "tuesday": {"open": "HH:MM or null", "close": "HH:MM or null", "closed": False},
            "wednesday": {"open": "HH:MM or null", "close": "HH:MM or null", "closed": False},
            "thursday": {"open": "HH:MM or null", "close": "HH:MM or null", "closed": False},
            "friday": {"open": "HH:MM or null", "close": "HH:MM or null", "closed": False},
            "saturday": {"open": None, "close": None, "closed": True},
            "sunday": {"open": None, "close": None, "closed": True},
            "notes": "string or null",
        },
        "emergency_definitions": [
            {
                "type": "string — concise snake_case type name",
                "description": "string — how it was described",
                "keywords": ["trigger keywords stated"],
                "transfer_target_name": "string or null",
                "transfer_target_phone": "string or null",
                "transfer_target_type": "phone_tree or individual or voicemail or dispatch or null",
                "fallback_on_timeout": "string or null — what to do if transfer fails",
            }
        ],
        "non_emergency_routing": {
            "business_hours_action": "transfer or voicemail or null",
            "after_hours_action": "collect_and_callback or voicemail or transfer or null",
        },
        "integration_constraints": [
            {
                "system": "string — e.g. ServiceTrade",
                "rule_description": "string — exact rule as stated",
                "job_types_excluded": ["list of job types NEVER to auto-create"],
            }
        ],
        "questions_or_unknowns": [
            {"field": "string", "question": "string", "priority": "high or medium or low"}
        ],
    }

    sanitized = proc._sanitize_onboarding_raw(placeholder_payload)
    v2 = proc._merge(v1, sanitized, source="onboarding_call")

    assert v2.business_hours.timezone == "standard business hours"
    assert [ed.type for ed in v2.emergency_definitions] == ["legacy_emergency"]
    assert not any("string or null" in json.dumps(ed.model_dump()) for ed in v2.emergency_definitions)
    assert not any(u.field == "string" for u in v2.questions_or_unknowns)


def test_superseded_emergency_unknowns_are_retired_when_taxonomy_changes():
    proc = OnboardingProcessor()
    v1 = _base_config()
    v1.questions_or_unknowns.extend(
        [
            UnknownItem(
                field="emergency_definitions.unknown.transfer_target",
                question=(
                    "What phone number or destination should emergency 'unknown' "
                    "calls be transferred to?"
                ),
                priority="high",
            ),
            UnknownItem(
                field="emergency_definitions.server_room_hvac_failure.transfer_target",
                question=(
                    "What phone number or destination should emergency "
                    "'server_room_hvac_failure' calls be transferred to?"
                ),
                priority="high",
            ),
        ]
    )

    onboarding = {
        "emergency_definitions": [
            {
                "type": "hvac_critical_failure",
                "description": "Critical HVAC failure",
                "keywords": ["server room"],
                "transfer_target_phone": "214-555-0192",
                "transfer_target_name": "Dispatch",
                "transfer_target_type": "dispatch",
                "transfer_timeout_seconds": 45,
                "fallback_on_timeout": "Notify dispatch and call back within 30 minutes.",
            },
            {
                "type": "hvac_extreme_weather_failure",
                "description": "Extreme weather HVAC failure",
                "keywords": ["freezing inside"],
                "transfer_target_phone": "214-555-0192",
                "transfer_target_name": "Dispatch",
                "transfer_target_type": "dispatch",
                "transfer_timeout_seconds": 45,
                "fallback_on_timeout": "Notify dispatch and call back within 30 minutes.",
            },
        ]
    }

    v2 = proc._merge(v1, onboarding, source="onboarding_form")
    remaining = {u.field for u in v2.open_unknowns()}

    assert "emergency_definitions.unknown.transfer_target" not in remaining
    assert "emergency_definitions.server_room_hvac_failure.transfer_target" not in remaining
    assert "emergency_definitions.hvac_critical_failure.transfer_target" not in remaining


def test_business_hours_range_strings_are_normalized():
    proc = OnboardingProcessor()
    business_hours = proc._build_business_hours(
        {
            "timezone": "Central Time",
            "monday": {"open": "7:30 AM to 5:00 PM", "close": None, "closed": False},
            "tuesday": {"open": "07:30", "close": "17:00", "closed": False},
            "wednesday": {"open": "07:30", "close": "17:00", "closed": False},
            "thursday": {"open": "07:30", "close": "17:00", "closed": False},
            "friday": {"open": "07:30", "close": "17:00", "closed": False},
            "saturday": "closed",
            "sunday": "closed",
        }
    )

    assert business_hours.timezone == "America/Chicago"
    assert business_hours.monday.open == "07:30"
    assert business_hours.monday.close == "17:00"


def test_n8n_workflow_uses_supported_cli_arguments():
    workflow_path = Path(__file__).resolve().parents[1] / "workflows" / "n8n_workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))

    commands = [
        node.get("parameters", {}).get("command", "")
        for node in workflow.get("nodes", [])
    ]
    joined = "\n".join(commands)

    assert "--file" not in joined
    assert (
        "main.py demo --case-id {{ $json.case_id }} --transcript "
        "{{ $json.transcript_path }}" in joined
    )
    assert (
        "main.py onboard --case-id {{ $json.case_id }} --transcript "
        "data/samples/{{ $json.case_id }}/onboarding_transcript.txt" in joined
    )
    assert (
        "main.py form --case-id {{ $json.case_id }} --form "
        "data/samples/{{ $json.case_id }}/onboarding_form.json" in joined
    )
