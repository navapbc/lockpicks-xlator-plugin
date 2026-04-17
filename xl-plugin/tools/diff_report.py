#!/usr/bin/env python3
"""
diff-report: show AI-vs-user diffs from the observability session log.

For each file logged in $DOMAINS_DIR/<domain>/logs/session.jsonl (file_written and
file_edited events), finds the most recent commit tagged with
'Co-Authored-By: Claude' and diffs it against the user's next committed change.

Read-only — makes no git commits or file modifications.

Usage (via xlator CLI):
  ./xlator diff-report <domain>
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("PROJECT_ROOT", Path(__file__).parent.parent))

assert "DOMAINS_DIR" in os.environ, "DOMAINS_DIR must be set to the domains directory"
DOMAINS_DIR = Path(os.environ.get("DOMAINS_DIR", "domains"))

from rich.console import Console
_console = Console()
_err_console = Console(stderr=True)


def run(domain: str) -> None:
    log_path = ROOT / DOMAINS_DIR / domain / "logs" / "session.jsonl"
    if not log_path.exists():
        _err_console.print(f"[red]ERR[/red] No session log found: {log_path.relative_to(ROOT)}")
        _console.print("Run some slash commands in this domain first to generate log entries.")
        sys.exit(1)

    # Collect unique file paths from file_written and file_edited events
    file_paths: list[str] = []
    seen: set[str] = set()
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") in ("file_written", "file_edited"):
                p = event.get("path", "")
                if p and p not in seen:
                    seen.add(p)
                    file_paths.append(p)

    if not file_paths:
        _console.print("No file_written or file_edited events found in session log.")
        sys.exit(0)

    found_any = False
    for file_path in file_paths:
        # Find most recent AI-generated commit for this file
        ai_result = subprocess.run(
            ["git", "log", "--format=%H", "--grep=Co-Authored-By: Claude", "--", file_path],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        ai_shas = [s.strip() for s in ai_result.stdout.splitlines() if s.strip()]
        if not ai_shas:
            _console.print(f"[dim]No AI commit found for {file_path} — skipping[/dim]")
            continue
        ai_sha = ai_shas[0]  # most recent

        # Find the next user commit after the AI commit
        user_result = subprocess.run(
            ["git", "log", "--format=%H", "--ancestry-path", f"{ai_sha}..HEAD", "--", file_path],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        user_shas = [s.strip() for s in user_result.stdout.splitlines() if s.strip()]

        if user_shas:
            user_sha = user_shas[-1]  # earliest commit after ai_sha
            diff_result = subprocess.run(
                ["git", "diff", ai_sha, user_sha, "--", file_path],
                capture_output=True, text=True, cwd=str(ROOT),
            )
        else:
            # No committed user changes — fall back to working-tree diff vs AI commit
            diff_result = subprocess.run(
                ["git", "diff", ai_sha, "--", file_path],
                capture_output=True, text=True, cwd=str(ROOT),
            )

        diff_text = diff_result.stdout.strip()
        if not diff_text:
            _console.print(f"[dim]{file_path}: no user changes detected[/dim]")
            continue

        found_any = True
        _console.print(f"\n[bold]{file_path}[/bold] [dim](AI: {ai_sha[:8]})[/dim]")
        _console.print(diff_text)

    if not found_any:
        _console.print("No user changes found relative to AI-generated commits.")
