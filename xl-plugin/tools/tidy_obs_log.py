#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["rich>=10.0"]
# ///
"""
tidy-log: render session log as a human-readable Markdown conversation.

Reads $DOMAINS_FULLPATH/<domain>/logs/session.jsonl (domain events) and
$DOMAINS_FULLPATH/.shared/logs/session.jsonl (all events, filtered to matching
session IDs), merges them, and writes a Markdown conversation view to
$DOMAINS_FULLPATH/<domain>/logs/session-report.md.

Usage (via xlator CLI):
  xlator tidy-log <domain>
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

DOMAINS_FULLPATH = Path(os.environ["DOMAINS_FULLPATH"])


from rich.console import Console
_console = Console()
_err_console = Console(stderr=True)


def _read_jsonl(path: Path) -> list[tuple[int, dict]]:
    """Read a JSONL file, returning (line_number, event) pairs. Skips malformed lines."""
    results = []
    try:
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    results.append((i, json.loads(line)))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return results


def _format_duration(seconds: int) -> str:
    """Format whole seconds as `{s}s` for sub-minute, `{m}m {s}s` otherwise."""
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    return f"{m}m {s}s"


def _render_turn(
    turn_number: int,
    turn_ts: str,
    user_events: list,
    tool_events: list,
    assistant_response: str,
    turn_duration: dict | None = None,
    skill_duration: dict | None = None,
) -> str:
    lines = []
    header = f"### Turn {turn_number} — {turn_ts}"
    if turn_duration is not None:
        header += f" (⏱ {_format_duration(turn_duration.get('duration_seconds', 0))})"
    lines.append(header)
    lines.append("")

    # User block
    lines.append("**👤 User**")
    lines.append("")
    for event in user_events:
        lines.append(event.get("prompt", "").strip())
        lines.append("")

    # Tools block (only if there are tool events)
    if tool_events:
        lines.append("**🔧 Tools**")
        lines.append("")
        for event in tool_events:
            etype = event.get("type")
            if etype == "file_written":
                path = event.get("path", "")
                nbytes = event.get("bytes", 0)
                lines.append(f"📝 Wrote `{path}` ({nbytes:,} bytes)")
                lines.append("")
            elif etype == "file_edited":
                path = event.get("path", "")
                diff = event.get("diff", "").rstrip()
                lines.append(f"✏️ Edited `{path}`")
                if diff:
                    lines.append("```diff")
                    lines.append(diff)
                    lines.append("```")
                lines.append("")
            elif etype == "cli_command":
                cmd = event.get("cmd", "")
                exit_code = event.get("exit_code")
                exit_str = f"exit {exit_code}" if exit_code is not None else "exit ?"
                lines.append(f"⚡ `{cmd}` → {exit_str}")
                stdout = (event.get("stdout") or "").strip()
                stderr = (event.get("stderr") or "").strip()
                if stdout:
                    lines.append("```")
                    lines.append(stdout)
                    lines.append("```")
                if stderr:
                    lines.append("```")
                    lines.append(stderr)
                    lines.append("```")
                lines.append("")
            elif etype == "ai_question":
                question = event.get("question", "").strip()
                answer = event.get("answer", "").strip()
                lines.append(f"❓ {question}")
                if answer:
                    lines.append(f"> {answer}")
                lines.append("")

    # Claude block
    lines.append("**🤖 Claude**")
    lines.append("")
    if assistant_response:
        lines.append(assistant_response.strip())
    else:
        lines.append("*(no response recorded)*")
    lines.append("")

    if skill_duration is not None:
        active = _format_duration(skill_duration.get("duration_seconds", 0))
        turns = skill_duration.get("turns", 0)
        turns_label = "turn" if turns == 1 else "turns"
        wait = skill_duration.get("wait_seconds", 0)
        if wait > 0:
            wait_str = _format_duration(wait)
            lines.append(f"⏱ Skill total: {active} ({turns} {turns_label}, {wait_str} waiting)")
        else:
            lines.append(f"⏱ Skill total: {active} ({turns} {turns_label})")
        lines.append("")

    return "\n".join(lines)


def run(domain: str) -> None:
    domain_log = DOMAINS_FULLPATH / domain / "logs" / "session.jsonl"
    if not domain_log.exists():
        _err_console.print(f"[red]ERR[/red] No session log found: {domain_log.relative_to(DOMAINS_FULLPATH)}")
        sys.exit(1)

    domain_events = _read_jsonl(domain_log)
    if not domain_events:
        _console.print("No events found.")
        sys.exit(0)

    # Collect session IDs from domain log
    session_ids: set[str] = set()
    for _, event in domain_events:
        sid = event.get("session_id")
        if sid:
            session_ids.add(sid)

    # Read global log (all event types, filtered to matching session IDs).
    # Skip when domain IS .shared to avoid reading the same file twice.
    global_log = DOMAINS_FULLPATH / ".shared" / "logs" / "session.jsonl"
    global_events: list[tuple[int, dict]] = []
    if domain != ".shared" and global_log.exists():
        for lineno, event in _read_jsonl(global_log):
            if event.get("session_id") in session_ids:
                global_events.append((lineno, event))

    # Merge: domain events get their line numbers; global events offset to avoid collisions
    # We'll tag each with source so sort is stable; use a large offset for global line numbers
    # so that when ts is equal, domain events sort before global events (matching log write order
    # isn't guaranteed, but line_number tiebreaker gives determinism)
    merged: list[tuple[str, str, int, dict]] = []  # (ts, source_tag, line_number, event)
    for lineno, event in domain_events:
        merged.append((event.get("ts", ""), "domain", lineno, event))
    for lineno, event in global_events:
        merged.append((event.get("ts", ""), "global", lineno, event))

    # Sort by (ts, line_number) — line_number is tiebreaker
    merged.sort(key=lambda x: (x[0], x[2]))

    # Group into sessions
    sessions: dict[str, list[dict]] = defaultdict(list)
    session_order: list[str] = []
    for ts, _src, _lineno, event in merged:
        sid = event.get("session_id", "_unknown")
        if sid not in sessions:
            session_order.append(sid)
        sessions[sid].append(event)

    # Render
    from datetime import datetime, timezone
    generated_ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    output_lines = []
    output_lines.append(f"# Session Log: {domain}")
    output_lines.append("")
    output_lines.append(f"Generated: {generated_ts}")
    output_lines.append("")
    output_lines.append("---")
    output_lines.append("")

    for sid in session_order:
        output_lines.append(f"## Session {sid}")
        output_lines.append("")

        events = sessions[sid]

        # Pre-pass: identify which skill_duration events to render. For each /-prefixed
        # skill range (a contiguous run starting at a /-prefixed user_prompt), keep only
        # the LAST skill_duration event before the next /-prefixed user_prompt or end-of-session.
        keep_skill_duration_ts: set[str] = set()
        in_slash_skill = False
        latest_skill_duration_in_range: dict | None = None
        for event in events:
            etype = event.get("type")
            if etype == "user_prompt":
                if latest_skill_duration_in_range is not None:
                    keep_skill_duration_ts.add(latest_skill_duration_in_range.get("to_ts", ""))
                    latest_skill_duration_in_range = None
                in_slash_skill = event.get("prompt", "").startswith("/")
            elif etype == "skill_duration" and in_slash_skill:
                latest_skill_duration_in_range = event
        if latest_skill_duration_in_range is not None:
            keep_skill_duration_ts.add(latest_skill_duration_in_range.get("to_ts", ""))

        # Group into turns: each user_prompt starts a new turn
        # A "turn" = [user_prompt events, tool events, last assistant_response, optional duration events]
        turns: list[dict] = []  # each: {ts, user, tools, responses, turn_duration, skill_duration}
        current_turn: dict | None = None

        def _new_turn(ts: str, user_events: list) -> dict:
            return {
                "ts": ts,
                "user": user_events,
                "tools": [],
                "responses": [],
                "turn_duration": None,
                "skill_duration": None,
            }

        for event in events:
            etype = event.get("type")
            if etype == "user_prompt":
                if current_turn is not None:
                    turns.append(current_turn)
                current_turn = _new_turn(event.get("ts", ""), [event])
            elif etype == "assistant_response":
                if current_turn is None:
                    current_turn = _new_turn(event.get("ts", ""), [])
                current_turn["responses"].append(event.get("response", ""))
            elif etype in ("file_written", "file_edited", "cli_command", "ai_question"):
                if current_turn is None:
                    current_turn = _new_turn(event.get("ts", ""), [])
                current_turn["tools"].append(event)
            elif etype == "turn_duration":
                if current_turn is None:
                    current_turn = _new_turn(event.get("ts", ""), [])
                current_turn["turn_duration"] = event
            elif etype == "skill_duration":
                if current_turn is None:
                    current_turn = _new_turn(event.get("ts", ""), [])
                if event.get("to_ts", "") in keep_skill_duration_ts:
                    current_turn["skill_duration"] = event

        if current_turn is not None:
            turns.append(current_turn)

        for i, turn in enumerate(turns, 1):
            last_response = turn["responses"][-1] if turn["responses"] else ""
            output_lines.append(_render_turn(
                i,
                turn["ts"],
                turn["user"],
                turn["tools"],
                last_response,
                turn_duration=turn["turn_duration"],
                skill_duration=turn["skill_duration"],
            ))

    output_path = DOMAINS_FULLPATH / domain / "logs" / "session-report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(output_lines))
    _console.print(f"[green]✓[/green] Written: {output_path.relative_to(DOMAINS_FULLPATH)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        _err_console.print("Usage: xlator tidy-log <domain>")
        sys.exit(1)
    run(sys.argv[1])
