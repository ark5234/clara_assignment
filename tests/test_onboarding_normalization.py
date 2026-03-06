import json
from processors.onboarding_processor import OnboardingProcessor


def test_normalize_emergency_types_and_business_hours():
    proc = OnboardingProcessor()
    raw = {
        "business_hours": {
            "monday": {"open": "08:00", "close": "18:00"},
            "saturday": "closed",
            "timezone": "MST",
        },
        "emergency_types": [
            {
                "type": "active_fire_alarm",
                "collect": ["name", "phone"],
                "transfer_to": "(602) 555-0177",
                "timeout": 40,
                "fallback": "call 911 if life-threatening",
            }
        ],
    }

    normalized = proc._normalize_form_alternates(raw)
    assert "emergency_definitions" in normalized
    ed = normalized["emergency_definitions"][0]
    assert ed.get("collect_before_transfer") == ["name", "phone"]
    assert ed.get("transfer_target_phone") == "(602) 555-0177"

    bh = normalized["business_hours"]
    assert isinstance(bh["saturday"], dict)
    assert bh["saturday"]["closed"] is True


def test_build_emergency_defs_coercion():
    proc = OnboardingProcessor()
    raw_list = [
        {
            "type": "access_control_failure",
            "keywords": "door won't open, locked out",
            "collect": "name, phone, address",
            "timeout": "40 seconds",
        }
    ]
    defs = proc._build_emergency_defs(raw_list)
    assert len(defs) == 1
    d = defs[0]
    assert isinstance(d.collect_before_transfer, list)
    assert d.transfer_timeout_seconds == 40
