import json
from processors.onboarding_processor import OnboardingProcessor


def test_nested_json_in_strings():
    proc = OnboardingProcessor()
    # emergency item encoded as a JSON string
    raw = {
        "emergency_definitions": [
            json.dumps({
                "type": "active_fire_alarm",
                "collect": "name, phone",
                "transfer_to": "(602) 555-0177",
                "timeout": "40 seconds",
            })
        ]
    }

    sanitized = proc._sanitize_onboarding_raw(raw)
    defs = proc._build_emergency_defs(sanitized.get("emergency_definitions", []))
    assert len(defs) == 1
    d = defs[0]
    assert isinstance(d.collect_before_transfer, list)
    assert d.transfer_timeout_seconds == 40


def test_business_hours_varied_formats():
    proc = OnboardingProcessor()
    raw = {
        "business_hours": {
            "monday": "07:30-17:00",
            "tuesday": {"open": "08:00"},
            "thursday": "closed.",
            "saturday": None,
        }
    }

    sanitized = proc._sanitize_onboarding_raw(raw)
    bh = proc._build_business_hours(sanitized.get("business_hours", {}))
    assert bh.monday.open == "07:30"
    assert bh.thursday.closed is True
    # saturday None -> not closed by parser, but still returns a TimeSlot
    assert hasattr(bh.saturday, "open")


def test_integration_mapping_from_compact_form():
    proc = OnboardingProcessor()
    raw = {"integration": {"system": "ServiceTitan", "note": "manual dispatch", "auto_create_jobs": False}}
    normalized = proc._normalize_form_alternates(raw)
    assert "integration_constraints" in normalized
    ic = normalized["integration_constraints"][0]
    assert ic["system"] == "ServiceTitan"
    assert "manual dispatch" in ic["rule_description"]
