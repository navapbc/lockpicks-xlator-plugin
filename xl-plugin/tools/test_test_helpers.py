# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "pytest",
#   "pyyaml",
# ]
# ///
"""Tests for tools/test_helpers.py — the shared canonical-example loader.

Verifies the contract for `load_canonical` (YAML loading) and `canonical_path`
(folder resolution for paired non-YAML siblings).
"""

from pathlib import Path

import pytest

from test_helpers import (
    canonical_path,
    load_canonical,
)


def test_load_canonical_returns_parsed_dict():
    """Happy path — load_canonical returns the canonical's parsed contents."""
    manifest = load_canonical("naming-manifest")
    assert isinstance(manifest, dict)
    assert manifest["version"] == "1.0"
    assert "Applicant" in manifest["inputs"]
    assert "eligibility_decision" in manifest["outputs"]


def test_load_canonical_works_for_every_yaml_canonical():
    """Happy path — every YAML canonical in the corpus loads without error."""
    yaml_file_types = [
        "suggested-target",
        "naming-manifest",
        "metadata",
        "prompt-context",
        "input-variables",
        "output-variables",
        "constants-and-tables",
        "skeleton",
        "ruleset-groups",
        "ruleset-modules",
        "sample-artifacts",
        "sample-tests",
        "tests",
        "input-index",
    ]
    for file_type in yaml_file_types:
        result = load_canonical(file_type)
        assert isinstance(result, dict), (
            f"load_canonical({file_type!r}) did not return a dict"
        )


def test_canonical_path_returns_folder_not_file():
    """Happy path — canonical_path returns the file-type subfolder, not the canonical file.

    The contract is folder-not-file so callers can resolve adjacent siblings
    (paired sources, README, non-YAML canonicals like compressed/canonical.md).
    """
    folder = canonical_path("skeleton")
    assert isinstance(folder, Path)
    assert folder.is_dir()
    assert folder.name == "skeleton"
    assert (folder / "canonical.yaml").exists()


def test_canonical_path_resolves_to_corpus_root():
    """Happy path — the resolved path is rooted under xl-plugin/core/examples/."""
    folder = canonical_path("compressed")
    assert folder.parts[-3:] == ("xl-plugin", "core", "examples", "compressed")[-3:]
    assert (folder / "source.md").exists()
    assert (folder / "canonical.md").exists()


def test_load_canonical_raises_for_unknown_file_type():
    """Error path — unknown file type raises FileNotFoundError with the resolved path in the message."""
    with pytest.raises(FileNotFoundError) as exc_info:
        load_canonical("nonexistent-file-type")
    msg = str(exc_info.value)
    assert "nonexistent-file-type" in msg
    assert "canonical.yaml" in msg


def test_load_canonical_raises_for_path_traversal_attempts():
    """Error path — file_type with path separators is rejected to prevent path traversal."""
    with pytest.raises(ValueError):
        load_canonical("../escape")
    with pytest.raises(ValueError):
        canonical_path("foo/bar")
