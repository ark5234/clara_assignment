"""
Central configuration for Clara AI pipeline.
Loads settings from environment variables / .env file.

ZERO-COST LLM OPTIONS (in order of recommendation):
  1. gemini   — Google Gemini 1.5 Flash (free tier: 15 RPM, 1M TPD)
                Get key at: https://aistudio.google.com/apikey
  2. groq     — Groq Llama 3.3 70B (free tier: 6K TPM, 500K TPD)
                Get key at: https://console.groq.com
  3. ollama   — Local models via Ollama (completely free, runs offline)
                Install: https://ollama.ai
  4. openai   — OpenAI GPT-4o (paid, for reference only)
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── LLM Provider selection ─────────────────────────────────────────────────
# Default: "gemini" (zero cost). Change to "groq" | "ollama" | "openai"
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini")

# API keys per provider (only the selected provider's key is required)
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# Per-provider model overrides (sensible defaults set below)
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Shared setting
OPENAI_TEMPERATURE: float = float(os.getenv("OPENAI_TEMPERATURE", "0.0"))

# ── Storage ────────────────────────────────────────────────────────────────
# Output directory root — per-account outputs are stored under here
OUTPUT_DIR: str = os.getenv("CLARA_OUTPUT_DIR", "outputs/accounts")

# ── Logging ────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("CLARA_LOG_LEVEL", "INFO")

# ── Task tracker ───────────────────────────────────────────────────────────
# Options: "local" (JSON file), "github" (GitHub Issues via free API)
TASK_TRACKER_BACKEND: str = os.getenv("TASK_TRACKER_BACKEND", "local")
TASK_TRACKER_FILE: str = os.getenv("TASK_TRACKER_FILE", "outputs/task_tracker.json")
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")         # for GitHub Issues backend
GITHUB_REPO: str = os.getenv("GITHUB_REPO", "")           # e.g. "your-org/your-repo"
