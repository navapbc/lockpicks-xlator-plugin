# /// script
# requires-python = ">=3.14"
# ///
"""Tests for parse_fences.py — covers all plan scenarios (AE1, AE2, AE4)."""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from parse_fences import extract_text, parse_fences


def test_single_important_block():
    """Happy path: one :::important block."""
    text = ":::important\nPrimary result.\n:::"
    result = parse_fences(text)
    assert result == [{"type": "important", "content": "Primary result."}]


def test_multiple_sequential_blocks():
    """AE1: sequential fence blocks, each closed before the next opens."""
    text = (
        ":::important\nResult A.\n:::\n"
        ":::error\nSomething failed.\n:::\n"
        ":::next_step\nRun /foo next.\n:::"
    )
    result = parse_fences(text)
    assert len(result) == 3
    assert result[0] == {"type": "important", "content": "Result A."}
    assert result[1] == {"type": "error", "content": "Something failed."}
    assert result[2] == {"type": "next_step", "content": "Run /foo next."}


def test_unfenced_text_becomes_detail():
    """AE2: unfenced prose routes to detail."""
    text = "Just some plain text.\nMore text."
    result = parse_fences(text)
    assert result == [{"type": "detail", "content": "Just some plain text.\nMore text."}]


def test_no_fences_at_all():
    """AE2 edge: entire input with no fences → single detail block."""
    text = "Line one.\nLine two.\nLine three."
    result = parse_fences(text)
    assert len(result) == 1
    assert result[0]["type"] == "detail"
    assert "Line one." in result[0]["content"]


def test_mixed_fenced_and_unfenced():
    """AE4: mixed fenced + unfenced content."""
    text = "preamble line\n:::important\nResult here.\n:::\ntrailing text"
    result = parse_fences(text)
    assert result[0] == {"type": "detail", "content": "preamble line"}
    assert result[1] == {"type": "important", "content": "Result here."}
    assert result[2] == {"type": "detail", "content": "trailing text"}


def test_empty_fence_block():
    """Edge: empty fence block (open then immediate close) → empty content dropped."""
    text = ":::detail\n:::"
    result = parse_fences(text)
    assert result == []


def test_unknown_fence_type():
    """Edge: unknown type parsed as-is, no error raised."""
    text = ":::unknown\nSome content.\n:::"
    result = parse_fences(text)
    assert result == [{"type": "unknown", "content": "Some content."}]


def test_trailing_content_after_last_close():
    """Edge: trailing unfenced content after last close → detail block."""
    text = ":::important\nResult.\n:::\nThis is a trailing line."
    result = parse_fences(text)
    assert result[-1] == {"type": "detail", "content": "This is a trailing line."}


def test_unclosed_fence_yields_partial_block():
    """Error path: unclosed fence → partial block yielded with declared type."""
    text = ":::progress\nScanning…"
    result = parse_fences(text)
    assert result == [{"type": "progress", "content": "Scanning…"}]


def test_extract_text_plain():
    """extract_text returns plain text unchanged."""
    text = ":::important\nResult.\n:::"
    assert extract_text(text) == text


def test_extract_text_single_json():
    """extract_text pulls result from --output-format json object."""
    payload = json.dumps(
        {"type": "result", "subtype": "success", "result": ":::important\nDomain ready.\n:::"}
    )
    assert extract_text(payload) == ":::important\nDomain ready.\n:::"


def test_extract_text_stream_json():
    """extract_text pulls result from the last result object in stream-json NDJSON."""
    lines = [
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": ":::important\nDomain ready.\n:::"}]}}),
        json.dumps({"type": "result", "subtype": "success", "result": ":::important\nDomain ready.\n:::"}),
    ]
    payload = "\n".join(lines)
    assert extract_text(payload) == ":::important\nDomain ready.\n:::"


def test_extract_text_json_no_result_key():
    """extract_text returns raw string when JSON has no result key."""
    payload = json.dumps({"type": "assistant"})
    assert extract_text(payload) == payload


def test_extract_text_invalid_json():
    """extract_text returns raw string when input looks like JSON but is invalid."""
    payload = "{not valid json"
    assert extract_text(payload) == payload


def test_parse_fences_from_json_input():
    """End-to-end: JSON envelope is unwrapped and fences parsed correctly."""
    inner = ":::important\nDomain 'my_domain' is ready.\n:::\n:::next_step\nRun index-inputs.\n:::"
    payload = json.dumps({"type": "result", "subtype": "success", "result": inner})
    result = parse_fences(extract_text(payload))
    assert len(result) == 2
    assert result[0] == {"type": "important", "content": "Domain 'my_domain' is ready."}
    assert result[1] == {"type": "next_step", "content": "Run index-inputs."}


def test_all_six_types_parsed():
    """All six defined fence types round-trip correctly."""
    types = ["important", "error", "next_step", "detail", "progress", "user_input"]
    for t in types:
        text = f":::{t}\ncontent\n:::"
        result = parse_fences(text)
        assert result == [{"type": t, "content": "content"}], f"failed for type {t!r}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
