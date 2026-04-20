#!/usr/bin/env python3
"""
Xlator observability hook handler.

# TODO: Replace with Monitors? https://code.claude.com/docs/en/plugins-reference#monitors

Called from Claude Code hooks defined in .claude-plugin/hooks/hooks.json.
Reads the hook event payload from stdin (JSON) and appends a JSONL entry
to $DOMAINS_DIR/<domain>/logs/session.jsonl (resolved from xlator.conf in
the git project root).

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


# ---------------------------------------------------------------------------
# Config loading (xlator.conf in git project root)
# ---------------------------------------------------------------------------

def _load_conf() -> Path:
    """Locate xlator.conf via git project root and parse it.

    Returns (project_root, conf_dict). Silent on all failures.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        project_root = Path(result.stdout.strip()) if result.returncode == 0 else Path.cwd()
    except Exception:
        project_root = Path.cwd()

    return project_root


def _resolve_domains_dir(project_root: Path) -> Path:
    raw = os.environ.get("DOMAINS_DIR", "")
    if raw:
        p = Path(raw)
        return (project_root / p).resolve() if not p.is_absolute() else p.resolve()
    sys.exit(0)  # xlator.conf absent or DOMAINS_DIR not set — skip logging silently


_PROJECT_ROOT = _load_conf()
_DOMAINS_DIR = _resolve_domains_dir(_PROJECT_ROOT)


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
    """Return True only if the domain directory actually exists under DOMAINS_DIR."""
    return name == ".shared" or (_PROJECT_ROOT / _DOMAINS_DIR / name).is_dir()


def _infer_domain(text: str) -> str:
    """Extract domain name from a file path or shell command string.

    Primary: match against the resolved DOMAINS_DIR path in the text.
    Secondary: match against the DOMAINS_DIR basename (handles relative paths in text).
    Fallback for xlator commands: positional arg 2 is the domain.
    Default: .shared

    Candidates are rejected if they contain non-identifier characters or do not
    correspond to an existing domain directory, preventing stray folder creation.
    """
    domains_str = str(_DOMAINS_DIR)
    if domains_str in text:
        remainder = text[text.index(domains_str) + len(domains_str):].lstrip("/")
        m = re.match(r"([^/\s]+)/", remainder)
        if m and _VALID_DOMAIN.match(m.group(1)) and _is_known_domain(m.group(1)):
            return m.group(1)

    # basename fallback — handles relative paths like rules/snap/...
    basename = re.escape(_DOMAINS_DIR.name)
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
    log_dir = _PROJECT_ROOT / _DOMAINS_DIR / domain / "logs"
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
    """Extract the last assistant text turn from the transcript and log it."""
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

    if not last_response:
        return

    session_id = _get_session_id()
    _append_event(".shared", {
        "ts": _ts(),
        "session_id": session_id,
        "type": "assistant_response",
        "domain": ".shared",
        "response": last_response,
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
