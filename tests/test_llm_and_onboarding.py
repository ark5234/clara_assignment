from utils.llm_client import LLMClient
from processors.onboarding_processor import OnboardingProcessor
from schemas.agent_config import AgentConfig
from datetime import datetime, timezone


def test_parse_fenced_json():
    sample = "Here is the JSON:\n\n```json\n{\n  \"field\": \"x\",\n  \"question\": \"What is X?\"\n}\n```"
    parsed = LLMClient._parse_json(sample)
    assert isinstance(parsed, dict)
    assert parsed.get("field") == "x"


def test_onboarding_unknowns_normalization():
    # Create a minimal v1 config
    now = datetime.now(timezone.utc).isoformat()
    v1 = AgentConfig(
        config_id="case_test",
        version="v1",
        created_at=now,
        updated_at=now,
        source="demo",
    )

    proc = OnboardingProcessor()
    # Provide onboarding payload with mixed unknowns formats
    onboarding = {
        "questions_or_unknowns": [
            {"field": "addr", "question": "What is the address?", "priority": "high"},
            "field: contact; question: Who is the contact?; priority: medium",
            "{\"field\": \"phone\", \"question\": \"Phone?\"}",
        ]
    }

    v2 = proc._merge(v1, onboarding, source="onboarding_form")
    # All unknowns should be present and have non-None question strings
    for u in v2.questions_or_unknowns:
        assert u.field
        assert isinstance(u.question, str)
