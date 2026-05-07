#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# ///
"""
Xlator observability hook handler.

# TODO later: Replace with Monitors? https://code.claude.com/docs/en/plugins-reference#monitors

Called from Claude Code hooks defined in .claude-plugin/hooks/hooks.json.
Reads the hook event payload from stdin (JSON) and appends a JSONL entry
to <domain>/logs/session.jsonl.

Usage (from hooks.json):
  xlator observe_hook <EventType>

Supported event types:
  SessionStart       — create session ID temp file
  SessionEnd         — delete session ID temp file
  UserPromptSubmit   — log user message
  Stop               — log Claude's final text response for the turn
  PostToolUse        — log Bash/Write/Edit/AskUserQuestion tool use (dispatched by tool_name)

Errors are silently swallowed and the script always exits 0 so that
observability failures never block the user's workflow.
"""
import os
import sys

if os.environ.get("OBSERVE_HOOK_DISABLED", "").lower() in ["1", "true", "yes"]:
    # Short-circuit the entire script if the env var is set, to minimize overhead when disabled.
    sys.exit(0)

import fcntl
import glob as _glob
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent  # tools/ -> plugin root
_SESSION_GLOB = "/tmp/xlator-session-*"
_SESSION_PREFIX = "/tmp/xlator-session-"

DOMAINS_FULLPATH = Path(os.environ["DOMAINS_FULLPATH"])


# ---------------------------------------------------------------------------
# Session ID management
# ---------------------------------------------------------------------------

def _create_session_id() -> str:
    sid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = Path(f"{_SESSION_PREFIX}{sid}")
    path.write_text(sid)
    return sid


def _get_session_id() -> str:
    """Return the current session ID, creating one if needed.

    Finds /tmp/xlator-session-* files, takes the newest (alphabetical = chronological),
    deletes stale extras. Creates a new one if none exist.
    """
    matches = sorted(_glob.glob(_SESSION_GLOB))
    if not matches:
        return _create_session_id()
    # Keep newest, delete stale ones
    for stale in matches[:-1]:
        try:
            Path(stale).unlink()
        except OSError:
            pass
    return Path(matches[-1]).read_text().strip()


def _delete_session_id() -> None:
    for path in _glob.glob(_SESSION_GLOB):
        try:
            Path(path).unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Domain inference
# ---------------------------------------------------------------------------

_VALID_DOMAIN = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_known_domain(name: str) -> bool:
    """Return True only if the domain directory actually exists under DOMAINS_FULLPATH."""
    return name == ".shared" or (DOMAINS_FULLPATH / name).is_dir()


def _infer_domain(text: str) -> str:
    """Extract domain name from a file path or shell command string.

    Primary: match against the resolved DOMAINS_FULLPATH path in the text.
    Secondary: match against the DOMAINS_FULLPATH basename (handles relative paths in text).
    Fallback for xlator skills: positional arg 2 is the domain.
    Default: .shared

    Candidates are rejected if they contain non-identifier characters or do not
    correspond to an existing domain directory, preventing stray folder creation.
    """
    domains_str = str(DOMAINS_FULLPATH)
    if domains_str in text:
        remainder = text[text.index(domains_str) + len(domains_str):].lstrip("/")
        m = re.match(r"([^/\s]+)/", remainder)
        if m and _VALID_DOMAIN.match(m.group(1)) and _is_known_domain(m.group(1)):
            return m.group(1)

    # basename fallback — handles relative paths like rules/snap/...
    basename = re.escape(DOMAINS_FULLPATH.name)
    m = re.search(rf"{basename}/([^/\s]+)/", text)
    if m and _VALID_DOMAIN.match(m.group(1)) and _is_known_domain(m.group(1)):
        return m.group(1)

    # xlator <action> <domain> [<module>] — matches any xlator invocation
    m2 = re.match(r"(?:.*\bxlator(?:\.py)?)\s+\S+\s+(\S+)", text)
    if m2 and _VALID_DOMAIN.match(m2.group(1)) and _is_known_domain(m2.group(1)):
        return m2.group(1)

    # xl:<action> <domain> [<module>] — matches any xlator invocation
    m2 = re.match(r"(?:.*\bxl:(?:\.py)?)\S+\s+(\S+)", text)
    if m2 and _VALID_DOMAIN.match(m2.group(1)) and _is_known_domain(m2.group(1)):
        return m2.group(1)

    return ".shared"


# ---------------------------------------------------------------------------
# Log file
# ---------------------------------------------------------------------------

def _log_file(domain: str) -> Path:
    log_dir = DOMAINS_FULLPATH / domain / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "session.jsonl"


def _append_event(domain: str, event: dict) -> None:
    """Append a JSONL entry to the domain's session log, with an advisory lock."""
    log_path = _log_file(domain)
    with open(log_path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another hook invocation holds the lock; drop this event rather than block.
            return
        try:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Duration computation (used by handle_stop)
# ---------------------------------------------------------------------------

# Event types that mark the start of a new active-AI segment for per-turn timing.
_TURN_BOUNDARY_TYPES = ("user_prompt", "assistant_response", "ai_question")


def _read_session_events(session_id: str) -> list[dict]:
    """Read all events for the given session_id across every per-domain session.jsonl
    plus .shared, merged and sorted by ts.

    Skips missing files and malformed lines silently. Constructs paths directly to
    avoid _log_file()'s mkdir side-effect.
    """
    events: list[dict] = []
    for log_path in DOMAINS_FULLPATH.glob("*/logs/session.jsonl"):
        try:
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("session_id") == session_id:
                        events.append(event)
        except OSError:
            continue
    events.sort(key=lambda e: e.get("ts", ""))
    return events


def _seconds_between(from_ts: str | None, to_ts: str) -> int | None:
    """Whole-second difference (to_ts − from_ts). Returns None on invalid input or
    negative delta. All session.jsonl timestamps are second-resolution
    (isoformat(timespec='seconds')) so integer seconds is the natural precision."""
    if from_ts is None:
        return None
    try:
        dt_from = datetime.fromisoformat(from_ts.replace("Z", "+00:00"))
        dt_to = datetime.fromisoformat(to_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    delta = int((dt_to - dt_from).total_seconds())
    if delta < 0:
        return None
    return delta


def _compute_durations(events: list[dict], now_ts: str) -> dict:
    """Pure function. Given the chronologically-sorted events for the current
    session and the current Stop's timestamp, returns a dict with:
      - "turn": always present when computable, with from_ts, to_ts, duration_seconds
      - "skill": present when a /-prefixed user_prompt exists in the session, with
                 from_ts, to_ts, duration_seconds (active-AI), wait_seconds, turns

    Returns {} if no per-turn duration can be computed. The current Stop's
    assistant_response event is NOT in `events` yet (handle_stop appends it).
    """
    # Per-turn: from the latest boundary event (user_prompt | assistant_response | ai_question).
    turn_from_ts: str | None = None
    for event in reversed(events):
        if event.get("type") in _TURN_BOUNDARY_TYPES:
            turn_from_ts = event.get("ts")
            break
    if turn_from_ts is None:
        return {}

    turn_seconds = _seconds_between(turn_from_ts, now_ts)
    if turn_seconds is None:
        return {}

    result: dict = {
        "turn": {
            "duration_seconds": turn_seconds,
            "from_ts": turn_from_ts,
            "to_ts": now_ts,
        }
    }

    # Per-skill: find the most recent /-prefixed user_prompt in the session.
    skill_start_event: dict | None = None
    for event in reversed(events):
        if event.get("type") == "user_prompt" and event.get("prompt", "").startswith("/"):
            skill_start_event = event
            break
    if skill_start_event is None:
        return result

    skill_start_ts = skill_start_event.get("ts")
    if skill_start_ts is None:
        return result

    # Walk events between skill_start and now: count assistant turns; sum AskUserQuestion waits.
    # Each ai_question wait = (ai_question.ts − latest_preceding_assistant_response.ts), or
    # (ai_question.ts − skill_start.ts) if no prior assistant_response exists.
    last_active_ts = skill_start_ts
    wait_total = 0
    turn_count = 0
    for event in events:
        ts = event.get("ts")
        if ts is None or ts <= skill_start_ts or ts > now_ts:
            continue
        etype = event.get("type")
        if etype == "ai_question":
            wait = _seconds_between(last_active_ts, ts)
            if wait is not None:
                wait_total += wait
            last_active_ts = ts
        elif etype == "assistant_response":
            turn_count += 1
            last_active_ts = ts

    # Current Stop is the final turn of this skill_duration computation.
    turn_count += 1

    wall_seconds = _seconds_between(skill_start_ts, now_ts)
    if wall_seconds is None:
        return result
    active_seconds = wall_seconds - wait_total
    if active_seconds < 0:
        return result

    result["skill"] = {
        "duration_seconds": active_seconds,
        "from_ts": skill_start_ts,
        "to_ts": now_ts,
        "wait_seconds": wait_total,
        "turns": turn_count,
    }
    return result


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def handle_session_start(_payload: dict) -> None:
    _create_session_id()


def handle_session_end(_payload: dict) -> None:
    _delete_session_id()


def handle_user_prompt_submit(payload: dict) -> None:
    prompt = payload.get("prompt", "")
    if not prompt:
        return
    domain = _infer_domain(prompt)
    session_id = _get_session_id()
    _append_event(domain, {
        "ts": _ts(),
        "session_id": session_id,
        "type": "user_prompt",
        "domain": domain,
        "prompt": prompt,
    })


def handle_stop(payload: dict) -> None:
    """Log the assistant's last text response (when present) and append per-turn
    and per-skill duration events derived from session.jsonl."""
    transcript = payload.get("transcript", [])
    last_response = ""
    for turn in reversed(transcript):
        if turn.get("role") != "assistant":
            continue
        content = turn.get("content", "")
        if isinstance(content, str):
            last_response = content.strip()
        elif isinstance(content, list):
            texts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            last_response = " ".join(t for t in texts if t).strip()
        if last_response:
            break

    session_id = _get_session_id()
    now_ts = _ts()

    # Read session events BEFORE appending this turn's assistant_response so the
    # per-turn boundary uses the *previous* turn-end, not the one we're about to write.
    # _read_session_events is itself defensive (returns [] on any I/O or parse failure),
    # and main()'s top-level except still backstops anything below.
    prior_events = _read_session_events(session_id)

    if last_response:
        _append_event(".shared", {
            "ts": now_ts,
            "session_id": session_id,
            "type": "assistant_response",
            "domain": ".shared",
            "response": last_response,
        })

    # Compute durations. The except guard is intentional: _compute_durations is the new
    # logic that could throw on malformed events; the per-_append_event calls below are
    # already covered by main()'s top-level handler.
    try:
        durations = _compute_durations(prior_events, now_ts)
    except Exception:
        durations = {}

    if "turn" in durations:
        _append_event(".shared", {
            "ts": now_ts,
            "session_id": session_id,
            "type": "turn_duration",
            "domain": ".shared",
            **durations["turn"],
        })

    if "skill" in durations:
        _append_event(".shared", {
            "ts": now_ts,
            "session_id": session_id,
            "type": "skill_duration",
            "domain": ".shared",
            **durations["skill"],
        })


def handle_post_tool_use(payload: dict) -> None:
    tool_name = payload.get("tool_name", "")
    session_id = _get_session_id()
    ts = _ts()

    if tool_name == "Bash":
        cmd = (payload.get("tool_input") or {}).get("command", "")
        if not cmd:
            return
        # Only log xlator invocations (./xlator, /path/to/xlator, python3 xlator.py)
        if not re.search(r"\bxlator(?:\.py)?\b", cmd):
            return
        domain = _infer_domain(cmd)
        resp = payload.get("tool_response") or {}
        _append_event(domain, {
            "ts": ts,
            "session_id": session_id,
            "type": "cli_command",
            "domain": domain,
            "cmd": cmd,
            "exit_code": resp.get("exit_code", resp.get("exitCode")),
            "stdout": resp.get("stdout", ""),
            "stderr": resp.get("stderr", ""),
        })

    elif tool_name == "Write":
        file_path = (payload.get("tool_input") or {}).get("file_path", "")
        if not file_path:
            return
        content = (payload.get("tool_input") or {}).get("content", "")
        domain = _infer_domain(file_path)
        _append_event(domain, {
            "ts": ts,
            "session_id": session_id,
            "type": "file_written",
            "domain": domain,
            "path": file_path,
            "bytes": len(content.encode("utf-8")),
        })

    elif tool_name == "Edit":
        file_path = (payload.get("tool_input") or {}).get("file_path", "")
        if not file_path:
            return
        old_string = (payload.get("tool_input") or {}).get("old_string", "")
        new_string = (payload.get("tool_input") or {}).get("new_string", "")
        domain = _infer_domain(file_path)
        import difflib
        diff = "".join(difflib.unified_diff(
            old_string.splitlines(keepends=True),
            new_string.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
            n=2,
        ))
        _append_event(domain, {
            "ts": ts,
            "session_id": session_id,
            "type": "file_edited",
            "domain": domain,
            "path": file_path,
            "diff": diff,
        })

    elif tool_name == "AskUserQuestion":
        question = (payload.get("tool_input") or {}).get("question", "")
        if not question:
            return
        answer = (payload.get("tool_response") or {}).get("answer", "")
        domain = _infer_domain(question)
        _append_event(domain, {
            "ts": ts,
            "session_id": session_id,
            "type": "ai_question",
            "domain": domain,
            "question": question,
            "answer": answer,
        })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(20)

    event_type = sys.argv[1]

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        payload = {}

    try:
        match event_type:
            case "SessionStart":
                handle_session_start(payload)
            case "SessionEnd":
                handle_session_end(payload)
            case "UserPromptSubmit":
                handle_user_prompt_submit(payload)
            case "Stop":
                handle_stop(payload)
            case "PostToolUse":
                handle_post_tool_use(payload)
    except Exception:
        pass  # observability must never block the workflow

    sys.exit(0)


if __name__ == "__main__":
    main()
