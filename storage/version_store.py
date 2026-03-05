"""
File-based version store for Clara AI agent configurations.

Output structure per case:

  outputs/accounts/
    case_001/
      v1/
        agent_config.json     ← AgentConfig (internal model)
        account_memo.json     ← AccountMemo (assignment spec output)
        retell_spec.json      ← RetellAgentSpec (Retell import spec)
        agent_prompt.md       ← Production system prompt
      v2/
        agent_config.json
        account_memo.json
        retell_spec.json
        agent_prompt.md
      changelog/
        changes.json          ← Structured v1→v2 diff
        changes.md            ← Human-readable changelog
      processing_log.json     ← Append-only run log
      input_hashes.json       ← {version: sha256} for idempotency

Design:
- Idempotent: same input hash → no-op, returns cached path
- Immutable versions: v1 is never overwritten once saved
- Append-only processing log
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import config
from schemas.agent_config import AgentConfig
from utils.logger import get_logger

logger = get_logger(__name__)


class VersionStore:
    def __init__(self, output_dir: str | None = None) -> None:
        self.output_dir = output_dir or config.OUTPUT_DIR

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    def save(self, agent_config: AgentConfig) -> str:
        """
        Persist AgentConfig to disk. Returns the agent_config.json file path.
        Idempotent: skips if same version+hash already saved.
        """
        version = agent_config.version
        vdir = self._version_dir(agent_config.config_id, version)
        os.makedirs(vdir, exist_ok=True)

        config_path = os.path.join(vdir, "agent_config.json")
        prompt_path = os.path.join(vdir, "agent_prompt.md")
        hashes_path = os.path.join(self._case_dir(agent_config.config_id), "input_hashes.json")

        # ---- Idempotency check ----
        raw_hashes = self._load_json(hashes_path)
        hashes: dict = raw_hashes if isinstance(raw_hashes, dict) else {}
        if (
            version in hashes
            and agent_config.input_hash
            and hashes.get(version) == agent_config.input_hash
            and os.path.exists(config_path)
        ):
            logger.info(
                f"[{agent_config.config_id}] {version} already saved with identical input hash — skipping"
            )
            return config_path

        # ---- Write config JSON ----
        with open(config_path, "w", encoding="utf-8") as fh:
            fh.write(agent_config.model_dump_json(indent=2))
        logger.info(f"[{agent_config.config_id}] Saved {version} agent_config → {config_path}")

        # ---- Write prompt markdown ----
        if agent_config.agent_prompt:
            with open(prompt_path, "w", encoding="utf-8") as fh:
                fh.write(agent_config.agent_prompt)
            logger.info(f"[{agent_config.config_id}] Saved {version} agent_prompt → {prompt_path}")

        # ---- Update input hash tracking ----
        if agent_config.input_hash:
            hashes[version] = agent_config.input_hash
            os.makedirs(self._case_dir(agent_config.config_id), exist_ok=True)
            with open(hashes_path, "w", encoding="utf-8") as fh:
                json.dump(hashes, fh, indent=2)

        # ---- Append to processing log ----
        self._append_log(
            agent_config.config_id,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": version,
                "source": agent_config.source,
                "unknowns_count": len(agent_config.open_unknowns()),
                "changes_count": len(agent_config.change_log),
                "config_path": config_path,
            },
        )

        return config_path

    def save_memo(self, case_id: str, version: str, memo_json: str) -> str:
        """Persist AccountMemo JSON. Returns file path."""
        path = os.path.join(self._version_dir(case_id, version), "account_memo.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(memo_json)
        logger.info(f"[{case_id}] Saved {version} account_memo → {path}")
        return path

    def save_retell_spec(self, case_id: str, version: str, spec_json: str) -> str:
        """Persist RetellAgentSpec JSON. Returns file path."""
        path = os.path.join(self._version_dir(case_id, version), "retell_spec.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(spec_json)
        logger.info(f"[{case_id}] Saved {version} retell_spec → {path}")
        return path

    def save_changelog(self, case_id: str, changes_dict: dict, changes_md: str) -> tuple[str, str]:
        """Persist changelog JSON and Markdown. Returns (json_path, md_path)."""
        clog_dir = os.path.join(self._case_dir(case_id), "changelog")
        os.makedirs(clog_dir, exist_ok=True)
        json_path = os.path.join(clog_dir, "changes.json")
        md_path = os.path.join(clog_dir, "changes.md")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(changes_dict, fh, indent=2, default=str)
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(changes_md)
        logger.info(f"[{case_id}] Saved changelog → {clog_dir}")
        return json_path, md_path

    def load(self, case_id: str, version: str) -> Optional[AgentConfig]:
        """Load a saved AgentConfig. Returns None if not found."""
        path = os.path.join(self._version_dir(case_id, version), "agent_config.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return AgentConfig(**data)

    def exists(self, case_id: str, version: str) -> bool:
        path = os.path.join(self._version_dir(case_id, version), "agent_config.json")
        return os.path.exists(path)

    def list_cases(self) -> list[str]:
        """Return all case IDs in the output directory."""
        if not os.path.exists(self.output_dir):
            return []
        return [
            d for d in os.listdir(self.output_dir)
            if os.path.isdir(os.path.join(self.output_dir, d))
        ]

    def get_processing_log(self, case_id: str) -> list:
        """Return the processing log for a case."""
        path = os.path.join(self._case_dir(case_id), "processing_log.json")
        raw = self._load_json(path)
        return raw if isinstance(raw, list) else []

    def get_changelog(self, case_id: str) -> Optional[dict]:
        """Return the v1→v2 changelog dict, or None if not generated yet."""
        path = os.path.join(self._case_dir(case_id), "changelog", "changes.json")
        raw = self._load_json(path)
        return raw if isinstance(raw, dict) else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _case_dir(self, case_id: str) -> str:
        return os.path.join(self.output_dir, case_id)

    def _version_dir(self, case_id: str, version: str) -> str:
        vdir = os.path.join(self._case_dir(case_id), version)
        os.makedirs(vdir, exist_ok=True)
        return vdir

    def _load_json(self, path: str) -> dict | list | None:  # type: ignore[return]
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)  # type: ignore[no-any-return]

    def _append_log(self, case_id: str, entry: dict) -> None:
        path = os.path.join(self._case_dir(case_id), "processing_log.json")
        raw_log = self._load_json(path)
        log: list = raw_log if isinstance(raw_log, list) else []
        log.append(entry)
        os.makedirs(self._case_dir(case_id), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(log, fh, indent=2)
