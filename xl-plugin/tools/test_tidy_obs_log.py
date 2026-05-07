# /// script
# requires-python = ">=3.14"
# dependencies = ["pytest>=9.0.3", "rich>=10.0"]
# ///
"""Tests for tidy_obs_log.py rendering of duration events."""

import json
import os
import sys

import pytest

# DOMAINS_FULLPATH is read at module import; set a placeholder before importing.
os.environ.setdefault("DOMAINS_FULLPATH", "/tmp/xlator-test-placeholder")
sys.path.insert(0, os.path.dirname(__file__))

import tidy_obs_log
from tidy_obs_log import _format_duration, _render_turn


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------

def test_format_duration_seconds():
    assert _format_duration(0) == "0s"
    assert _format_duration(12) == "12s"
    assert _format_duration(59) == "59s"


def test_format_duration_minutes():
    assert _format_duration(60) == "1m 0s"
    assert _format_duration(75) == "1m 15s"
    assert _format_duration(240) == "4m 0s"


# ---------------------------------------------------------------------------
# _render_turn — duration decoration
# ---------------------------------------------------------------------------

def test_render_turn_no_durations_unchanged():
    """A turn rendered without duration events looks the same as pre-feature output."""
    output = _render_turn(1, "2026-05-07T12:00:00Z", [], [], "hi")
    assert "### Turn 1 — 2026-05-07T12:00:00Z" in output
    assert "⏱" not in output


def test_render_turn_header_includes_duration():
    turn_dur = {
        "duration_seconds": 12,
        "from_ts": "2026-05-07T12:00:00Z",
        "to_ts": "2026-05-07T12:00:12Z",
    }
    output = _render_turn(1, "2026-05-07T12:00:00Z", [], [], "hi", turn_duration=turn_dur)
    assert "### Turn 1 — 2026-05-07T12:00:00Z (⏱ 12s)" in output


def test_render_turn_skill_total_no_wait():
    skill_dur = {
        "duration_seconds": 12,
        "from_ts": "2026-05-07T12:00:00Z",
        "to_ts": "2026-05-07T12:00:12Z",
        "wait_seconds": 0,
        "turns": 1,
    }
    output = _render_turn(1, "2026-05-07T12:00:00Z", [], [], "hi", skill_duration=skill_dur)
    assert "⏱ Skill total: 12s (1 turn)" in output


def test_render_turn_skill_total_plural_turns():
    skill_dur = {
        "duration_seconds": 240,
        "from_ts": "2026-05-07T12:00:00Z",
        "to_ts": "2026-05-07T12:04:00Z",
        "wait_seconds": 0,
        "turns": 3,
    }
    output = _render_turn(1, "2026-05-07T12:00:00Z", [], [], "hi", skill_duration=skill_dur)
    assert "⏱ Skill total: 4m 0s (3 turns)" in output


def test_render_turn_skill_total_with_wait():
    skill_dur = {
        "duration_seconds": 240,
        "from_ts": "2026-05-07T12:00:00Z",
        "to_ts": "2026-05-07T12:05:00Z",
        "wait_seconds": 60,
        "turns": 3,
    }
    output = _render_turn(1, "2026-05-07T12:00:00Z", [], [], "hi", skill_duration=skill_dur)
    assert "⏱ Skill total: 4m 0s (3 turns, 1m 0s waiting)" in output


# ---------------------------------------------------------------------------
# Integration: full run() pipeline filters skill_duration to last-per-skill
# ---------------------------------------------------------------------------

def _setup_session_log(domain_path, events):
    log_path = domain_path / "logs" / "session.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


def _run_and_read(monkeypatch, tmp_path, domain: str) -> str:
    monkeypatch.setattr(tidy_obs_log, "DOMAINS_FULLPATH", tmp_path)
    tidy_obs_log.run(domain)
    return (tmp_path / domain / "logs" / "session-report.md").read_text(encoding="utf-8")


def test_run_renders_turn_and_skill_durations(monkeypatch, tmp_path):
    events = [
        {"ts": "2026-05-07T12:00:00Z", "session_id": "S1", "type": "user_prompt", "domain": "snap", "prompt": "/foo"},
        {"ts": "2026-05-07T12:00:12Z", "session_id": "S1", "type": "assistant_response", "domain": ".shared", "response": "done"},
        {"ts": "2026-05-07T12:00:12Z", "session_id": "S1", "type": "turn_duration", "domain": ".shared",
         "duration_seconds": 12, "from_ts": "2026-05-07T12:00:00Z", "to_ts": "2026-05-07T12:00:12Z"},
        {"ts": "2026-05-07T12:00:12Z", "session_id": "S1", "type": "skill_duration", "domain": ".shared",
         "duration_seconds": 12, "from_ts": "2026-05-07T12:00:00Z", "to_ts": "2026-05-07T12:00:12Z",
         "wait_seconds": 0, "turns": 1},
    ]
    _setup_session_log(tmp_path / "snap", events)

    report = _run_and_read(monkeypatch, tmp_path, "snap")
    assert "### Turn 1 — 2026-05-07T12:00:00Z (⏱ 12s)" in report
    assert "⏱ Skill total: 12s (1 turn)" in report


def test_run_keeps_only_last_skill_duration_per_skill(monkeypatch, tmp_path):
    """When a /-skill spans multiple Stops (each emitting a skill_duration), only
    the LAST one renders. Earlier interim totals are filtered out."""
    events = [
        {"ts": "2026-05-07T12:00:00Z", "session_id": "S1", "type": "user_prompt", "domain": "snap", "prompt": "/foo"},
        # Turn 1 of /foo: emits an interim skill_duration that should NOT render.
        {"ts": "2026-05-07T12:00:10Z", "session_id": "S1", "type": "assistant_response", "domain": ".shared", "response": "thinking"},
        {"ts": "2026-05-07T12:00:10Z", "session_id": "S1", "type": "turn_duration", "domain": ".shared",
         "duration_seconds": 10, "from_ts": "2026-05-07T12:00:00Z", "to_ts": "2026-05-07T12:00:10Z"},
        {"ts": "2026-05-07T12:00:10Z", "session_id": "S1", "type": "skill_duration", "domain": ".shared",
         "duration_seconds": 10, "from_ts": "2026-05-07T12:00:00Z", "to_ts": "2026-05-07T12:00:10Z",
         "wait_seconds": 0, "turns": 1},
        # Turn 2 of /foo (still no second user_prompt — multi-step skill): emits the FINAL skill_duration.
        {"ts": "2026-05-07T12:00:20Z", "session_id": "S1", "type": "assistant_response", "domain": ".shared", "response": "done"},
        {"ts": "2026-05-07T12:00:20Z", "session_id": "S1", "type": "turn_duration", "domain": ".shared",
         "duration_seconds": 10, "from_ts": "2026-05-07T12:00:10Z", "to_ts": "2026-05-07T12:00:20Z"},
        {"ts": "2026-05-07T12:00:20Z", "session_id": "S1", "type": "skill_duration", "domain": ".shared",
         "duration_seconds": 20, "from_ts": "2026-05-07T12:00:00Z", "to_ts": "2026-05-07T12:00:20Z",
         "wait_seconds": 0, "turns": 2},
    ]
    _setup_session_log(tmp_path / "snap", events)

    report = _run_and_read(monkeypatch, tmp_path, "snap")
    # The final skill total renders.
    assert "⏱ Skill total: 20s (2 turns)" in report
    # The interim total (10s, 1 turn) does NOT render.
    assert "Skill total: 10s" not in report


def test_run_no_durations_unchanged(monkeypatch, tmp_path):
    """A session.jsonl without any duration events renders identically to pre-feature output."""
    events = [
        {"ts": "2026-05-07T12:00:00Z", "session_id": "S1", "type": "user_prompt", "domain": "snap", "prompt": "/foo"},
        {"ts": "2026-05-07T12:00:12Z", "session_id": "S1", "type": "assistant_response", "domain": ".shared", "response": "done"},
    ]
    _setup_session_log(tmp_path / "snap", events)

    report = _run_and_read(monkeypatch, tmp_path, "snap")
    assert "### Turn 1 — 2026-05-07T12:00:00Z" in report
    assert "(⏱" not in report
    assert "Skill total" not in report
