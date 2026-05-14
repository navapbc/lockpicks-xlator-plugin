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

import re
from pathlib import Path

import pytest
import yaml

from test_helpers import (
    canonical_path,
    load_canonical,
)


def test_load_canonical_returns_parsed_dict():
    """Happy path — load_canonical returns a non-empty parsed dict.

    Avoids asserting specific keys from the naming-manifest canonical, which
    would couple the helper test to corpus content. Structural assertions only.
    """
    manifest = load_canonical("naming-manifest")
    assert isinstance(manifest, dict)
    assert len(manifest) > 0


@pytest.mark.parametrize(
    "file_type",
    [
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
        "civil-ruleset",
    ],
)
def test_load_canonical_works_for_every_yaml_canonical(file_type):
    """Happy path — every YAML canonical in the corpus loads without error.

    Includes `civil-ruleset` whose canonical lives at `canonical.civil.yaml`;
    the helper probes both `.yaml` and `.civil.yaml` extensions.
    """
    result = load_canonical(file_type)
    assert isinstance(result, dict)


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
    assert folder.parts[-4:] == ("xl-plugin", "core", "examples", "compressed")
    assert (folder / "source.md").exists()
    assert (folder / "canonical.md").exists()


def test_canonical_path_resolves_computations_paired_files():
    """Happy path — computations/ canonical_path resolves and both paired files exist."""
    folder = canonical_path("computations")
    assert (folder / "source.md").exists()
    assert (folder / "canonical.md.yaml").exists()


def test_load_canonical_raises_value_error_for_non_mapping_yaml(tmp_path, monkeypatch):
    """Error path — a canonical that parses as a list/scalar/empty raises ValueError, not silent return."""
    import test_helpers as th

    monkeypatch.setattr(th, "_CORE_EXAMPLES", tmp_path)
    (tmp_path / "list-rooted").mkdir()
    (tmp_path / "list-rooted" / "canonical.yaml").write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="did not parse as a YAML mapping"):
        load_canonical("list-rooted")

    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "canonical.yaml").write_text("")
    with pytest.raises(ValueError, match="did not parse as a YAML mapping"):
        load_canonical("empty")


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


def test_metadata_inline_block_matches_canonical_file():
    """Integration — the inlined metadata YAML in declare-target-ruleset/SKILL.md must equal core/examples/metadata/canonical.yaml.

    The corpus README documents `metadata` as both inlined-into-SKILL.md AND filed at
    `core/examples/metadata/canonical.yaml`. Drift between the two copies would silently
    teach the AI two different metadata shapes. Enforce byte-equality via YAML parse.
    """
    skill_md = (
        canonical_path("metadata").parents[2]
        / "skills"
        / "declare-target-ruleset"
        / "SKILL.md"
    ).read_text()
    fenced_blocks = re.findall(r"```yaml\n(.*?)```", skill_md, re.DOTALL)
    inline_metadata = next(
        (yaml.safe_load(block) for block in fenced_blocks if "display_name" in block),
        None,
    )
    assert inline_metadata is not None, (
        "declare-target-ruleset/SKILL.md must contain a ```yaml fenced block "
        "with the inlined metadata canonical (display_name + description)."
    )
    assert inline_metadata == load_canonical("metadata"), (
        "Inlined metadata block in declare-target-ruleset/SKILL.md has drifted from "
        "core/examples/metadata/canonical.yaml. Update both copies in lockstep."
    )
