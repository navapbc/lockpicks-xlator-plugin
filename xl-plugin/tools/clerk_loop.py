#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator clerk-loop: deterministic post-emission helper for Catala modules.

Library-primary tool that drives `clerk typecheck` + `clerk test`, parses
`catala` GNU-format diagnostics into typed records, runs the shared
naming-manifest divergence check, tracks repair history across iterations,
and returns a structured outcome the calling skill (or CLI consumer) can
act on.

Plan reference: U2 of docs/plans/2026-05-28-001-refactor-replace-civil-
with-catala-plan.md.

Cross-module contract check (Step 3 of the plan) is IMPLICIT in
`clerk typecheck`: a synthetic two-module fixture with a deliberate
exported-type mismatch produced this diagnostic from `clerk typecheck`:

    I don't know how to apply operator + on types SubModule.Color and integer

The `cross_module_contract` walker is therefore not implemented; the
category is retained in the Diagnostic taxonomy so future callers can
classify whichever surface emits the contract mismatch.

Library API
-----------

    from clerk_loop import run, LoopResult, Diagnostic, Attempt

    result = run(Path("domains/snap/specs/Eligibility.catala_en"))
    if result.status == "ok":
        ...
    else:
        for diag in result.last_diagnostics:
            ...

CLI surface
-----------

    xlator clerk-loop <domain> <module>

Emits a JSON header line, the per-tool sentinel divider
`--- CLERK-LOOP-HEADER-END ---`, then a human-readable summary. Matches
the per-tool sentinel convention from
`xl-plugin/tools/merge_naming_manifest.py:67`.

Exit codes:
    0 — status == "ok"
    1 — status == "unresolved" (loop hit max_iterations or halted on naming
        divergence / missing tooling)
    2 — pre-flight failure (module file missing, etc.)
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEADER_SENTINEL = "--- CLERK-LOOP-HEADER-END ---"

# Default and only initial value for the iteration cap (U9 will calibrate).
_DEFAULT_MAX_ITERATIONS = 5

# Same-category repeat window (K). When the most recent K iterations all
# share a diagnostic category and the count did not decrease, the loop
# library marks the latest attempt as "regenerate".
_SAME_CATEGORY_WINDOW = 2

# Density threshold: more than 1 error per 20 source lines triggers
# regenerate signalling.
_DENSITY_LINES_PER_ERROR = 20

# GNU-format diagnostic line. Catala 1.1.0 emits:
#   file:line.col-line.col: [SEVERITY] message...
# or:
#   file:line.col-line.col: severity: message...
# Anchor on the colon-delimited prefix (file, location, square-bracket
# tag), not the message body — keeps the parser robust to cosmetic
# upstream wording changes.
_GNU_LINE_RE = re.compile(
    r"""^
    (?P<file>[^:]+(?::[A-Za-z]:[^:]+)?)   # file path (handles Windows drive)
    :
    (?P<line>\d+)
    (?:\.(?P<col>\d+))?
    (?:-\d+(?:\.\d+)?)?                   # optional end-line.col
    :\s*
    (?:\[(?P<tag>[A-Z]+)\]|(?P<sev>[A-Za-z]+):)
    \s*
    (?P<message>.*)
    $""",
    re.VERBOSE,
)

# Category inference keywords — ordered most-specific first. The first
# match wins. Keywords are case-folded against the message body.
#
# Resolution order: ultra-specific phrases (multi-word) come before bare
# keywords so e.g. "apply operator" routes to `type` even when the
# message also mentions a sub-module name.
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("naming_divergence", ("naming-manifest", "naming divergence")),
    # Very-specific multi-word phrases first
    ("type", (
        "apply operator", "on types ",
        "cannot unify", "does not unify",
    )),
    ("fence", ("catala-metadata", "metadata fence", "fence visibility",
               "metadata is hidden")),
    ("enum", ("enumeration", "non-exhaustive", "exhaustive match")),
    ("exception", ("exception default", "exception priority",
                   "conflicting default")),
    # Bare keywords (single-token). Order matters: more-specific
    # categories first.
    ("enum", ("enum",)),
    ("exception", ("exception",)),
    ("module", ("module", "stdlib_en")),
    ("fence", ("metadata", "fence", "visibility")),
    ("runtime", ("division", "overflow", "uncaught", "assertion")),
    ("type", ("type", "operator", "applied to", "expected")),
    ("scope", ("scope", "rule", "definition")),
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ClerkLoopError(Exception):
    """Raised on pre-flight / environment failures the library cannot
    paper over (missing module file, missing `clerk`)."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass
class Diagnostic:
    """A single parsed diagnostic, normalized across `clerk typecheck` and
    `clerk test`."""

    file: str
    line: int
    col: int
    severity: str
    category: str
    message: str
    raw: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Attempt:
    """One iteration's signal record. The library does NOT patch or
    regenerate — `action_taken` is a recommendation flag for the calling
    skill, computed from same-category-repeat / density / unparseable-
    region heuristics."""

    iteration: int
    diagnostics: list[Diagnostic]
    action_taken: Literal["patch", "regenerate"]
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "iteration": self.iteration,
            "diagnostics": [d.as_dict() for d in self.diagnostics],
            "action_taken": self.action_taken,
            "notes": self.notes,
        }


@dataclass
class LoopResult:
    status: Literal["ok", "unresolved"]
    iterations: int
    last_diagnostics: list[Diagnostic] = field(default_factory=list)
    repair_history: list[Attempt] = field(default_factory=list)
    summary: str = ""
    # Surface the regenerate-recommended flag for the most recent attempt
    # so consuming skills do not have to re-derive it from repair_history.
    regenerate_recommended: bool = False

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "iterations": self.iterations,
            "last_diagnostics": [d.as_dict() for d in self.last_diagnostics],
            "repair_history": [a.as_dict() for a in self.repair_history],
            "summary": self.summary,
            "regenerate_recommended": self.regenerate_recommended,
        }


# ---------------------------------------------------------------------------
# GNU diagnostic parser
# ---------------------------------------------------------------------------

def parse_gnu_diagnostics(text: str) -> list[Diagnostic]:
    """Parse `catala --message-format=gnu` output into Diagnostic records.

    Skips non-matching lines silently — the GNU stream interleaves with
    other tool output (RESULT banners, traceback boxes from older invocations,
    etc.). Unmappable text is captured by the `unparseable_region` flag at
    a higher layer."""
    diags: list[Diagnostic] = []
    for raw_line in text.splitlines():
        m = _GNU_LINE_RE.match(raw_line.strip())
        if not m:
            continue
        tag = m.group("tag")
        sev = m.group("sev")
        severity = (tag or sev or "error").lower()
        message = m.group("message").strip()
        category = _infer_category(message)
        diags.append(
            Diagnostic(
                file=m.group("file"),
                line=int(m.group("line")),
                col=int(m.group("col") or 0),
                severity=severity,
                category=category,
                message=message,
                raw=raw_line.strip(),
            )
        )
    return diags


def _infer_category(message: str) -> str:
    body = message.lower()
    for cat, keywords in _CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw in body:
                return cat
    return "other"


# ---------------------------------------------------------------------------
# Patch-vs-regenerate signals
# ---------------------------------------------------------------------------

def same_category_repeat(history: Iterable[Attempt], window: int = _SAME_CATEGORY_WINDOW) -> bool:
    """True iff the last `window` attempts share a non-empty category set
    and the diagnostic count did not strictly decrease across them."""
    history_list = list(history)
    if len(history_list) < window:
        return False
    tail = history_list[-window:]
    # Build a category-multiset per attempt.
    cat_sets = [tuple(sorted({d.category for d in a.diagnostics})) for a in tail]
    if any(not cs for cs in cat_sets):
        return False
    if len(set(cat_sets)) > 1:
        return False
    counts = [len(a.diagnostics) for a in tail]
    return counts[-1] >= counts[0] and counts[0] > 0


def density_threshold_exceeded(
    diagnostics: Iterable[Diagnostic], source_lines: int,
    lines_per_error: int = _DENSITY_LINES_PER_ERROR,
) -> bool:
    """True iff `len(diagnostics) > source_lines / lines_per_error`."""
    diags = list(diagnostics)
    if source_lines <= 0:
        return False
    return len(diags) * lines_per_error > source_lines


def unparseable_region(raw_text: str, parsed: Iterable[Diagnostic]) -> bool:
    """True iff GNU output is present (lines containing `[ERROR]` or
    `[WARNING]` tags) but the parser yielded no Diagnostic records. The
    raw GNU lines are colon-prefixed; the human-format `[ERROR]` decoration
    inside box-drawn frames does NOT match `_GNU_LINE_RE`, so seeing
    boxed errors with no parsed records means the caller forgot to set
    `--message-format=gnu` or the regex is out of date."""
    has_signal = ("[ERROR]" in raw_text) or ("[WARNING]" in raw_text)
    return has_signal and not list(parsed)


def classify_action(
    history: Iterable[Attempt],
    latest_diagnostics: Iterable[Diagnostic],
    source_lines: int,
    raw_text: str,
) -> tuple[Literal["patch", "regenerate"], str]:
    """Return ("patch" | "regenerate", explanatory_note).

    Resolution order: density (cheap, no history needed) → unparseable →
    same-category repeat. Any True flag pushes to "regenerate"."""
    diagnostics = list(latest_diagnostics)
    history_list = list(history)
    reasons: list[str] = []

    if density_threshold_exceeded(diagnostics, source_lines):
        reasons.append(
            f"density>1/{_DENSITY_LINES_PER_ERROR} ({len(diagnostics)} errors, "
            f"{source_lines} lines)"
        )
    if unparseable_region(raw_text, diagnostics):
        reasons.append("unparseable diagnostic region (no GNU records matched)")
    if same_category_repeat(history_list + [
        Attempt(
            iteration=(history_list[-1].iteration + 1) if history_list else 1,
            diagnostics=diagnostics,
            action_taken="patch",
        )
    ]):
        reasons.append(
            f"same-category repeat across last K={_SAME_CATEGORY_WINDOW} iterations"
        )

    if reasons:
        return "regenerate", "; ".join(reasons)
    return "patch", ""


# ---------------------------------------------------------------------------
# Tool invocation
# ---------------------------------------------------------------------------

def _require_clerk() -> str:
    """Return the absolute path to `clerk`, or raise ClerkLoopError with
    install hint."""
    path = shutil.which("clerk")
    if path is None:
        raise ClerkLoopError(
            "clerk not found in PATH. Install the Catala toolchain "
            "(see https://catala-lang.org/) or activate the OPAM switch "
            "that provides it (e.g. `opam install catala`)."
        )
    return path


_CLERK_TOML_DEFAULT = """[project]
target_dir = "_targets"
include_dirs = ["."]
"""


def ensure_catala_bootstrap(work_dir: Path) -> None:
    """Bootstrap the Catala stdlib for direct `catala` invocations in `work_dir`.

    Project convention: all `clerk` and `catala` commands MUST be invoked
    from the folder containing `clerk.toml` (typically `specs/` or
    `output/`). This helper makes that location self-sufficient: it ensures
    `clerk.toml` exists, then runs `clerk start` to materialize
    `_build/libcatala` when absent.

    Idempotent. Safe to call on every command — clerk start exits 0 quickly
    once the stdlib is in place.
    """
    if shutil.which("clerk") is None:
        return
    clerk_toml = work_dir / "clerk.toml"
    if not clerk_toml.is_file():
        clerk_toml.write_text(_CLERK_TOML_DEFAULT)
    libcatala = work_dir / "_build" / "libcatala"
    if libcatala.is_dir() and any(libcatala.iterdir()):
        return
    subprocess.run(
        ["clerk", "start"],
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        check=False,
    )


def _run_clerk_typecheck(module_path: Path, include_dirs: list[Path]) -> tuple[int, str, str]:
    """Invoke `clerk typecheck` with GNU message format forwarded via
    --catala-opts. Return (returncode, stdout, stderr)."""
    _require_clerk()
    ensure_catala_bootstrap(module_path.parent)
    cmd: list[str] = ["clerk", "typecheck",
                      "--catala-opts=--message-format=gnu"]
    for inc in include_dirs:
        cmd.extend(["--include", str(inc)])
    cmd.append(str(module_path))
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        cwd=str(module_path.parent),
    )
    return proc.returncode, proc.stdout, proc.stderr


def _run_clerk_test(module_path: Path, include_dirs: list[Path]) -> tuple[int, str, str]:
    """Invoke `clerk test` against the module's directory with GNU
    message format forwarded. Return (returncode, stdout, stderr).

    `clerk test` expects target NAMES (declared in clerk.toml) rather than
    raw file paths; we pass NO positional argument and rely on cwd
    discovery so clerk auto-detects testable files in the module's
    directory (which is what `xlator catala-test` already does — see
    `xlator.py:cmd_catala_test`).
    """
    _require_clerk()
    ensure_catala_bootstrap(module_path.parent)
    cmd: list[str] = ["clerk", "test",
                      "--catala-opts=--message-format=gnu"]
    for inc in include_dirs:
        cmd.extend(["--include", str(inc)])
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        cwd=str(module_path.parent),
    )
    return proc.returncode, proc.stdout, proc.stderr


# Sentinel emitted by `clerk test` when no test cases failed. Catala 1.1.0
# wraps it in a box-drawn banner: "ALL TESTS PASSED". Used to decide
# whether a non-zero clerk-test exit is a runtime warning (per
# SPIKE_RESULT.md lesson #3 — dry-run evaluation can raise Division_by_zero
# style runtime errors even when no #[test] annotations exist) versus a
# genuine test failure.
_CLERK_TEST_PASS_SENTINEL = "ALL TESTS PASSED"
_CLERK_TEST_FAIL_SENTINEL = "TESTS FAILED"


def _clerk_test_passed(rc: int, output: str) -> bool:
    """Return True iff clerk test should be treated as a pass.

    Catala 1.1.0 `clerk test`:
      - exits 0 with "ALL TESTS PASSED" banner → pass;
      - exits non-zero with "ALL TESTS PASSED" banner AND no GNU
        diagnostics → runtime warning (e.g. Division_by_zero in
        dry-run evaluation per SPIKE_RESULT.md). Treat as pass.
      - emits "TESTS FAILED" banner → failure regardless of exit.
    """
    if _CLERK_TEST_FAIL_SENTINEL in output:
        return False
    if _CLERK_TEST_PASS_SENTINEL in output:
        return True
    return rc == 0


def _catala_dependency_graph(module_path: Path) -> Optional[dict]:
    """Run `catala dependency-graph` and return the parsed JSON, or None
    on failure. Failure is non-fatal: the divergence check downgrades to
    a noop with a warning when the graph can't be generated."""
    if shutil.which("catala") is None:
        return None
    ensure_catala_bootstrap(module_path.parent)
    proc = subprocess.run(
        ["catala", "dependency-graph", module_path.name],
        cwd=str(module_path.parent),
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Naming-manifest divergence check
# ---------------------------------------------------------------------------

def _collect_manifest_keys(manifest: dict) -> tuple[set[str], set[str], set[str]]:
    """Flatten manifest into three sets:

    - `input_fields`: leaf names under `inputs.<Entity>.<field>` (these
      are struct fields, NOT scope-level identifiers; the depgraph
      surfaces them only as part of `<entity>.<field>` accesses);
    - `computed_outputs`: leaf names under `computed.*` and `outputs.*`
      (these are scope-level identifiers and DO appear as depgraph nodes);
    - `entities`: entity names (`inputs.<Entity>` keys), lower-cased — the
      Catala source typically binds them as `input <entity_lower> content
      <Entity>`, surfacing the bound name as a depgraph node.
    """
    input_fields: set[str] = set()
    computed_outputs: set[str] = set()
    entities: set[str] = set()
    inputs = manifest.get("inputs") or {}
    if isinstance(inputs, dict):
        for entity, fields in inputs.items():
            if isinstance(entity, str):
                entities.add(entity.lower())
            if isinstance(fields, dict):
                input_fields.update(k for k in fields.keys() if isinstance(k, str))
    for section in ("computed", "outputs"):
        sec = manifest.get(section) or {}
        if isinstance(sec, dict):
            computed_outputs.update(k for k in sec.keys() if isinstance(k, str))
    return input_fields, computed_outputs, entities


# Regexes for Catala source identifier harvesting. The depgraph only
# surfaces scope-level node labels; struct fields are declared as
# `data <name> content <type>` and never appear as graph nodes, so we
# also parse the source text for them.
_RE_DATA_FIELD = re.compile(r"^\s*data\s+([a-z_][a-z0-9_]*)\s+content\b", re.MULTILINE)
_RE_SCOPE_VAR = re.compile(
    r"^\s*(?:input|output|internal|context)\s+([a-z_][a-z0-9_]*)\s+content\b",
    re.MULTILINE,
)
# Sub-scope binding: `<name> scope <Module>.<Scope>` or
# `output <name> scope <Module>.<Scope>` inside a scope declaration.
# The binding name is Catala-local — not a manifest concept.
_RE_SUB_SCOPE_BINDING = re.compile(
    r"^\s*(?:output\s+)?([a-z_][a-z0-9_]*)\s+scope\s+[A-Za-z_]",
    re.MULTILINE,
)
# Comprehension / quantifier-bound variable. Covers:
#   exists <v> among ..., for all <v> among ..., list of <v> among ...,
#   map each <v> among ..., combine all <v> among ..., content of <v> among ...,
#   number of <v> among ...
# These are local Catala iteration vars — never manifest concepts.
_RE_COMPREHENSION_VAR = re.compile(
    r"\b(?:exists|for\s+all|list\s+of|map\s+each|combine\s+all|content\s+of|number\s+of)\s+([a-z_][a-z0-9_]*)\s+among\b",
)
# Tuple comprehension: `map each (x, y) among (lst1, lst2) to expr`. Both
# names are local Catala vars.
_RE_COMPREHENSION_TUPLE = re.compile(
    r"\b(?:map\s+each|combine\s+all)\s+\(\s*([a-z_][a-z0-9_]*)\s*,\s*([a-z_][a-z0-9_]*)\s*\)\s+among\b",
)


def _collect_source_text_identifiers(module_path: Path) -> set[str]:
    """Parse the .catala_en source for declared identifiers the depgraph
    doesn't surface (struct fields, scope-bound variables).

    Reads only `data <name> content ...` and `input|output|internal|
    context <name> content ...` lines inside `catala-metadata` blocks —
    these are the declaration forms the manifest tracks. Identifier
    accesses (`household.monthly_gross_income`) are intentionally NOT
    parsed; the divergence check is about declared identifiers, not
    usages.
    """
    try:
        text = module_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    ids: set[str] = set()
    ids.update(_RE_DATA_FIELD.findall(text))
    ids.update(_RE_SCOPE_VAR.findall(text))
    return ids


def _collect_catala_local_names(module_path: Path) -> set[str]:
    """Parse the .catala_en source for names that are Catala-local —
    bound by the language itself, NOT by policy / the naming manifest.

    Collects:
      - sub-scope bindings (`<name> scope Module.Scope`, optionally
        prefixed with `output`),
      - comprehension / quantifier vars (`exists <v> among ...`,
        `for all <v> among ...`, `map each <v> among ...`,
        `combine all <v> among ...`, `list of <v> among ...`,
        `content of <v> among ...`, `number of <v> among ...`,
        plus 2-tuple forms `map each (x, y) among ...`).

    Names returned here are subtracted from the depgraph's bare node
    labels in the divergence check so that Catala-local bindings do NOT
    surface as "missing from manifest" false positives. Scope-variable
    declarations (`input|output|internal|context <name> content ...`)
    are deliberately EXCLUDED here — those names SHOULD appear in the
    manifest, so they remain subject to the divergence check.
    """
    try:
        text = module_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    ids: set[str] = set()
    ids.update(_RE_SUB_SCOPE_BINDING.findall(text))
    ids.update(_RE_COMPREHENSION_VAR.findall(text))
    for a, b in _RE_COMPREHENSION_TUPLE.findall(text):
        ids.add(a)
        ids.add(b)
    return ids


def _collect_source_identifiers(graph: dict) -> tuple[set[str], set[str]]:
    """Return (bare_ids, dotted_field_tails) from the depgraph.

    bare_ids: node labels that are not dotted (e.g. `household`,
    `is_eligible`). These map to scope-level identifiers — computed
    fields, outputs, and bound input names.

    dotted_field_tails: for any node label of the form `entity.field`,
    the tail (`field`). These match `inputs.<Entity>.<field>` manifest
    entries even when the depgraph surfaces them as struct accesses.

    Sub-module-qualified names (`SubModule.SomeScope.x`) — three or more
    dotted segments — are out of scope for the primary-module manifest
    and dropped.
    """
    bare: set[str] = set()
    dotted_tails: set[str] = set()
    intra = graph.get("intra_scopes") or {}
    for scope_info in intra.values():
        if not isinstance(scope_info, dict):
            continue
        nodes = scope_info.get("nodes") or {}
        if not isinstance(nodes, dict):
            continue
        for label in nodes.values():
            if not isinstance(label, str):
                continue
            parts = label.split(".")
            if len(parts) == 1:
                bare.add(label)
            elif len(parts) == 2:
                dotted_tails.add(parts[1])
            # 3+ segments → sub-module-qualified; drop
    return bare, dotted_tails


def _find_manifest_for(module_path: Path) -> Optional[Path]:
    """Locate `naming-manifest.yaml` alongside the module. Per the project
    convention, the manifest lives at `specs/naming-manifest.yaml` of the
    domain. We look in the module's parent, parent.parent, and
    parent.parent.parent — covering test fixtures (sibling of the module)
    and real domains (sibling specs/ directory)."""
    candidates = [
        module_path.parent / "naming-manifest.yaml",
        module_path.parent / "specs" / "naming-manifest.yaml",
        module_path.parent.parent / "specs" / "naming-manifest.yaml",
        module_path.parent.parent / "naming-manifest.yaml",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def naming_divergence_check(
    module_path: Path,
    manifest_path: Optional[Path] = None,
) -> list[Diagnostic]:
    """Set-diff manifest keys against Catala source identifiers from
    `catala dependency-graph`. Emit a `naming_divergence` Diagnostic per
    missing identifier (manifest-side and source-side), each with both
    resolution options in the message body. Halts the loop at a higher
    layer — does NOT auto-rewrite.

    Returns an empty list when:
      - no manifest is found (silent — divergence cannot be evaluated),
      - `catala dependency-graph` is unavailable or fails,
      - the sets match.
    """
    if manifest_path is None:
        manifest_path = _find_manifest_for(module_path)
    if manifest_path is None:
        return []

    try:
        with manifest_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []

    graph = _catala_dependency_graph(module_path)

    input_fields, computed_outputs, entities = _collect_manifest_keys(raw)

    # Graph-derived identifiers are scope-level (bare) + dotted entity
    # accesses; source-text-derived identifiers are declared names (struct
    # fields + scope-bound variables). The union covers everything the
    # manifest is expected to align against.
    if graph is None:
        bare_ids: set[str] = set()
        dotted_tails: set[str] = set()
    else:
        bare_ids, dotted_tails = _collect_source_identifiers(graph)
    declared_in_source = _collect_source_text_identifiers(module_path)
    catala_local_names = _collect_catala_local_names(module_path)

    # Manifest → source: a manifest entry is satisfied when its name
    # appears as a declared identifier in the source, OR as a depgraph
    # node label, OR as the tail of a dotted access.
    all_source_ids = bare_ids | dotted_tails | declared_in_source
    diags: list[Diagnostic] = []
    missing_in_source = sorted(
        (input_fields | computed_outputs) - all_source_ids
    )
    # Source → manifest: bare graph-node ids that are not in the manifest
    # AND not a known entity binding are reported. Catala-local names
    # (sub-scope bindings like `hc scope X.Y`, comprehension vars like
    # `exists m among ...`) are language-local — not manifest concepts —
    # and must be filtered out before reporting.
    known_in_manifest = input_fields | computed_outputs | entities
    missing_in_manifest = sorted(
        bare_ids - known_in_manifest - catala_local_names
    )

    for name in missing_in_source:
        msg = (
            f"identifier '{name}' is declared in naming-manifest.yaml but "
            f"not present in the Catala source. Resolve by either: "
            f"(a) adding the identifier to the Catala source (preferred when "
            f"the rule is missing), or (b) removing the entry from the "
            f"manifest (preferred when the identifier was intentionally "
            f"dropped)."
        )
        diags.append(
            Diagnostic(
                file=str(manifest_path),
                line=0,
                col=0,
                severity="error",
                category="naming_divergence",
                message=msg,
                raw=msg,
            )
        )
    for name in missing_in_manifest:
        msg = (
            f"identifier '{name}' appears in the Catala source but is not "
            f"declared in naming-manifest.yaml. Resolve by either: "
            f"(a) renaming the identifier in the Catala source to a "
            f"manifest-declared name, or (b) adding the identifier to the "
            f"manifest under its appropriate section."
        )
        diags.append(
            Diagnostic(
                file=str(module_path),
                line=0,
                col=0,
                severity="error",
                category="naming_divergence",
                message=msg,
                raw=msg,
            )
        )
    return diags


def naming_divergence_check_aggregated(
    manifest_path: Path,
    module_paths: list[Path],
    *,
    warnings_out: Optional[list[str]] = None,
) -> list[Diagnostic]:
    """Aggregated set-diff: a manifest entry is satisfied when ANY module
    in `module_paths` declares it; a source-side identifier is unmatched
    only when no manifest entry covers it across the entire work-list.

    Returns the same shape of Diagnostic list as `naming_divergence_check`
    so consuming skills can render both single-module and aggregated
    results uniformly.

    Returns an empty list when the manifest path doesn't exist (silent —
    same fallback as the single-module check). Missing module files are
    skipped, with a warning emitted to stderr and (optionally) appended
    to `warnings_out`; aggregation continues across the remaining
    modules.

    The source → manifest direction (R3) anchors each diagnostic to a
    canonical declaring module (the sorted-first path that declared the
    identifier) and names every declaring module in the message body so
    the analyst can locate the offending identifier across the work-list.
    """
    if not manifest_path.is_file():
        return []

    try:
        with manifest_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []

    input_fields, computed_outputs, entities = _collect_manifest_keys(raw)

    aggregated_bare: set[str] = set()
    aggregated_dotted_tails: set[str] = set()
    aggregated_declared: set[str] = set()
    aggregated_catala_local: set[str] = set()
    # Map: bare identifier → set of declaring module paths (used for the
    # source → manifest direction's `file=` anchor and message body).
    bare_attribution: dict[str, set[Path]] = {}

    for module_path in module_paths:
        if not module_path.is_file():
            warning = (
                f"naming_divergence_check_aggregated: skipped missing "
                f"module file {module_path}"
            )
            print(f"WARN: {warning}", file=sys.stderr)
            if warnings_out is not None:
                warnings_out.append(warning)
            continue

        graph = _catala_dependency_graph(module_path)
        if graph is None:
            bare_ids: set[str] = set()
            dotted_tails: set[str] = set()
        else:
            bare_ids, dotted_tails = _collect_source_identifiers(graph)
        declared_in_source = _collect_source_text_identifiers(module_path)

        aggregated_bare |= bare_ids
        aggregated_dotted_tails |= dotted_tails
        aggregated_declared |= declared_in_source
        aggregated_catala_local |= _collect_catala_local_names(module_path)
        for name in bare_ids:
            bare_attribution.setdefault(name, set()).add(module_path)

    all_source_ids = (
        aggregated_bare | aggregated_dotted_tails | aggregated_declared
    )
    known_in_manifest = input_fields | computed_outputs | entities

    diags: list[Diagnostic] = []
    missing_in_source = sorted(
        (input_fields | computed_outputs) - all_source_ids
    )
    # Subtract Catala-local names (sub-scope bindings, comprehension vars)
    # aggregated across all modules so they don't surface as "missing
    # from manifest" — language-local, not policy concepts.
    missing_in_manifest = sorted(
        aggregated_bare - known_in_manifest - aggregated_catala_local
    )

    for name in missing_in_source:
        msg = (
            f"identifier '{name}' is declared in naming-manifest.yaml but "
            f"not present in the Catala source. Resolve by either: "
            f"(a) adding the identifier to the Catala source (preferred when "
            f"the rule is missing), or (b) removing the entry from the "
            f"manifest (preferred when the identifier was intentionally "
            f"dropped)."
        )
        diags.append(
            Diagnostic(
                file=str(manifest_path),
                line=0,
                col=0,
                severity="error",
                category="naming_divergence",
                message=msg,
                raw=msg,
            )
        )

    for name in missing_in_manifest:
        declarers = sorted(bare_attribution.get(name, set()))
        canonical = str(declarers[0]) if declarers else ""
        if len(declarers) > 1:
            origin = (
                f" Declared in: "
                + ", ".join(str(p) for p in declarers)
                + "."
            )
        else:
            origin = ""
        msg = (
            f"identifier '{name}' appears in the Catala source but is not "
            f"declared in naming-manifest.yaml. Resolve by either: "
            f"(a) renaming the identifier in the Catala source to a "
            f"manifest-declared name, or (b) adding the identifier to the "
            f"manifest under its appropriate section."
            + origin
        )
        diags.append(
            Diagnostic(
                file=canonical,
                line=0,
                col=0,
                severity="error",
                category="naming_divergence",
                message=msg,
                raw=msg,
            )
        )
    return diags


# ---------------------------------------------------------------------------
# Operational hygiene
# ---------------------------------------------------------------------------

def _try_reset_log() -> None:
    """Best-effort call into `catala_runtime.reset_log()` between
    iterations (PR #45 prevention). Importing `catala_runtime` requires
    the package to be on sys.path; we silently no-op when it isn't, since
    short-lived CLI invocations don't accumulate state."""
    try:
        # Catala runtime lives outside the tools/ package; insert its
        # parent on sys.path on demand.
        runtime_dir = Path(__file__).resolve().parent.parent / "core" / "catala" / "python"
        if runtime_dir.is_dir() and str(runtime_dir) not in sys.path:
            sys.path.insert(0, str(runtime_dir))
        mod = importlib.import_module("catala_runtime")
        reset = getattr(mod, "reset_log", None)
        if callable(reset):
            reset()
    except Exception:
        # Operational hygiene must never raise — the loop owns
        # diagnostics, not side-effects.
        return


# ---------------------------------------------------------------------------
# Source-line counter
# ---------------------------------------------------------------------------

def _count_source_lines(module_path: Path) -> int:
    try:
        with module_path.open(encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Library entry point
# ---------------------------------------------------------------------------

def run(
    module_path: Path | str,
    *,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    reset_log: bool = True,
    include_dirs: Optional[list[Path]] = None,
    manifest_path: Optional[Path] = None,
    skip_naming_divergence_check: bool = False,
) -> LoopResult:
    """Drive the clerk-loop against `module_path`.

    The library does NOT patch or regenerate — it inspects the current
    state of the module and reports a structured outcome. Calling skills
    (which emit the source) loop externally, regenerating or patching
    between calls.

    Parameters
    ----------
    module_path : path to the .catala_en module under test.
    max_iterations : initial cap N=5 (U9 calibrates).
    reset_log : when True, call `catala_runtime.reset_log()` after each
        clerk invocation. Default True.
    include_dirs : extra `--include` directories for clerk (sub-modules
        live alongside or in adjacent dirs). Defaults to the module's
        parent directory.
    manifest_path : override `naming-manifest.yaml` discovery. When None,
        the canonical search order applies.
    skip_naming_divergence_check : when True, bypass the per-iteration
        in-loop naming-manifest divergence check. Intended for orchestrators
        that perform an aggregated divergence check across multiple modules
        as a post-pass (the per-file check is structurally unable to
        converge against a shared manifest covering identifiers in sibling
        modules). Default False preserves the single-module CLI contract.
    """
    module_path = Path(module_path)
    if not module_path.is_file():
        raise ClerkLoopError(f"module file not found: {module_path}")

    if include_dirs is None:
        include_dirs = [module_path.parent]

    source_lines = _count_source_lines(module_path)
    history: list[Attempt] = []
    last_diagnostics: list[Diagnostic] = []
    last_raw = ""

    for iteration in range(1, max_iterations + 1):
        diagnostics: list[Diagnostic] = []
        raw_segments: list[str] = []

        # Step 1: clerk typecheck
        rc_tc, out_tc, err_tc = _run_clerk_typecheck(module_path, include_dirs)
        combined_tc = (out_tc or "") + "\n" + (err_tc or "")
        raw_segments.append(combined_tc)
        diagnostics.extend(parse_gnu_diagnostics(combined_tc))

        # Step 2: clerk test (only when typecheck passed — running tests
        # against a typecheck-failing module wastes work and confuses the
        # diagnostic stream). `clerk test` may exit non-zero even when
        # "ALL TESTS PASSED" appears (SPIKE_RESULT.md lesson #3 —
        # runtime errors during dry-run evaluation); we treat that as a
        # pass and rely on the GNU-format diagnostics for actionable
        # signal.
        test_passed = True
        if rc_tc == 0:
            rc_t, out_t, err_t = _run_clerk_test(module_path, include_dirs)
            combined_t = (out_t or "") + "\n" + (err_t or "")
            raw_segments.append(combined_t)
            diagnostics.extend(parse_gnu_diagnostics(combined_t))
            test_passed = _clerk_test_passed(rc_t, combined_t)

        # Step 3: cross-module contract check — SKIPPED. The synthetic
        # two-module fixture verification (see module docstring) confirmed
        # `clerk typecheck` catches exported-type mismatches with the
        # "I don't know how to apply operator + on types ..." diagnostic.
        # The `cross_module_contract` category remains reserved.

        # Step 4: shared naming-manifest divergence check (skipped when an
        # orchestrator does an aggregated check across the work-list — the
        # per-file check cannot converge against a manifest covering
        # identifiers in sibling modules).
        if not skip_naming_divergence_check:
            diagnostics.extend(naming_divergence_check(module_path, manifest_path))

        combined_raw = "\n".join(raw_segments)
        last_raw = combined_raw

        action, note = classify_action(history, diagnostics, source_lines, combined_raw)

        history.append(Attempt(
            iteration=iteration,
            diagnostics=diagnostics,
            action_taken=action,
            notes=note,
        ))
        last_diagnostics = diagnostics

        if reset_log:
            _try_reset_log()

        # Convergence check: zero diagnostics AND typecheck clean AND
        # test phase reports pass (or wasn't run because typecheck
        # already failed — but that branch is excluded by the prior
        # condition).
        if not diagnostics and rc_tc == 0 and test_passed:
            return LoopResult(
                status="ok",
                iterations=iteration,
                last_diagnostics=[],
                repair_history=history,
                summary=f"clerk typecheck + clerk test passed on iteration {iteration}.",
                regenerate_recommended=False,
            )

        # Halt on naming divergence — caller resolves before re-emitting.
        if any(d.category == "naming_divergence" for d in diagnostics):
            return LoopResult(
                status="unresolved",
                iterations=iteration,
                last_diagnostics=last_diagnostics,
                repair_history=history,
                summary=(
                    f"halted on naming-manifest divergence "
                    f"({sum(1 for d in diagnostics if d.category == 'naming_divergence')} "
                    f"mismatch(es)). Resolve in the source or manifest before retrying."
                ),
                regenerate_recommended=False,
            )

    # Fell off the loop without convergence.
    final_action = history[-1].action_taken if history else "patch"
    return LoopResult(
        status="unresolved",
        iterations=max_iterations,
        last_diagnostics=last_diagnostics,
        repair_history=history,
        summary=(
            f"max iterations ({max_iterations}) reached without convergence. "
            f"{len(last_diagnostics)} diagnostic(s) remaining; "
            f"latest action recommendation: {final_action}."
        ),
        regenerate_recommended=(final_action == "regenerate"),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_module_path(domain: str, module: str) -> Path:
    """Mirror xlator.py's resolve_paths convention for the Catala source
    file. The plan says the authored source post-pivot lives at
    `domains/<domain>/specs/<module>.catala_en`; pre-pivot the file is in
    `domains/<domain>/output/<module>.catala_en`. We prefer the specs
    path and fall back to output when the specs file is missing."""
    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        raise ClerkLoopError("DOMAINS_FULLPATH not set in environment.")
    base = Path(domains_root) / domain
    candidates = [
        base / "specs" / f"{module}.catala_en",
        # The plan's post-pivot convention is Catala-CamelCase filenames
        # (see SPIKE_RESULT.md lesson #1); accept both spellings.
        base / "specs" / f"{module.capitalize()}.catala_en",
        base / "output" / f"{module}.catala_en",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise ClerkLoopError(
        f"module file not found under {base}. Tried: "
        + ", ".join(str(c) for c in candidates)
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xlator clerk-loop",
        description=(
            "Drive clerk typecheck + clerk test against a Catala module, "
            "parse GNU-format diagnostics, run the naming-manifest "
            "divergence check, and report a structured outcome."
        ),
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument("module", help="Module name (e.g. eligibility)")
    parser.add_argument(
        "--max-iterations", type=int, default=_DEFAULT_MAX_ITERATIONS,
        help=f"Iteration cap (initial N=5; U9 calibrates).",
    )
    parser.add_argument(
        "--no-reset-log", action="store_true",
        help="Skip the inter-iteration catala_runtime.reset_log() call.",
    )
    parser.add_argument(
        "--module-path", default=None,
        help="Direct path to the .catala_en module (bypasses domain/module "
             "resolution; useful for test fixtures).",
    )
    args = parser.parse_args(argv)

    try:
        if args.module_path:
            module_path = Path(args.module_path)
            if not module_path.is_file():
                raise ClerkLoopError(f"module file not found: {module_path}")
        else:
            module_path = _resolve_module_path(args.domain, args.module)
    except ClerkLoopError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        print(_HEADER_SENTINEL)
        print(f"ERROR: {exc}")
        return 2

    try:
        result = run(
            module_path,
            max_iterations=args.max_iterations,
            reset_log=not args.no_reset_log,
        )
    except ClerkLoopError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        print(_HEADER_SENTINEL)
        print(f"ERROR: {exc}")
        return 2

    header = {
        "status": result.status,
        "iterations": result.iterations,
        "diagnostic_count": len(result.last_diagnostics),
        "regenerate_recommended": result.regenerate_recommended,
        "module": str(module_path),
    }
    print(json.dumps(header))
    print(_HEADER_SENTINEL)
    print(result.summary)
    if result.last_diagnostics:
        print()
        print(f"Diagnostics ({len(result.last_diagnostics)}):")
        for d in result.last_diagnostics:
            loc = f"{d.file}:{d.line}.{d.col}" if d.line else d.file
            print(f"  [{d.severity}/{d.category}] {loc}: {d.message}")
    if result.repair_history:
        print()
        print(f"Repair history ({len(result.repair_history)} iteration(s)):")
        for a in result.repair_history:
            note = f" — {a.notes}" if a.notes else ""
            print(f"  iter {a.iteration}: {a.action_taken}"
                  f" ({len(a.diagnostics)} diag){note}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
