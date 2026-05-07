# /// script
# requires-python = ">=3.14"
# ///
"""
Per-file outcome markers for parallel per-file skill loops.

Used by `xlator extract-computations` and `xlator compress-inputs` to record
the outcome of each per-file action (compress, extract, ...) when their
parent skill (`/index-inputs` or standalone `/compress-input`) fans out work
across parallel subagent workers. Each worker writes its own marker file
under a per-action marker directory; `--finalize` collates markers from disk
instead of reading a `succeeded:` / `failed:` array from a shared plan file.

Marker contract:
- Marker filename mirrors the source rel verbatim with `.outcome.json`
  appended. A source at `input/policy_docs/sub/foo.md` produces a marker at
  `<marker_dir>/sub/foo.md.outcome.json`. The `.md` extension is preserved
  so the source path is reconstructable from the marker filename.
- Marker payload: `{"src": "<source_rel>", "status": "<status>",
  "source_sha": "<sha>" | null, "error": "<short>" | omitted}`.
- Status is one of `in_progress`, `succeeded`, `failed`. Workers write
  `in_progress` BEFORE invoking the per-file skill and atomically update to
  `succeeded` or `failed` after. `--finalize` treats missing markers and
  `in_progress` markers both as aborted (cleans up the destination because
  action state is indeterminate), but distinguishes them in the failure
  summary.
- All writes are atomic via tmp + `os.replace`, matching the existing
  manifest-write idiom in `extract_computations.py` / `compress_inputs.py`.

Public API:
- `marker_path_for(marker_dir, source_rel)` -> Path
- `write_marker(marker_dir, source_rel, status, source_sha=None, error=None)`
- `read_markers(marker_dir)` -> dict[str, dict]
- `cleanup_marker_dir(marker_dir)` -> None
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_OUTCOME_SUFFIX = ".outcome.json"

VALID_STATUSES = ("in_progress", "succeeded", "failed")


def marker_path_for(marker_dir: Path, source_rel: str) -> Path:
    """Compute the marker file path for a given source rel.

    `source_rel` is the source path relative to the domain root (e.g.,
    `input/policy_docs/sub/foo.md` or just `sub/foo.md`). The marker
    filename mirrors the rel verbatim with `.outcome.json` appended.
    """
    return Path(marker_dir) / (source_rel + _OUTCOME_SUFFIX)


def write_marker(
    marker_dir: Path,
    source_rel: str,
    status: str,
    source_sha: str | None = None,
    error: str | None = None,
) -> Path:
    """Atomically write a marker for `source_rel` under `marker_dir`.

    Uses tmp + `os.replace` for atomicity. Returns the marker path. Creates
    intermediate directories as needed.

    Raises ValueError if `status` is not in VALID_STATUSES.
    """
    if status not in VALID_STATUSES:
        raise ValueError(
            f"invalid marker status: {status!r}; expected one of {VALID_STATUSES}"
        )
    payload: dict[str, object] = {"src": source_rel, "status": status}
    if source_sha is not None:
        payload["source_sha"] = source_sha
    if error is not None:
        payload["error"] = error

    path = marker_path_for(marker_dir, source_rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=False)
    os.replace(tmp, path)
    return path


def read_markers(marker_dir: Path) -> dict[str, dict]:
    """Read all markers under `marker_dir`, keyed by `src`.

    Returns an empty dict if `marker_dir` does not exist. Markers with
    unreadable JSON are skipped silently — the caller's `--finalize` logic
    treats "no marker for a `to_*` source" as aborted, which produces the
    same downstream effect as a corrupt marker (dst cleanup, exit non-zero).

    The dict key is the marker payload's `src` field (which is the source
    rel as written by the worker). The value is the full payload dict
    (including `status`, `source_sha`, optional `error`).
    """
    marker_dir = Path(marker_dir)
    if not marker_dir.is_dir():
        return {}
    markers: dict[str, dict] = {}
    for path in marker_dir.rglob("*" + _OUTCOME_SUFFIX):
        if not path.is_file():
            continue
        try:
            with path.open(encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        src = payload.get("src")
        if not isinstance(src, str):
            continue
        markers[src] = payload
    return markers


def cleanup_marker_dir(marker_dir: Path) -> None:
    """Remove `marker_dir` and all its contents. No-op if absent."""
    marker_dir = Path(marker_dir)
    if not marker_dir.exists():
        return
    # Walk depth-first and unlink files, then rmdir the (now-empty) dirs.
    for path in sorted(marker_dir.rglob("*"), reverse=True):
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    marker_dir.rmdir()
