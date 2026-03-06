"""
Diff and Changelog generator — compares v1 and v2 AgentConfigs and produces:

  1. changes.json  — machine-readable diff (field-by-field)
  2. changes.md    — human-readable changelog

Uses deepdiff when available; falls back to a manual dict comparison.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from schemas.agent_config import AgentConfig
from utils.logger import get_logger

logger = get_logger(__name__)


class ChangelogGenerator:
    def generate(
        self, v1: AgentConfig, v2: AgentConfig
    ) -> tuple[dict, str]:
        """
        Returns (changes_dict, changes_markdown).
        """
        logger.debug(f"[{v1.config_id}] Generating changelog v1→v2")

        changes = self._compute_changes(v1, v2)
        markdown = self._render_markdown(changes, v1, v2)
        return changes, markdown

    # ------------------------------------------------------------------
    def _compute_changes(self, v1: AgentConfig, v2: AgentConfig) -> dict:
        """Build structured diff from the change_log entries in v2."""
        v2_entries = [e for e in v2.change_log if e.version_from == "v1"]
        resolved = [u for u in v2.questions_or_unknowns if u.resolved]
        remaining = [u for u in v2.questions_or_unknowns if not u.resolved]
        conflicts = [e for e in v2_entries if e.conflict_noted]

        field_changes = []
        for entry in v2_entries:
            if entry.field_path == "*":
                continue
            field_changes.append({
                "field": entry.field_path,
                "old_value": entry.old_value,
                "new_value": entry.new_value,
                "source": entry.source,
                "reason": entry.reason,
                "conflict": entry.conflict_noted,
            })

        # Also try deepdiff for a richer flat diff
        flat_diff = self._flat_diff(v1, v2)

        return {
            "account_id": v1.config_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "fields_changed": len(field_changes),
                "unknowns_resolved": len(resolved),
                "unknowns_remaining": len(remaining),
                "conflicts_detected": len(conflicts),
            },
            "field_changes": field_changes,
            "flat_diff": flat_diff,
            "conflicts": [
                {
                    "field": e.field_path,
                    "v1_value": e.old_value,
                    "v2_value": e.new_value,
                    "reason": e.reason,
                }
                for e in conflicts
            ],
            "resolved_unknowns": [
                {"field": u.field, "question": u.question}
                for u in resolved
            ],
            "remaining_unknowns": [
                {"field": u.field, "question": u.question, "priority": u.priority}
                for u in remaining
            ],
        }

    def _flat_diff(self, v1: AgentConfig, v2: AgentConfig) -> list[dict]:
        """
        Produce a flat list of changed leaf values using deepdiff if available,
        otherwise fall back to a manual JSON comparison.
        """
        try:
            from deepdiff import DeepDiff  # type: ignore

            # Exclude noisy audit fields from the diff
            exclude = {
                "root['version']",
                "root['updated_at']",
                "root['source']",
                "root['change_log']",
                "root['agent_prompt']",
                "root['raw_extraction']",
                "root['questions_or_unknowns']",
            }
            dd = DeepDiff(
                v1.model_dump(),
                v2.model_dump(),
                ignore_order=True,
                exclude_paths=exclude,
            )
            result = []
            for change_type, changes in dd.items():
                if isinstance(changes, dict):
                    for path, detail in changes.items():
                        entry: dict[str, Any] = {"path": str(path), "change_type": change_type}
                        if hasattr(detail, "t1"):
                            entry["old"] = detail.t1
                            entry["new"] = detail.t2
                        else:
                            entry["detail"] = str(detail)
                        result.append(entry)
            return result
        except ImportError:
            # Manual fallback: compare JSON dicts level-by-level
            return self._manual_flat_diff(v1.model_dump(), v2.model_dump())

    def _manual_flat_diff(
        self, d1: dict, d2: dict, path: str = "root"
    ) -> list[dict]:
        results = []
        all_keys = set(d1.keys()) | set(d2.keys())
        skip = {"version", "updated_at", "source", "change_log",
                "agent_prompt", "raw_extraction", "questions_or_unknowns"}
        for key in all_keys:
            if key in skip:
                continue
            full_path = f"{path}.{key}"
            v1_val = d1.get(key)
            v2_val = d2.get(key)
            if v1_val == v2_val:
                continue
            if isinstance(v1_val, dict) and isinstance(v2_val, dict):
                results.extend(self._manual_flat_diff(v1_val, v2_val, full_path))
            else:
                v1_s = json.dumps(v1_val)[:120]
                v2_s = json.dumps(v2_val)[:120]
                if v1_s != v2_s:
                    results.append({
                        "path": full_path,
                        "change_type": "values_changed",
                        "old": v1_val,
                        "new": v2_val,
                    })
        return results

    # ------------------------------------------------------------------
    def _render_markdown(
        self, changes: dict, v1: AgentConfig, v2: AgentConfig
    ) -> str:
        s = changes["summary"]
        now = changes["generated_at"][:10]
        company = v2.client.name or v2.config_id

        lines = [
            f"# Changelog — {company}",
            f"**Account ID:** `{v1.config_id}`  |  **Date:** {now}  "
            "|  **Transition:** v1 → v2",
            "",
            "## Summary",
            "| Metric | Value |",
            "|---|---|",
            f"| Fields changed | {s['fields_changed']} |",
            f"| Unknowns resolved | {s['unknowns_resolved']} |",
            f"| Unknowns remaining | {s['unknowns_remaining']} |",
            f"| Conflicts detected | {s['conflicts_detected']} |",
            "",
        ]

        # Field changes
        if changes["field_changes"]:
            lines += ["## Field Changes", ""]
            for fc in changes["field_changes"]:
                conflict_tag = " ⚠️ **CONFLICT**" if fc["conflict"] else ""
                old_s = _truncate(fc["old_value"])
                new_s = _truncate(fc["new_value"])
                reason = fc.get("reason") or "Confirmed in onboarding"
                lines += [
                    f"### `{fc['field']}`{conflict_tag}",
                    f"- **Source:** {fc['source']}",
                    f"- **Reason:** {reason}",
                    f"- **Before:** `{old_s}`",
                    f"- **After:** `{new_s}`",
                    "",
                ]

        # Conflicts
        if changes["conflicts"]:
            lines += ["## ⚠️ Conflicts", ""]
            for c in changes["conflicts"]:
                lines += [
                    f"- **Field:** `{c['field']}`",
                    f"  - v1 assumed: `{_truncate(c['v1_value'])}`",
                    f"  - v2 confirmed: `{_truncate(c['v2_value'])}`",
                    f"  - Reason: {c.get('reason', 'Overridden in onboarding')}",
                    "",
                ]

        # Resolved unknowns
        if changes["resolved_unknowns"]:
            lines += ["## ✅ Resolved Unknowns", ""]
            for u in changes["resolved_unknowns"]:
                lines.append(f"- `{u['field']}`: {u['question']}")
            lines.append("")

        # Remaining unknowns
        if changes["remaining_unknowns"]:
            lines += ["## ❓ Remaining Unknowns", ""]
            for u in changes["remaining_unknowns"]:
                pri = u.get("priority", "medium")
                prefix = "🔴" if pri == "high" else "🟡"
                lines.append(f"- {prefix} `{u['field']}`: {u['question']}")
            lines.append("")

        return "\n".join(lines)


def _truncate(val: Any, max_len: int = 80) -> str:
    s = json.dumps(val, default=str) if not isinstance(val, str) else val
    return s[:max_len] + "…" if len(s) > max_len else s
