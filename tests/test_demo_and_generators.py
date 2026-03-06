from processors.demo_processor import DemoProcessor
from generators.prompt_generator import PromptGenerator


def test_build_v1_and_prompt_generation_minimal():
    proc = DemoProcessor()
    raw = {}
    case_id = "case_test"
    input_hash = "deadbeef"
    now = "2026-03-06T00:00:00Z"

    v1 = proc._build_v1_config(raw, case_id, input_hash, now)
    assert v1.config_id == case_id
    # ensure mandatory unknowns were added
    fields = {u.field for u in v1.questions_or_unknowns}
    assert "business_hours.timezone" in fields

    pg = PromptGenerator()
    prompt = pg.generate(v1)
    assert isinstance(prompt, str) and len(prompt) > 0
    assert "CLARA AI VOICE AGENT" in prompt
