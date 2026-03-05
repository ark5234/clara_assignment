"""
Clara AI — CLI entry point

Commands
--------
  demo        Process a demo transcript → v1 config
  onboard     Process an onboarding transcript → v2 config
  form        Process a structured onboarding form (JSON) → v2 config
  batch       Process all cases in a directory
  status      Show stored versions and unknowns for a case
  inspect     Pretty-print the change log for a case
  diff        Show a Rich-formatted v1→v2 diff table for a case

Usage examples
--------------
  python main.py demo    --case-id case_001 --transcript data/samples/case_001/demo_transcript.txt
  python main.py onboard --case-id case_001 --transcript data/samples/case_001/onboarding_transcript.txt
  python main.py form    --case-id case_002 --form      data/samples/case_002/onboarding_form.json
  python main.py batch   --cases-dir data/samples/
  python main.py status  --case-id case_001
  python main.py inspect --case-id case_001 --version v2
  python main.py diff    --case-id case_001
"""
import json
import sys

import click
from rich.console import Console
from rich.table import Table
from rich import box

from pipeline.orchestrator import Orchestrator, PipelineResult

console = Console()


def _print_result(result: PipelineResult) -> None:
    icon = {"ok": "[green]✓[/green]", "skipped": "[yellow]→[/yellow]", "error": "[red]✗[/red]"}.get(
        result.status, "?"
    )
    console.print(f"{icon}  [{result.status.upper()}] {result.case_id} / {result.version}")
    if result.config_path:
        console.print(f"   Path: {result.config_path}")
    if result.notes:
        for note in result.notes:
            console.print(f"   [dim]ℹ {note}[/dim]")
    if result.error:
        console.print(f"   [red]Error: {result.error}[/red]")


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------
@click.group()
def cli() -> None:
    """Clara AI — voice agent configuration pipeline."""


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------
@cli.command()
@click.option("--case-id", required=True, help="Unique identifier for this client case.")
@click.option(
    "--transcript",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the demo call transcript (.txt).",
)
@click.option("--output-dir", default=None, help="Override default output directory.")
def demo(case_id: str, transcript: str, output_dir: str | None) -> None:
    """Process a demo transcript → generate v1 agent config."""
    with open(transcript, "r", encoding="utf-8") as fh:
        text = fh.read()

    orch = Orchestrator(output_dir=output_dir)
    result = orch.run_demo(case_id=case_id, transcript=text)
    _print_result(result)

    if result.status == "ok" and result.agent_config:
        _print_unknowns_summary(result.agent_config)

    sys.exit(0 if result.status in ("ok", "skipped") else 1)


# ---------------------------------------------------------------------------
# onboard
# ---------------------------------------------------------------------------
@cli.command()
@click.option("--case-id", required=True, help="Case ID matching an existing v1 config.")
@click.option(
    "--transcript",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the onboarding call transcript (.txt).",
)
@click.option("--output-dir", default=None, help="Override default output directory.")
def onboard(case_id: str, transcript: str, output_dir: str | None) -> None:
    """Process an onboarding transcript → update to v2 agent config."""
    with open(transcript, "r", encoding="utf-8") as fh:
        text = fh.read()

    orch = Orchestrator(output_dir=output_dir)
    result = orch.run_onboarding(case_id=case_id, transcript=text)
    _print_result(result)

    if result.status == "ok" and result.agent_config:
        _print_changes_summary(result.agent_config)
        _print_unknowns_summary(result.agent_config)

    sys.exit(0 if result.status in ("ok", "skipped") else 1)


# ---------------------------------------------------------------------------
# form
# ---------------------------------------------------------------------------
@cli.command()
@click.option("--case-id", required=True, help="Case ID matching an existing v1 config.")
@click.option(
    "--form",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the onboarding form (.json).",
)
@click.option("--output-dir", default=None, help="Override default output directory.")
def form(case_id: str, form: str, output_dir: str | None) -> None:
    """Process a structured onboarding form → update to v2 agent config."""
    with open(form, "r", encoding="utf-8") as fh:
        form_data = json.load(fh)

    orch = Orchestrator(output_dir=output_dir)
    result = orch.run_form(case_id=case_id, form_data=form_data)
    _print_result(result)

    if result.status == "ok" and result.agent_config:
        _print_changes_summary(result.agent_config)
        _print_unknowns_summary(result.agent_config)

    sys.exit(0 if result.status in ("ok", "skipped") else 1)


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------
@cli.command()
@click.option(
    "--cases-dir",
    default="data/samples",
    show_default=True,
    help="Directory containing case subdirectories.",
)
@click.option("--output-dir", default=None, help="Override default output directory.")
def batch(cases_dir: str, output_dir: str | None) -> None:
    """Batch-process all cases in a directory."""
    orch = Orchestrator(output_dir=output_dir)
    results = orch.run_batch(cases_dir=cases_dir)

    console.rule("[bold]Batch Summary")
    ok = sum(1 for r in results if r.status == "ok")
    skipped = sum(1 for r in results if r.status == "skipped")
    errors = sum(1 for r in results if r.status == "error")

    for result in results:
        _print_result(result)

    console.print(f"\n[bold]Total:[/bold] {len(results)} runs — "
                  f"[green]{ok} ok[/green], "
                  f"[yellow]{skipped} skipped[/yellow], "
                  f"[red]{errors} errors[/red]")
    sys.exit(0 if errors == 0 else 1)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
@cli.command()
@click.option("--case-id", required=True, help="Case ID to inspect.")
@click.option("--output-dir", default=None)
def status(case_id: str, output_dir: str | None) -> None:
    """Show version status and open unknowns for a case."""
    from storage.version_store import VersionStore
    store = VersionStore(output_dir=output_dir)

    console.rule(f"[bold]Case: {case_id}")

    for ver in ("v1", "v2"):
        if store.exists(case_id, ver):
            cfg = store.load(case_id, ver)
            if cfg:
                console.print(f"[green]{ver}[/green] — source: {cfg.source}, updated: {cfg.updated_at[:19]}")
                _print_unknowns_summary(cfg)
        else:
            console.print(f"[dim]{ver} — not found[/dim]")


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------
@cli.command()
@click.option("--case-id", required=True)
@click.option("--version", "version", default="v2", show_default=True)
@click.option("--output-dir", default=None)
def inspect(case_id: str, version: str, output_dir: str | None) -> None:
    """Pretty-print the change log for a specific version of a case."""
    from storage.version_store import VersionStore
    store = VersionStore(output_dir=output_dir)

    cfg = store.load(case_id, version)
    if cfg is None:
        console.print(f"[red]Config {version} not found for case '{case_id}'[/red]")
        sys.exit(1)

    console.rule(f"[bold]Change Log — {case_id} / {version}")

    if not cfg.change_log:
        console.print("[dim]No change log entries.[/dim]")
        return

    table = Table(box=box.ROUNDED, show_lines=True)
    table.add_column("Timestamp", style="dim", width=22)
    table.add_column("Field", style="cyan")
    table.add_column("From → To", max_width=60)
    table.add_column("Source")
    table.add_column("Conflict?")

    for entry in cfg.change_log:
        conflict_style = "[red]YES[/red]" if entry.conflict_noted else "—"
        old_str = str(entry.old_value)[:40] if entry.old_value is not None else "∅"
        new_str = str(entry.new_value)[:40] if entry.new_value is not None else "∅"
        table.add_row(
            entry.timestamp[:19],
            entry.field_path,
            f"{old_str} → {new_str}",
            entry.source,
            conflict_style,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------
@cli.command()
@click.option("--case-id", required=True, help="Case ID with both v1 and v2 saved.")
@click.option("--output-dir", default=None)
@click.option("--show-raw", is_flag=True, default=False, help="Also print raw flat diff JSON.")
def diff(case_id: str, output_dir: str | None, show_raw: bool) -> None:
    """Show a formatted v1→v2 diff for a case. Reads pre-generated changelog if available."""
    from storage.version_store import VersionStore
    from generators.changelog_generator import ChangelogGenerator

    store = VersionStore(output_dir=output_dir)

    # Prefer pre-generated changelog
    changes = store.get_changelog(case_id)
    if changes is None:
        # Generate on the fly if both configs exist
        v1 = store.load(case_id, "v1")
        v2 = store.load(case_id, "v2")
        if v1 is None or v2 is None:
            console.print(f"[red]Both v1 and v2 must exist for case '{case_id}'[/red]")
            sys.exit(1)
        cgen = ChangelogGenerator()
        changes, changes_md = cgen.generate(v1, v2)
        # Save for next time
        store.save_changelog(case_id, changes, changes_md)

    console.rule(f"[bold]v1 → v2 Diff — {case_id}")

    # Summary box
    s = changes.get("summary", {})
    summary_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    summary_table.add_column(style="bold cyan")
    summary_table.add_column()
    summary_table.add_row("Fields changed", str(s.get("fields_changed", 0)))
    summary_table.add_row("Unknowns resolved", str(s.get("unknowns_resolved", 0)))
    summary_table.add_row("Unknowns remaining", str(s.get("unknowns_remaining", 0)))
    summary_table.add_row("Conflicts", f"[red]{s.get('conflicts_detected', 0)}[/red]" if s.get('conflicts_detected') else "0")
    console.print(summary_table)

    # Field changes
    fc = changes.get("field_changes", [])
    if fc:
        console.rule("[bold]Field Changes")
        tbl = Table(box=box.ROUNDED, show_lines=True)
        tbl.add_column("Field", style="cyan", min_width=20)
        tbl.add_column("Before", max_width=36)
        tbl.add_column("After", max_width=36)
        tbl.add_column("Source", style="dim", min_width=14)
        tbl.add_column("Conflict")
        for f in fc:
            conflict_tag = "[red]YES[/red]" if f.get("conflict") else "—"
            old_s = str(f.get("old_value", "∅"))[:36]
            new_s = str(f.get("new_value", "∅"))[:36]
            tbl.add_row(f["field"], old_s, new_s, f.get("source", ""), conflict_tag)
        console.print(tbl)

    # Conflicts detail
    conflicts = changes.get("conflicts", [])
    if conflicts:
        console.rule("[bold red]Conflicts")
        for c in conflicts:
            console.print(f"  Field: [cyan]{c['field']}[/cyan]")
            console.print(f"    v1: {c.get('v1_value')}")
            console.print(f"    v2: {c.get('v2_value')}")
            console.print(f"    Reason: [italic]{c.get('reason', 'Overridden in onboarding')}[/italic]")

    # Remaining unknowns
    remaining = changes.get("remaining_unknowns", [])
    if remaining:
        console.rule("[bold yellow]Remaining Unknowns")
        for u in remaining:
            pri = u.get("priority", "medium")
            icon = "[red]●[/red]" if pri == "high" else "[yellow]●[/yellow]"
            console.print(f"  {icon} [{u['field']}] {u['question']}")

    if show_raw:
        console.rule("[dim]Flat Diff (raw)")
        console.print_json(json.dumps(changes.get("flat_diff", [])))


# ---------------------------------------------------------------------------
# Shared print helpers
# ---------------------------------------------------------------------------
def _print_unknowns_summary(config) -> None:
    open_unknowns = config.open_unknowns()
    if not open_unknowns:
        console.print("   [green]No open unknowns[/green]")
        return

    high = [u for u in open_unknowns if u.priority == "high"]
    if high:
        console.print(f"   [red]⚠ {len(high)} HIGH-priority unknown(s):[/red]")
        for u in high:
            console.print(f"     • [{u.field}] {u.question}")
    other = [u for u in open_unknowns if u.priority != "high"]
    if other:
        console.print(f"   [yellow]{len(other)} other unknown(s) — run `status` for details[/yellow]")


def _print_changes_summary(config) -> None:
    changes = [e for e in config.change_log if e.version_from == "v1"]
    if changes:
        console.print(f"   [cyan]{len(changes)} field(s) updated from v1 → v2[/cyan]")
    conflicts = [e for e in changes if e.conflict_noted]
    if conflicts:
        console.print(f"   [red]⚠ {len(conflicts)} conflict(s) detected — run `inspect` to review[/red]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    cli()
