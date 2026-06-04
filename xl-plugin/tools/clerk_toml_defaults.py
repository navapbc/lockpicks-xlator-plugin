"""Centralized, tier-aware `clerk.toml` defaults.

Single source of truth for the default `clerk.toml` literals written when a
directory first needs one. Two tiers exist today:

- **spec tier** — the spec directory (`specs/`) and the `output/` build dir.
  `include_dirs = ["."]` lets `clerk` resolve sibling modules in the same dir.
- **test tier** — a directory named `tests` (e.g., `specs/tests/`,
  `output/tests/`). `include_dirs = [".", ".."]` additionally lets a test
  fixture's `> Using <ParentModule>` resolve the module declared in the
  parent spec/build dir.

**Tier rule (stable):** a directory is test-tier iff its *basename* is exactly
`tests`. Every other basename is spec-tier. This is a strict-leaf rule, not a
recursive-ancestor one: `specs/tests/` is test-tier, but a deeper directory
such as `specs/tests/regression/` (basename `regression`) is spec-tier.
Layouts nested deeper than `<dir>/tests/` are an explicit non-goal — no skill
creates them today, and recursive matching would not produce a correct include
path for them anyway (`specs/tests/regression/` would need `["..", ".."]` to
reach the spec module, which neither tier provides). Revisit this rule only if
such a layout is introduced.

All callers (`clerk_loop.ensure_catala_bootstrap`, and any future writer)
import the literal from here so the two tiers cannot drift apart — a change to
either default lands in exactly one place.
"""

from __future__ import annotations

from pathlib import Path

# Spec-tier default. Byte-for-byte identical to the pre-centralization literal
# (`clerk_loop._CLERK_TOML_DEFAULT` and the inline literal formerly in
# `xlator.cmd_new_domain`) so no existing clerk consumer sees a diff.
SPEC_TIER = """[project]
target_dir = "_targets"
include_dirs = ["."]
"""

# Test-tier default. Adds `..` so a fixture under `tests/` resolves modules
# declared in the parent directory.
TEST_TIER = """[project]
target_dir = "_targets"
include_dirs = [".", ".."]
"""


def clerk_toml_for(dir_path: Path | str) -> str:
    """Return the default `clerk.toml` contents for the directory's tier.

    Test-tier (`[".", ".."]`) iff the directory's basename is exactly `tests`;
    otherwise spec-tier (`["."]`). See the module docstring for the rule's
    rationale and limits.
    """
    return TEST_TIER if Path(dir_path).name == "tests" else SPEC_TIER
