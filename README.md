# Clara AI — Voice Agent Automation Pipeline

Automated pipeline that transforms raw demo call and onboarding transcripts into production-ready Retell AI voice agent configurations for service trade businesses (fire protection, HVAC, electrical, security).

---

## What it does

| Input | Output |
|---|---|
| Demo call transcript (.txt) | `v1/agent_config.json` — internal config model |
| Onboarding call transcript (.txt) or form (.json) | `v1/account_memo.json` — structured account memo |
| | `v1/retell_spec.json` — Retell agent import spec |
| | `v1/agent_prompt.md` — production system prompt |
| v1 + onboarding → | `v2/` versions of all above |
| | `changelog/changes.json` + `changes.md` — v1→v2 diff |

---

## Quick start

### 1. Install dependencies

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure your LLM provider

Copy `.env.example` to `.env` and fill in your key:

```bash
cp .env.example .env
```

**Zero-cost options (pick one):**

| Provider | Free limit | Setup |
|---|---|---|
| **Gemini** (recommended) | 15 req/min, 1M tokens/day | Get key at [aistudio.google.com](https://aistudio.google.com) |
| **Groq** | 6K tokens/min, 500K/day | Get key at [console.groq.com](https://console.groq.com) |
| **Ollama** (local) | Unlimited | Install [ollama.ai](https://ollama.ai), run `ollama pull llama3.2` |

In `.env`, set:
```
LLM_PROVIDER=gemini        # or groq / ollama / openai
GEMINI_API_KEY=your_key_here
```

---

## CLI usage

### Process a single demo call

```bash
python main.py demo --case-id case_001 --transcript data/samples/case_001/demo_transcript.txt
```

Produces: `outputs/accounts/case_001/v1/`

### Process onboarding (transcript)

```bash
python main.py onboard --case-id case_001 --transcript data/samples/case_001/onboarding_transcript.txt
```

Produces: `outputs/accounts/case_001/v2/` + `changelog/`

### Process onboarding (form)

```bash
python main.py form --case-id case_002 --form data/samples/case_002/onboarding_form.json
```

### Batch process all 5 sample cases

```bash
python main.py batch --cases-dir data/samples/
```

### Show status for a case

```bash
python main.py status --case-id case_001
```

### Show v1→v2 diff as a Rich table

```bash
python main.py diff --case-id case_001
python main.py diff --case-id case_001 --show-raw   # also print flat diff JSON
```

### Inspect the change log

```bash
python main.py inspect --case-id case_001 --version v2
```

---

## Output structure

```
outputs/accounts/
  case_001/
    v1/
      agent_config.json     ← Full internal AgentConfig model
      account_memo.json     ← Structured account memo
      retell_spec.json      ← Retell agent import spec (paste into UI)
      agent_prompt.md       ← Production system prompt
    v2/
      agent_config.json
      account_memo.json
      retell_spec.json
      agent_prompt.md
    changelog/
      changes.json          ← Machine-readable v1→v2 diff
      changes.md            ← Human-readable changelog
    processing_log.json     ← Append-only run history
    input_hashes.json       ← SHA-256 hashes (idempotency)
outputs/
  task_tracker.json         ← Completed pipeline task log
```

---

## Sample cases

| Case | Company | Industry | Has onboarding? |
|---|---|---|---|
| case_001 | Premier Fire Protection | Fire protection | ✅ transcript |
| case_002 | Arctic HVAC Services | HVAC | ✅ form |
| case_003 | Electra Electrical Services | Electrical | ✅ transcript (has hour conflict) |
| case_004 | Summit Sprinkler Systems | Fire protection | ✅ transcript |
| case_005 | Guardian Alarm & Security | Electrical/Security | ✅ form |

---

## Architecture

```
main.py  (Click CLI)
  └── pipeline/orchestrator.py
        ├── processors/
        │     ├── demo_processor.py       → LLM extraction from demo transcript → v1
        │     ├── onboarding_processor.py → merges v1 + onboarding → v2 with changelog
        │     └── form_processor.py       → merges v1 + JSON form → v2
        ├── generators/
        │     ├── prompt_generator.py     → AgentConfig → Retell system prompt .md
        │     ├── account_memo_generator.py → AgentConfig → AccountMemo JSON
        │     ├── retell_spec_generator.py  → AgentConfig + prompt → RetellAgentSpec JSON
        │     └── changelog_generator.py   → v1 + v2 → changes.json + changes.md
        ├── storage/version_store.py      → file I/O, idempotency, artifact paths
        └── utils/
              ├── llm_client.py           → universal LLM wrapper (Gemini/Groq/Ollama/OpenAI)
              ├── task_tracker.py         → local JSON log + optional GitHub Issues
              └── logger.py              → rich logging
schemas/
  ├── agent_config.py   ← core internal model (AgentConfig, BusinessHours, etc.)
  ├── account_memo.py   ← output: AccountMemo
  └── retell_spec.py    ← output: RetellAgentSpec
workflows/
  └── n8n_workflow.json ← n8n automation workflow (file trigger → full pipeline)
```

---

## Retell AI integration

Retell's free tier does not provide API access for agent creation. Each `retell_spec.json` includes a `retell_import_instructions` block with step-by-step instructions:

1. Log in at [app.retellai.com](https://app.retellai.com)
2. Create Agent → Blank Agent
3. Set agent name, paste the `system_prompt`, choose voice
4. Add call transfer targets from `call_transfer_protocol.transfer_targets`
5. Configure fallback from `fallback_protocol`

---

## n8n automation

Import `workflows/n8n_workflow.json` into your n8n instance to automate the full pipeline:

- **File trigger** — automatically runs when a new `demo_transcript.txt` appears in `data/samples/`
- **Batch trigger** — scheduled daily run for all cases
- Runs Python CLI commands via Execute Command nodes
- Logs tasks to the task tracker on completion

See `_clara_import_instructions` inside the JSON for step-by-step import guide.

---

## Task tracker

Every successful pipeline run is logged to `outputs/task_tracker.json`.

To also create GitHub Issues for unresolved unknowns, set in `.env`:
```
TASK_TRACKER_BACKEND=github
GITHUB_TOKEN=your_personal_access_token
GITHUB_REPO=yourorg/yourrepo
```

---

## LLM provider details

| Provider | `LLM_PROVIDER` value | `base_url` | Notes |
|---|---|---|---|
| Gemini (free) | `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` | 1.5 Flash, JSON mode supported |
| Groq (free) | `groq` | `https://api.groq.com/openai/v1` | Llama-3.3-70B, JSON mode supported |
| Ollama (local) | `ollama` | `http://localhost:11434/v1` | Run `ollama pull llama3.2` first |
| OpenAI (paid) | `openai` | `https://api.openai.com/v1` | GPT-4o, reference only |

---

## Known limitations

- **Retell free tier** — agent creation requires manual copy-paste from `retell_spec.json` into the Retell UI. No programmatic sync.
- **LLM accuracy** — extraction quality depends on transcript clarity. Verify all config fields before deploying to production.
- **Conflict resolution** — when v1 (demo assumption) and v2 (onboarding confirmation) disagree on a field, v2 always wins and a conflict is flagged in the changelog. Review flagged conflicts manually.
- **Ollama** — JSON mode is not enforced (model-dependent). The client uses instruction-augmented prompts and lenient fence-stripping to recover valid JSON.
