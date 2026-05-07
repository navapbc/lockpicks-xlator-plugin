# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest>=9.0.3"]
# ///
"""Tests for observe_hook.py duration helpers."""

import json
import os
import sys

import pytest

# DOMAINS_FULLPATH is read at module import; set a placeholder before importing.
os.environ.setdefault("DOMAINS_FULLPATH", "/tmp/xlator-test-placeholder")
sys.path.insert(0, os.path.dirname(__file__))

import observe_hook
from observe_hook import _compute_durations, _read_session_events, _seconds_between


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ev(ts: str, etype: str, session_id: str = "S1", **extra) -> dict:
    """Build a minimal event dict with the fields the helpers consume."""
    return {"ts": ts, "type": etype, "session_id": session_id, **extra}


def _write_jsonl(path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


# ---------------------------------------------------------------------------
# _seconds_between
# ---------------------------------------------------------------------------

def test_seconds_between_basic():
    assert _seconds_between("2026-05-07T12:00:00Z", "2026-05-07T12:00:12Z") == 12


def test_seconds_between_minutes():
    assert _seconds_between("2026-05-07T12:00:00Z", "2026-05-07T12:05:00Z") == 300


def test_seconds_between_negative_returns_none():
    assert _seconds_between("2026-05-07T12:00:12Z", "2026-05-07T12:00:00Z") is None


def test_seconds_between_invalid_returns_none():
    assert _seconds_between("not-a-timestamp", "2026-05-07T12:00:00Z") is None


def test_seconds_between_none_from_returns_none():
    assert _seconds_between(None, "2026-05-07T12:00:00Z") is None


# ---------------------------------------------------------------------------
# _compute_durations — happy paths
# ---------------------------------------------------------------------------

def test_single_turn_slash_skill():
    events = [_ev("2026-05-07T12:00:00Z", "user_prompt", prompt="/foo")]
    result = _compute_durations(events, "2026-05-07T12:00:12Z")
    assert result["turn"] == {
        "duration_seconds": 12,
        "from_ts": "2026-05-07T12:00:00Z",
        "to_ts": "2026-05-07T12:00:12Z",
    }
    assert result["skill"] == {
        "duration_seconds": 12,
        "from_ts": "2026-05-07T12:00:00Z",
        "to_ts": "2026-05-07T12:00:12Z",
        "wait_seconds": 0,
        "turns": 1,
    }


def test_free_form_prompt_emits_only_turn():
    events = [_ev("2026-05-07T12:00:00Z", "user_prompt", prompt="hello")]
    result = _compute_durations(events, "2026-05-07T12:00:12Z")
    assert "turn" in result
    assert "skill" not in result


def test_multi_turn_with_wait():
    events = [
        _ev("2026-05-07T12:00:00Z", "user_prompt", prompt="/foo"),
        _ev("2026-05-07T12:00:10Z", "assistant_response", response="..."),
        _ev("2026-05-07T12:00:50Z", "ai_question", question="?", answer="a"),
    ]
    result = _compute_durations(events, "2026-05-07T12:01:00Z")
    # Per-turn boundary is the latest of {assistant_response t=10, ai_question t=50, skill_start t=0}.
    # That's t=50, so turn duration = 60 - 50 = 10s.
    assert result["turn"]["duration_seconds"] == 10
    assert result["turn"]["from_ts"] == "2026-05-07T12:00:50Z"
    # Skill total = 60s wall-clock - 40s wait (t=50 - t=10) = 20s active.
    assert result["skill"]["duration_seconds"] == 20
    assert result["skill"]["wait_seconds"] == 40
    assert result["skill"]["turns"] == 2  # one prior assistant_response + this Stop


def test_within_turn_ai_question():
    events = [
        _ev("2026-05-07T12:00:00Z", "user_prompt", prompt="/foo"),
        _ev("2026-05-07T12:00:08Z", "ai_question", question="?", answer="a"),
    ]
    result = _compute_durations(events, "2026-05-07T12:00:12Z")
    # The ai_question is the latest boundary, so per-turn measures from it.
    assert result["turn"]["duration_seconds"] == 4
    assert result["turn"]["from_ts"] == "2026-05-07T12:00:08Z"
    # Skill total = 12s wall-clock - 8s wait = 4s active. 1 turn (the current Stop).
    assert result["skill"]["duration_seconds"] == 4
    assert result["skill"]["wait_seconds"] == 8
    assert result["skill"]["turns"] == 1


# ---------------------------------------------------------------------------
# _compute_durations — edge cases
# ---------------------------------------------------------------------------

def test_empty_session_returns_empty():
    assert _compute_durations([], "2026-05-07T12:00:12Z") == {}


def test_negative_delta_returns_empty():
    events = [_ev("2026-05-07T12:00:30Z", "user_prompt", prompt="/foo")]
    # now_ts is BEFORE the user_prompt — clock skew or reordered events.
    assert _compute_durations(events, "2026-05-07T12:00:00Z") == {}


def test_no_slash_skill_emits_only_turn():
    """Free-form prompts (no /-prefix anywhere in the session) skip skill_duration."""
    events = [
        _ev("2026-05-07T12:00:00Z", "user_prompt", prompt="hello"),
        _ev("2026-05-07T12:00:05Z", "assistant_response", response="hi"),
        _ev("2026-05-07T12:00:08Z", "user_prompt", prompt="more"),
    ]
    result = _compute_durations(events, "2026-05-07T12:00:12Z")
    assert "turn" in result
    assert "skill" not in result


def test_only_non_boundary_events_returns_empty():
    """If the session only has non-boundary events, no per-turn duration is computable."""
    events = [_ev("2026-05-07T12:00:00Z", "file_written", path="x")]
    assert _compute_durations(events, "2026-05-07T12:00:12Z") == {}


def test_skill_with_zero_wait_emits_active_equal_wall():
    events = [
        _ev("2026-05-07T12:00:00Z", "user_prompt", prompt="/foo"),
        _ev("2026-05-07T12:00:05Z", "assistant_response", response="..."),
    ]
    result = _compute_durations(events, "2026-05-07T12:00:12Z")
    assert result["skill"]["wait_seconds"] == 0
    assert result["skill"]["duration_seconds"] == 12  # 12s wall-clock, no wait
    assert result["skill"]["turns"] == 2


# ---------------------------------------------------------------------------
# _read_session_events — filesystem
# ---------------------------------------------------------------------------

def test_read_merges_per_domain_logs(monkeypatch, tmp_path):
    """Events under different domains all get discovered and merged by ts."""
    monkeypatch.setattr(observe_hook, "DOMAINS_FULLPATH", tmp_path)

    _write_jsonl(tmp_path / ".shared" / "logs" / "session.jsonl", [
        _ev("2026-05-07T12:00:10Z", "assistant_response", response="..."),
    ])
    _write_jsonl(tmp_path / "snap" / "logs" / "session.jsonl", [
        _ev("2026-05-07T12:00:00Z", "user_prompt", prompt="/foo"),
        _ev("2026-05-07T12:00:05Z", "ai_question", question="?", answer="a"),
    ])

    events = _read_session_events("S1")
    assert [(e["ts"], e["type"]) for e in events] == [
        ("2026-05-07T12:00:00Z", "user_prompt"),
        ("2026-05-07T12:00:05Z", "ai_question"),
        ("2026-05-07T12:00:10Z", "assistant_response"),
    ]


def test_read_filters_by_session_id(monkeypatch, tmp_path):
    monkeypatch.setattr(observe_hook, "DOMAINS_FULLPATH", tmp_path)
    _write_jsonl(tmp_path / ".shared" / "logs" / "session.jsonl", [
        _ev("2026-05-07T12:00:00Z", "user_prompt", session_id="S1", prompt="/foo"),
        _ev("2026-05-07T12:00:01Z", "user_prompt", session_id="S2", prompt="/bar"),
    ])

    events = _read_session_events("S1")
    assert len(events) == 1
    assert events[0]["session_id"] == "S1"


def test_read_skips_corrupt_lines(monkeypatch, tmp_path):
    monkeypatch.setattr(observe_hook, "DOMAINS_FULLPATH", tmp_path)

    log_path = tmp_path / ".shared" / "logs" / "session.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps(_ev("2026-05-07T12:00:00Z", "user_prompt", prompt="/foo")) + "\n"
        + "{not valid json\n"
        + json.dumps(_ev("2026-05-07T12:00:05Z", "assistant_response", response="ok")) + "\n",
        encoding="utf-8",
    )

    events = _read_session_events("S1")
    assert len(events) == 2
    assert events[0]["type"] == "user_prompt"
    assert events[1]["type"] == "assistant_response"


def test_read_handles_missing_directory(monkeypatch, tmp_path):
    """No log files under DOMAINS_FULLPATH yet → returns empty list, no exception, no mkdir."""
    monkeypatch.setattr(observe_hook, "DOMAINS_FULLPATH", tmp_path)

    events = _read_session_events("S1")
    assert events == []
    # Confirm we did not create any directories as a side-effect of the read.
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# handle_stop integration
# ---------------------------------------------------------------------------

def _read_shared_log(tmp_path) -> list[dict]:
    log_path = tmp_path / ".shared" / "logs" / "session.jsonl"
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _stop_payload(text: str) -> dict:
    """Build a minimal Stop hook payload with a single assistant text turn."""
    return {"transcript": [{"role": "assistant", "content": text}]}


def test_handle_stop_emits_assistant_response_turn_and_skill_durations(monkeypatch, tmp_path):
    """End-to-end: a /-prefixed user_prompt followed by handle_stop appends three
    .shared events (assistant_response, turn_duration, skill_duration)."""
    monkeypatch.setattr(observe_hook, "DOMAINS_FULLPATH", tmp_path)
    monkeypatch.setattr(observe_hook, "_get_session_id", lambda: "S1")
    # Seed a /-prefixed user_prompt event so handle_stop sees a skill in progress.
    _write_jsonl(tmp_path / "snap" / "logs" / "session.jsonl", [
        _ev("2026-05-07T12:00:00Z", "user_prompt", domain="snap", prompt="/foo"),
    ])
    # Force the Stop's "now" timestamp 12s after the user_prompt.
    monkeypatch.setattr(observe_hook, "_ts", lambda: "2026-05-07T12:00:12Z")

    observe_hook.handle_stop(_stop_payload("done"))

    events = _read_shared_log(tmp_path)
    types = [e["type"] for e in events]
    assert types == ["assistant_response", "turn_duration", "skill_duration"]
    assert events[1]["duration_seconds"] == 12
    assert events[2]["duration_seconds"] == 12
    assert events[2]["wait_seconds"] == 0
    assert events[2]["turns"] == 1


def test_handle_stop_emits_durations_when_last_response_empty(monkeypatch, tmp_path):
    """A Stop turn with no assistant text (e.g., turn ended on AskUserQuestion answer)
    still emits per-turn timing — the early-return guard was removed deliberately."""
    monkeypatch.setattr(observe_hook, "DOMAINS_FULLPATH", tmp_path)
    monkeypatch.setattr(observe_hook, "_get_session_id", lambda: "S1")
    _write_jsonl(tmp_path / "snap" / "logs" / "session.jsonl", [
        _ev("2026-05-07T12:00:00Z", "user_prompt", domain="snap", prompt="/foo"),
    ])
    monkeypatch.setattr(observe_hook, "_ts", lambda: "2026-05-07T12:00:08Z")

    # Empty transcript → no assistant_response logged, but turn/skill_duration still emit.
    observe_hook.handle_stop({"transcript": []})

    events = _read_shared_log(tmp_path)
    types = [e["type"] for e in events]
    assert types == ["turn_duration", "skill_duration"]


def test_handle_stop_swallows_exceptions_from_compute(monkeypatch, tmp_path):
    """If _compute_durations raises, handle_stop returns normally without propagating
    and without emitting partial duration events."""
    monkeypatch.setattr(observe_hook, "DOMAINS_FULLPATH", tmp_path)
    monkeypatch.setattr(observe_hook, "_get_session_id", lambda: "S1")
    monkeypatch.setattr(observe_hook, "_ts", lambda: "2026-05-07T12:00:12Z")

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(observe_hook, "_compute_durations", _boom)

    # Should not raise.
    observe_hook.handle_stop(_stop_payload("done"))

    events = _read_shared_log(tmp_path)
    # The assistant_response still landed; duration events were skipped.
    types = [e["type"] for e in events]
    assert types == ["assistant_response"]
