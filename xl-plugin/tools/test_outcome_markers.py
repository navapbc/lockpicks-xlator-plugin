# /// script
# requires-python = ">=3.14"
# ///
"""Tests for outcome_markers.py — covers U1 marker read/write/cleanup scenarios.

Run: uv run xl-plugin/tools/test_outcome_markers.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import outcome_markers  # noqa: E402


def test_write_and_read_succeeded_marker():
    """Happy path: write a success marker; read_markers returns it keyed by src."""
    with tempfile.TemporaryDirectory() as tmp:
        marker_dir = Path(tmp) / ".extract-plan.d"
        outcome_markers.write_marker(
            marker_dir,
            "input/policy_docs/foo.md",
            "succeeded",
            source_sha="abc123",
        )

        markers = outcome_markers.read_markers(marker_dir)

        assert markers == {
            "input/policy_docs/foo.md": {
                "src": "input/policy_docs/foo.md",
                "status": "succeeded",
                "source_sha": "abc123",
            }
        }


def test_write_failed_marker_includes_error_field():
    """Failure markers include the error field; success markers don't."""
    with tempfile.TemporaryDirectory() as tmp:
        marker_dir = Path(tmp) / ".extract-plan.d"
        outcome_markers.write_marker(
            marker_dir,
            "input/policy_docs/bad.md",
            "failed",
            source_sha="def456",
            error="malformed YAML output from skill",
        )

        markers = outcome_markers.read_markers(marker_dir)

        assert markers == {
            "input/policy_docs/bad.md": {
                "src": "input/policy_docs/bad.md",
                "status": "failed",
                "source_sha": "def456",
                "error": "malformed YAML output from skill",
            }
        }


def test_write_in_progress_then_update_to_succeeded():
    """In-progress marker pattern: worker writes in_progress, then updates atomically."""
    with tempfile.TemporaryDirectory() as tmp:
        marker_dir = Path(tmp) / ".compress-plan.d"
        # 1. Worker writes in_progress before invoking the per-file skill.
        outcome_markers.write_marker(
            marker_dir,
            "input/policy_docs/foo.md",
            "in_progress",
            source_sha="abc123",
        )
        first_read = outcome_markers.read_markers(marker_dir)
        assert first_read["input/policy_docs/foo.md"]["status"] == "in_progress"

        # 2. Worker updates to succeeded after the skill returns.
        outcome_markers.write_marker(
            marker_dir,
            "input/policy_docs/foo.md",
            "succeeded",
            source_sha="abc123",
        )
        second_read = outcome_markers.read_markers(marker_dir)
        assert second_read["input/policy_docs/foo.md"]["status"] == "succeeded"


def test_write_marker_with_subdir_source_creates_nested_marker():
    """Source path with subdir creates the nested directory under marker_dir."""
    with tempfile.TemporaryDirectory() as tmp:
        marker_dir = Path(tmp) / ".extract-plan.d"
        outcome_markers.write_marker(
            marker_dir,
            "input/policy_docs/sub/nested.md",
            "succeeded",
            source_sha="xyz",
        )

        expected_path = marker_dir / "input/policy_docs/sub/nested.md.outcome.json"
        assert expected_path.is_file()

        markers = outcome_markers.read_markers(marker_dir)
        assert "input/policy_docs/sub/nested.md" in markers


def test_write_marker_with_spaces_in_path_round_trips():
    """Source paths with spaces (e.g. '441-1 EARNED INCOME.md') round-trip cleanly."""
    with tempfile.TemporaryDirectory() as tmp:
        marker_dir = Path(tmp) / ".extract-plan.d"
        src = "input/policy_docs/441-1 EARNED INCOME.md"
        outcome_markers.write_marker(
            marker_dir, src, "succeeded", source_sha="sha1",
        )

        markers = outcome_markers.read_markers(marker_dir)
        assert src in markers
        assert markers[src]["status"] == "succeeded"


def test_read_markers_on_missing_dir_returns_empty():
    """Edge case: read_markers on a non-existent directory returns {} (does not raise)."""
    with tempfile.TemporaryDirectory() as tmp:
        marker_dir = Path(tmp) / "does_not_exist.d"
        assert outcome_markers.read_markers(marker_dir) == {}


def test_read_markers_skips_corrupt_json():
    """Edge case: a corrupt marker file is silently skipped (treated as no marker)."""
    with tempfile.TemporaryDirectory() as tmp:
        marker_dir = Path(tmp) / ".extract-plan.d"
        marker_dir.mkdir()
        # Write a valid marker.
        outcome_markers.write_marker(
            marker_dir, "input/policy_docs/good.md", "succeeded",
        )
        # Write a corrupt marker file by hand.
        bad_path = marker_dir / "input/policy_docs/bad.md.outcome.json"
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_text("{not valid json")

        markers = outcome_markers.read_markers(marker_dir)
        # Only the valid marker is surfaced.
        assert list(markers.keys()) == ["input/policy_docs/good.md"]


def test_write_marker_rejects_invalid_status():
    """Error path: an invalid status raises ValueError."""
    with tempfile.TemporaryDirectory() as tmp:
        marker_dir = Path(tmp) / ".extract-plan.d"
        try:
            outcome_markers.write_marker(
                marker_dir, "input/policy_docs/foo.md", "completed",
            )
        except ValueError as exc:
            assert "completed" in str(exc)
        else:
            raise AssertionError("expected ValueError for invalid status")


def test_marker_path_for_preserves_md_extension():
    """The .md extension is preserved in the marker filename (R5 / U1 spec)."""
    marker_dir = Path("/tmp/example.d")
    path = outcome_markers.marker_path_for(
        marker_dir, "input/policy_docs/foo.md",
    )
    assert path == marker_dir / "input/policy_docs/foo.md.outcome.json"


def test_atomic_write_uses_tmp_then_replace():
    """The write goes through a tmp file then os.replace (no partial reads).

    We verify by checking that no `.tmp` file is left behind after a successful
    write, and that the final file contains complete JSON.
    """
    with tempfile.TemporaryDirectory() as tmp:
        marker_dir = Path(tmp) / ".extract-plan.d"
        outcome_markers.write_marker(
            marker_dir, "input/policy_docs/foo.md", "succeeded",
            source_sha="abc",
        )

        # No leftover .tmp files in the marker dir.
        leftover = list(marker_dir.rglob("*.tmp"))
        assert leftover == [], f"unexpected leftover tmp files: {leftover}"

        # Final file is complete and parseable.
        path = marker_dir / "input/policy_docs/foo.md.outcome.json"
        with path.open() as f:
            payload = json.load(f)
        assert payload["status"] == "succeeded"


def test_cleanup_marker_dir_removes_all_files_and_dir():
    """cleanup_marker_dir removes nested markers and the dir itself."""
    with tempfile.TemporaryDirectory() as tmp:
        marker_dir = Path(tmp) / ".extract-plan.d"
        outcome_markers.write_marker(
            marker_dir, "input/policy_docs/a.md", "succeeded",
        )
        outcome_markers.write_marker(
            marker_dir, "input/policy_docs/sub/b.md", "succeeded",
        )
        assert marker_dir.is_dir()
        assert any(marker_dir.rglob("*.outcome.json"))

        outcome_markers.cleanup_marker_dir(marker_dir)
        assert not marker_dir.exists()


def test_cleanup_marker_dir_is_noop_when_absent():
    """cleanup_marker_dir on a non-existent dir does not raise."""
    with tempfile.TemporaryDirectory() as tmp:
        marker_dir = Path(tmp) / "absent.d"
        # Should not raise.
        outcome_markers.cleanup_marker_dir(marker_dir)


def test_multiple_markers_in_same_dir_are_independent():
    """Different sources in the same marker dir don't interfere."""
    with tempfile.TemporaryDirectory() as tmp:
        marker_dir = Path(tmp) / ".extract-plan.d"
        outcome_markers.write_marker(
            marker_dir, "input/policy_docs/a.md", "succeeded",
            source_sha="sha1",
        )
        outcome_markers.write_marker(
            marker_dir, "input/policy_docs/b.md", "failed",
            source_sha="sha2", error="boom",
        )

        markers = outcome_markers.read_markers(marker_dir)
        assert set(markers.keys()) == {
            "input/policy_docs/a.md", "input/policy_docs/b.md",
        }
        assert markers["input/policy_docs/a.md"]["status"] == "succeeded"
        assert markers["input/policy_docs/b.md"]["status"] == "failed"
        assert markers["input/policy_docs/b.md"]["error"] == "boom"


# ---------------------------------------------------------------------------
# Test runner: when invoked directly, run all test_* functions.
# ---------------------------------------------------------------------------

def _main():
    failures: list[tuple[str, BaseException]] = []
    test_fns = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    for name, fn in test_fns:
        try:
            fn()
            print(f"  ok  {name}")
        except BaseException as exc:  # noqa: BLE001
            print(f"  FAIL {name}: {exc!r}")
            failures.append((name, exc))
    print(f"\n{len(test_fns) - len(failures)}/{len(test_fns)} passed.")
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    _main()
