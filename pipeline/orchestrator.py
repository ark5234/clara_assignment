"""
Pipeline orchestrator â€” the main entry point for all Clara processing.

Responsibilities:
- Accept demo transcripts, onboarding transcripts, and onboarding forms
- Coordinate processors, generators, and version store
- Emit full artifact set per version: AgentConfig, AccountMemo, RetellSpec, prompt, changelog
- Support single-case and batch processing
- Enforce idempotency (skip re-processing when input hash matches)
- Log completed tasks to the task tracker

Usage (Python API):
    from pipeline.orchestrator import Orchestrator

    orch = Orchestrator()
    result = orch.run_demo(case_id="case_001", transcript="...")
    result = orch.run_onboarding(case_id="case_001", transcript="...")
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

from generators.prompt_generator import PromptGenerator
from generators.account_memo_generator import AccountMemoGenerator
from generators.retell_spec_generator import RetellSpecGenerator
from generators.changelog_generator import ChangelogGenerator
from processors.demo_processor import DemoProcessor
from processors.form_processor import FormProcessor
from processors.onboarding_processor import OnboardingProcessor
from schemas.agent_config import AgentConfig
from storage.version_store import VersionStore
from utils.llm_client import LLMClient
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineResult:
    case_id: str
    status: str          # "ok" | "skipped" | "error"
    version: str
    config_path: Optional[str] = None
    memo_path: Optional[str] = None
    spec_path: Optional[str] = None
    changelog_paths: Optional[tuple] = None
    agent_config: Optional[AgentConfig] = None
    error: Optional[str] = None
    notes: list = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        output_dir: str | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.llm = llm or LLMClient()
        self.store = VersionStore(output_dir=output_dir)
        self.prompt_gen = PromptGenerator()
        self.memo_gen = AccountMemoGenerator()
        self.spec_gen = RetellSpecGenerator()
        self.changelog_gen = ChangelogGenerator()

    # ------------------------------------------------------------------
    # Single case operations
    # ------------------------------------------------------------------
    def run_demo(self, case_id: str, transcript: str) -> PipelineResult:
        """
        Process a demo transcript â†’ produce v1 artifacts.
        Idempotent: returns cached v1 if same transcript was already processed.
        """
        processor = DemoProcessor(llm=self.llm)

        # Idempotency check
        input_hash = processor._hash_input(transcript)
        if self.store.exists(case_id, "v1"):
            existing = self.store.load(case_id, "v1")
            if existing and existing.input_hash == input_hash:
                logger.info(f"[{case_id}] v1 already up-to-date (same hash) â€” skipping")
                config_path = os.path.join(self.store._version_dir(case_id, "v1"), "agent_config.json")
                return PipelineResult(
                    case_id=case_id,
                    status="skipped",
                    version="v1",
                    config_path=config_path,
                    agent_config=existing,
                    notes=["v1 skipped â€” identical input hash"],
                )

        try:
            v1_config = processor.process(transcript, case_id)
            v1_config.agent_prompt = self.prompt_gen.generate(v1_config)
            config_path = self.store.save(v1_config)

            # Generate and save memo + spec
            memo = self.memo_gen.generate(v1_config)
            memo_path = self.store.save_memo(case_id, "v1", memo.model_dump_json(indent=2))

            spec = self.spec_gen.generate(v1_config, v1_config.agent_prompt or "")
            spec_path = self.store.save_retell_spec(case_id, "v1", spec.model_dump_json(indent=2))

            notes = []
            if v1_config.high_priority_unknowns():
                notes.append(
                    f"{len(v1_config.high_priority_unknowns())} high-priority unknowns â€” "
                    "onboarding call recommended"
                )

            self._track_task(case_id, "v1", "demo", config_path)

            return PipelineResult(
                case_id=case_id,
                status="ok",
                version="v1",
                config_path=config_path,
                memo_path=memo_path,
                spec_path=spec_path,
                agent_config=v1_config,
                notes=notes,
            )
        except Exception as exc:
            logger.error(f"[{case_id}] Demo processing failed: {exc}")
            return PipelineResult(
                case_id=case_id, status="error", version="v1", error=str(exc)
            )

    def run_onboarding(
        self, case_id: str, transcript: str, source: str = "onboarding_call"
    ) -> PipelineResult:
        """
        Process an onboarding transcript â†’ produce v2 artifacts + changelog.
        Requires v1 to already exist.
        """
        v1 = self.store.load(case_id, "v1")
        if v1 is None:
            msg = f"v1 config not found for '{case_id}'. Run demo processing first."
            logger.error(f"[{case_id}] {msg}")
            return PipelineResult(case_id=case_id, status="error", version="v2", error=msg)

        processor = OnboardingProcessor(llm=self.llm)
        input_hash = processor._hash_input(transcript)

        if self.store.exists(case_id, "v2"):
            existing = self.store.load(case_id, "v2")
            if existing and existing.input_hash == input_hash:
                logger.info(f"[{case_id}] v2 already up-to-date (same hash) â€” skipping")
                config_path = os.path.join(self.store._version_dir(case_id, "v2"), "agent_config.json")
                return PipelineResult(
                    case_id=case_id,
                    status="skipped",
                    version="v2",
                    config_path=config_path,
                    agent_config=existing,
                    notes=["v2 skipped â€” identical input hash"],
                )

        try:
            v2_config = processor.process(transcript, v1, source=source)
            v2_config.input_hash = input_hash
            v2_config.agent_prompt = self.prompt_gen.generate(v2_config)
            config_path = self.store.save(v2_config)

            # Generate and save memo + spec
            memo = self.memo_gen.generate(v2_config)
            memo_path = self.store.save_memo(case_id, "v2", memo.model_dump_json(indent=2))

            spec = self.spec_gen.generate(v2_config, v2_config.agent_prompt or "")
            spec_path = self.store.save_retell_spec(case_id, "v2", spec.model_dump_json(indent=2))

            # Generate changelog
            changes_dict, changes_md = self.changelog_gen.generate(v1, v2_config)
            changelog_paths = self.store.save_changelog(case_id, changes_dict, changes_md)

            notes = []
            if v2_config.open_unknowns():
                notes.append(
                    f"{len(v2_config.open_unknowns())} unknowns remain unresolved after onboarding"
                )
            conflicts = [e for e in v2_config.change_log if e.conflict_noted]
            if conflicts:
                notes.append(f"{len(conflicts)} conflict(s) detected â€” review changelog")

            self._track_task(case_id, "v2", source, config_path)

            return PipelineResult(
                case_id=case_id,
                status="ok",
                version="v2",
                config_path=config_path,
                memo_path=memo_path,
                spec_path=spec_path,
                changelog_paths=changelog_paths,
                agent_config=v2_config,
                notes=notes,
            )
        except Exception as exc:
            logger.error(f"[{case_id}] Onboarding processing failed: {exc}")
            return PipelineResult(
                case_id=case_id, status="error", version="v2", error=str(exc)
            )

    def run_form(self, case_id: str, form_data: dict | str) -> PipelineResult:
        """
        Process a structured onboarding form â†’ produce v2 artifacts + changelog.
        Requires v1 to already exist.
        """
        v1 = self.store.load(case_id, "v1")
        if v1 is None:
            msg = f"v1 config not found for '{case_id}'. Run demo processing first."
            logger.error(f"[{case_id}] {msg}")
            return PipelineResult(case_id=case_id, status="error", version="v2", error=msg)

        processor = FormProcessor(llm=self.llm)
        form_str = json.dumps(form_data, sort_keys=True) if isinstance(form_data, dict) else form_data
        input_hash = processor._hash_input(form_str)

        if self.store.exists(case_id, "v2"):
            existing = self.store.load(case_id, "v2")
            if existing and existing.input_hash == input_hash:
                logger.info(f"[{case_id}] v2 already up-to-date (same form hash) â€” skipping")
                config_path = os.path.join(self.store._version_dir(case_id, "v2"), "agent_config.json")
                return PipelineResult(
                    case_id=case_id,
                    status="skipped",
                    version="v2",
                    config_path=config_path,
                    agent_config=existing,
                    notes=["v2 skipped â€” identical form hash"],
                )

        try:
            v2_config = processor.process(form_data, v1)
            v2_config.input_hash = input_hash
            v2_config.agent_prompt = self.prompt_gen.generate(v2_config)
            config_path = self.store.save(v2_config)

            # Generate and save memo + spec
            memo = self.memo_gen.generate(v2_config)
            memo_path = self.store.save_memo(case_id, "v2", memo.model_dump_json(indent=2))

            spec = self.spec_gen.generate(v2_config, v2_config.agent_prompt or "")
            spec_path = self.store.save_retell_spec(case_id, "v2", spec.model_dump_json(indent=2))

            # Generate changelog
            changes_dict, changes_md = self.changelog_gen.generate(v1, v2_config)
            changelog_paths = self.store.save_changelog(case_id, changes_dict, changes_md)

            notes = []
            if v2_config.open_unknowns():
                notes.append(f"{len(v2_config.open_unknowns())} unknowns remain after form processing")

            self._track_task(case_id, "v2", "onboarding_form", config_path)

            return PipelineResult(
                case_id=case_id,
                status="ok",
                version="v2",
                config_path=config_path,
                memo_path=memo_path,
                spec_path=spec_path,
                changelog_paths=changelog_paths,
                agent_config=v2_config,
                notes=notes,
            )
        except Exception as exc:
            logger.error(f"[{case_id}] Form processing failed: {exc}")
            return PipelineResult(
                case_id=case_id, status="error", version="v2", error=str(exc)
            )

    # ------------------------------------------------------------------
    # Batch processing
    # ------------------------------------------------------------------
    def run_batch(self, cases_dir: str) -> List[PipelineResult]:
        """
        Process all cases found in cases_dir.

        Expected directory structure per case:
          cases_dir/
            <case_id>/
              demo_transcript.txt          â† required
              onboarding_transcript.txt    â† optional (mutually exclusive with form)
              onboarding_form.json         â† optional (mutually exclusive with transcript)
              metadata.json                â† optional, used for case_id override
        """
        if not os.path.isdir(cases_dir):
            logger.error(f"Cases directory not found: {cases_dir}")
            return []

        results: List[PipelineResult] = []
        case_dirs = sorted([
            d for d in os.listdir(cases_dir)
            if os.path.isdir(os.path.join(cases_dir, d))
        ])

        logger.info(f"Batch: found {len(case_dirs)} case(s) in '{cases_dir}'")

        for case_folder in case_dirs:
            case_path = os.path.join(cases_dir, case_folder)

            # Allow metadata.json to override the case_id
            metadata_path = os.path.join(case_path, "metadata.json")
            case_id = case_folder
            if os.path.exists(metadata_path):
                with open(metadata_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
                case_id = meta.get("case_id", case_folder)

            logger.info(f"--- Processing case: {case_id} ---")

            # Step 1: Demo transcript (required)
            demo_path = os.path.join(case_path, "demo_transcript.txt")
            if not os.path.exists(demo_path):
                logger.warning(f"[{case_id}] No demo_transcript.txt found â€” skipping")
                results.append(PipelineResult(
                    case_id=case_id,
                    status="error",
                    version="v1",
                    error="demo_transcript.txt not found",
                ))
                continue

            with open(demo_path, "r", encoding="utf-8") as fh:
                demo_text = fh.read()

            demo_result = self.run_demo(case_id=case_id, transcript=demo_text)
            results.append(demo_result)

            if demo_result.status == "error":
                continue

            # Step 2: Onboarding transcript OR form (optional)
            onboarding_transcript_path = os.path.join(case_path, "onboarding_transcript.txt")
            onboarding_form_path = os.path.join(case_path, "onboarding_form.json")

            if os.path.exists(onboarding_transcript_path):
                with open(onboarding_transcript_path, "r", encoding="utf-8") as fh:
                    onboarding_text = fh.read()
                ob_result = self.run_onboarding(case_id=case_id, transcript=onboarding_text)
                results.append(ob_result)

            elif os.path.exists(onboarding_form_path):
                with open(onboarding_form_path, "r", encoding="utf-8") as fh:
                    form_data = json.load(fh)
                ob_result = self.run_form(case_id=case_id, form_data=form_data)
                results.append(ob_result)

            else:
                logger.info(
                    f"[{case_id}] No onboarding data found â€” v1 only. "
                    "Add onboarding_transcript.txt or onboarding_form.json to generate v2."
                )

        return results

    # ------------------------------------------------------------------
    # Task tracker integration
    # ------------------------------------------------------------------
    def _track_task(
        self, case_id: str, version: str, source: str, config_path: str
    ) -> None:
        """Log a completed pipeline task to the task tracker (best-effort)."""
        try:
            from utils.task_tracker import TaskTracker
            tracker = TaskTracker()
            tracker.log_task(
                case_id=case_id,
                version=version,
                source=source,
                artifacts=[config_path],
            )
        except Exception as exc:
            # Task tracker is non-critical; never block pipeline on tracker failures
            logger.debug(f"[{case_id}] Task tracker log skipped: {exc}")
