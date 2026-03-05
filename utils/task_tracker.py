"""
Task tracker — logs completed pipeline runs to a local JSON file
and optionally creates GitHub Issues for unresolved unknowns.

Backends:
  "local"   — appends to outputs/task_tracker.json  (default, zero setup)
  "github"  — additionally creates a GitHub Issue via the REST API
               Requires GITHUB_TOKEN and GITHUB_REPO in .env

Usage:
    tracker = TaskTracker()
    tracker.log_task(case_id="case_001", version="v2", source="onboarding_call",
                     artifacts=["outputs/accounts/case_001/v2/agent_config.json"])
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import config
from utils.logger import get_logger

logger = get_logger(__name__)


class TaskTracker:
    def __init__(
        self,
        backend: str | None = None,
        task_file: str | None = None,
    ) -> None:
        self.backend = backend or getattr(config, "TASK_TRACKER_BACKEND", "local")
        self.task_file = task_file or getattr(config, "TASK_TRACKER_FILE", "outputs/task_tracker.json")

    def log_task(
        self,
        case_id: str,
        version: str,
        source: str,
        artifacts: list[str] | None = None,
        notes: list[str] | None = None,
        unknowns: list[dict] | None = None,
    ) -> str:
        """
        Record a completed pipeline run.  Returns the task ID.
        """
        task_id = f"{case_id}__{version}__{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        entry = {
            "task_id": task_id,
            "case_id": case_id,
            "version": version,
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "artifacts": artifacts or [],
            "notes": notes or [],
            "unresolved_unknowns": unknowns or [],
        }

        self._save_local(entry)

        if self.backend == "github":
            self._create_github_issue(entry)

        return task_id

    # ------------------------------------------------------------------
    def _save_local(self, entry: dict) -> None:
        """Append the entry to the local JSON task tracker file."""
        os.makedirs(os.path.dirname(os.path.abspath(self.task_file)), exist_ok=True)

        tasks: list = []
        if os.path.exists(self.task_file):
            try:
                with open(self.task_file, "r", encoding="utf-8") as fh:
                    tasks = json.load(fh)
            except (json.JSONDecodeError, OSError):
                tasks = []

        tasks.append(entry)
        with open(self.task_file, "w", encoding="utf-8") as fh:
            json.dump(tasks, fh, indent=2)

        logger.info(f"[TaskTracker] Logged task {entry['task_id']} → {self.task_file}")

    def _create_github_issue(self, entry: dict) -> None:
        """
        Create a GitHub Issue for unresolved unknowns (if any).
        Only runs when GITHUB_TOKEN and GITHUB_REPO are set.
        """
        token = getattr(config, "GITHUB_TOKEN", None)
        repo = getattr(config, "GITHUB_REPO", None)

        if not token or not repo:
            logger.debug("[TaskTracker] GitHub backend skipped — GITHUB_TOKEN or GITHUB_REPO not set")
            return

        if not entry.get("unresolved_unknowns"):
            logger.debug(f"[TaskTracker] No unresolved unknowns for {entry['case_id']} — skipping GitHub issue")
            return

        try:
            import urllib.request
            import urllib.error

            title = f"[Clara] Unresolved unknowns: {entry['case_id']} {entry['version']}"
            unknowns_md = "\n".join(
                f"- `{u.get('field', '?')}`: {u.get('question', '')}"
                for u in entry["unresolved_unknowns"]
            )
            body = (
                f"## Clara AI Pipeline — Unresolved Unknowns\n\n"
                f"**Case ID:** `{entry['case_id']}`  \n"
                f"**Version:** `{entry['version']}`  \n"
                f"**Source:** `{entry['source']}`  \n"
                f"**Timestamp:** {entry['timestamp']}\n\n"
                f"### Questions requiring follow-up\n\n{unknowns_md}\n\n"
                f"*Auto-created by Clara AI task tracker*"
            )

            payload = json.dumps({"title": title, "body": body, "labels": ["clara-ai", "needs-review"]})
            url = f"https://api.github.com/repos/{repo}/issues"
            req = urllib.request.Request(
                url,
                data=payload.encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.load(resp)
                issue_url = result.get("html_url", "")
                logger.info(f"[TaskTracker] GitHub issue created: {issue_url}")

        except Exception as exc:
            # Non-critical — never block pipeline on GitHub failures
            logger.warning(f"[TaskTracker] GitHub issue creation failed: {exc}")

    # ------------------------------------------------------------------
    def list_tasks(self) -> list[dict]:
        """Return all logged tasks from the local file."""
        if not os.path.exists(self.task_file):
            return []
        try:
            with open(self.task_file, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return []
