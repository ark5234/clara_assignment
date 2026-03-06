"""Simple CLI to show changelog diffs for an account.

Usage: python scripts/diff_viewer.py <case_id>
"""
import json
import sys
from pathlib import Path

def main(case_id: str):
    base = Path("outputs") / "accounts" / case_id / "changelog"
    if not base.exists():
        print("No changelog found for", case_id)
        return
    changes_json = base / "changes.json"
    if not changes_json.exists():
        print("No changes.json under", base)
        return
    data = json.loads(changes_json.read_text(encoding="utf-8"))
    print(f"Changelog for {case_id}:\n")
    print(json.dumps(data, indent=2))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/diff_viewer.py <case_id>")
        sys.exit(1)
    main(sys.argv[1])
