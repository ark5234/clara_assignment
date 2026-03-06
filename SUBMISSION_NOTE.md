# Submission Note

This repository contains the completed Clara AI voice agent automation pipeline.

## Delivered scope

- Converts demo transcripts into versioned `v1` artifacts.
- Converts onboarding transcripts or forms into confirmed `v2` artifacts.
- Produces structured outputs including `agent_config.json`, `account_memo.json`, `retell_spec.json`, `agent_prompt.md`, and changelog files.
- Supports idempotent processing via input hashing.
- Includes local and hosted LLM provider support.
- Includes automated linting and test coverage through GitHub Actions.

## Verification completed

Use the commands in `README.md` under `Verification`:

```bash
flake8 --config .flake8
pytest -q
```

## Notes for assessment

- The repository has been cleaned of temporary lint/test artifact files.
- The primary branch now contains the latest validated fixes.
- Generated business artifacts are written to `outputs/` at runtime and are not committed.

## Suggested review path

1. Read `README.md` for setup, usage, and verification.
2. Inspect `main.py` and `pipeline/orchestrator.py` for the top-level workflow.
3. Review `processors/` and `generators/` for extraction and artifact generation logic.
4. Run the verification commands to confirm lint and test status.
