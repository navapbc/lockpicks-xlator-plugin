"""Shared helpers for tests in xl-plugin/tools/.

Currently exposes a loader for canonical examples under
`xl-plugin/core/examples/<file_type>/`. Tests opt in opportunistically (per R13
of the examples-corpus plan) — existing inline-dict fixtures continue to work.

Public API
----------
- `canonical_path(file_type) -> Path` — folder under `core/examples/`.
- `load_canonical(file_type) -> dict` — parsed `<folder>/canonical.yaml`.

`canonical_path` returns the *folder* (not the canonical file). Tests that need
adjacent siblings (paired `.md` sources, non-YAML canonicals, README) resolve
them by joining onto the folder path. `load_canonical` is the YAML-loading
entry point.

`.md` / `.md.yaml` canonicals (e.g., `compressed/`, `computations/`) do not have
a dedicated loader in v1 — callers use `canonical_path` and read the bytes
directly. A text-loading helper will be added when a second consumer surfaces.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_CORE_EXAMPLES = Path(__file__).resolve().parents[1] / "core" / "examples"


def _validate_file_type(file_type: str) -> None:
    """Reject file_type values that contain path separators or traversal segments."""
    if "/" in file_type or "\\" in file_type or file_type in {"", ".", ".."}:
        raise ValueError(
            f"Invalid file_type {file_type!r}: must be a single subfolder name "
            f"under {_CORE_EXAMPLES} (no path separators, no traversal)."
        )


def canonical_path(file_type: str) -> Path:
    """Return the corpus subfolder for `file_type`.

    Example: `canonical_path("skeleton")` returns
    `<repo>/xl-plugin/core/examples/skeleton/`. Use this when the canonical is
    not YAML (e.g., `compressed/canonical.md`) or when a paired sibling needs to
    be loaded alongside.
    """
    _validate_file_type(file_type)
    return _CORE_EXAMPLES / file_type


def load_canonical(file_type: str) -> dict[str, object]:
    """Load the canonical YAML for `file_type` and return its parsed contents.

    Probes `<folder>/canonical.yaml` first, then `<folder>/canonical.civil.yaml`
    so the helper covers both ordinary YAML canonicals and CIVIL ruleset
    canonicals (per the corpus README's documented contract).

    Raises:
        FileNotFoundError: when neither `canonical.yaml` nor `canonical.civil.yaml`
            exists in the folder. The message includes the resolved folder so
            the caller can see exactly where the loader looked.
        ValueError: when `file_type` contains a path separator or traversal
            segment, OR when the canonical file's top-level YAML node is not a
            mapping (e.g., the file is empty, scalar-rooted, or list-rooted).
    """
    folder = canonical_path(file_type)
    for leaf in ("canonical.yaml", "canonical.civil.yaml"):
        yaml_path = folder / leaf
        if yaml_path.is_file():
            with yaml_path.open() as f:
                result = yaml.safe_load(f)
            if not isinstance(result, dict):
                raise ValueError(
                    f"{yaml_path} did not parse as a YAML mapping "
                    f"(got {type(result).__name__}); canonicals must be dicts."
                )
            return result
    raise FileNotFoundError(
        f"No canonical.yaml or canonical.civil.yaml found for file_type "
        f"{file_type!r}; looked in {folder}"
    )
