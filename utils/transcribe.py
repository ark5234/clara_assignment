"""
transcribe.py — Transcribe a demo call audio file using Whisper (local, free).

Usage:
    python utils/transcribe.py --audio data/recordings/case_006/demo_call_audio.m4a --case-id case_006

Requirements:
    pip install openai-whisper
    (also requires ffmpeg on PATH: https://ffmpeg.org/download.html)

Models (speed vs accuracy trade-off):
    tiny    — fastest, least accurate
    base    — good balance for clear speech  ← default
    small   — better accuracy
    medium  — high accuracy, slow
    large   — best accuracy, very slow, requires ~10GB RAM

The transcript is written to: data/samples/<case_id>/demo_transcript.txt
Existing file is backed up as demo_transcript.txt.bak before overwrite.
"""
from __future__ import annotations

import os
import shutil
import sys

import click


@click.command()
@click.option(
    "--audio",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the audio file (.m4a, .mp3, .wav, .mp4, etc.)",
)
@click.option("--case-id", required=True, help="Case ID — transcript will be saved to data/samples/<case-id>/")
@click.option("--model", default="base", show_default=True,
              help="Whisper model: tiny / base / small / medium / large")
@click.option("--output-dir", default=None, help="Override output directory (default: data/samples/<case-id>/)")
def transcribe(audio: str, case_id: str, model: str, output_dir: str | None) -> None:
    """Transcribe an audio/video file to a Clara demo_transcript.txt using Whisper."""
    try:
        import whisper  # type: ignore
    except ImportError:
        click.echo("[ERROR] whisper not installed. Run: pip install openai-whisper", err=True)
        sys.exit(1)

    out_dir = output_dir or os.path.join("data", "samples", case_id)
    os.makedirs(out_dir, exist_ok=True)
    transcript_path = os.path.join(out_dir, "demo_transcript.txt")

    # Backup existing transcript
    if os.path.exists(transcript_path):
        backup = transcript_path + ".bak"
        shutil.copy2(transcript_path, backup)
        click.echo(f"Backed up existing transcript → {backup}")

    click.echo(f"Loading Whisper model '{model}' ...")
    wmodel = whisper.load_model(model)

    click.echo(f"Transcribing: {audio}")
    result = wmodel.transcribe(audio, verbose=False)

    transcript_text = result["text"].strip()

    with open(transcript_path, "w", encoding="utf-8") as fh:
        fh.write(f"DEMO CALL TRANSCRIPT — {case_id}\n")
        fh.write(f"Source audio: {audio}\n")
        fh.write(f"Transcribed with Whisper model: {model}\n")
        fh.write("-" * 60 + "\n\n")
        fh.write(transcript_text)
        fh.write("\n")

    click.echo(f"[OK] Transcript saved → {transcript_path}")
    click.echo(f"     ({len(transcript_text)} characters)")
    click.echo("\nNext step:")
    click.echo(f"  python main.py demo --case-id {case_id} --transcript {transcript_path}")


if __name__ == "__main__":
    transcribe()
