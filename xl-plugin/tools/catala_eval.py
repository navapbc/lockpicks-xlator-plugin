#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""
Catala-backed evaluator.

A thin wrapper around `catala interpret --output-format=json --input=<json>`
exposing a JSON contract:

    {
      "outputs":  {...},
      "computed": {...},
      "reasons":  [...],
      "debug":    {"rules_fired": [...], "command": [...], ...}
    }

Consumed by `/expand-tests` Phase 2b/2c/2d, `/create-tests` Step 1, and
`/detect-stale-cases` via `xlator evaluate-catala <domain> <program> --inputs <tmpfile>`.

Library API
-----------

    from catala_eval import run, EvaluationResult, EvaluationError

    result = run(
        Path("domains/snap/specs/Eligibility.catala_en"),
        scope="Eligibility",
        inputs={"household": {"monthly_gross_income": 1000.0, ...}},
    )
    # result.outputs   → {"is_eligible": True, ...}
    # result.computed  → {"Eligibility.federal_poverty_line": 1704.00, ...}
    # result.reasons   → []
    # result.debug     → {"command": [...], "scope": "Eligibility", ...}

Granularity
-----------

Scope-level: one `catala interpret` invocation per `run()` call. Per-rule
signals surface through `EvaluationResult.computed` from the trace. If a
consumer needs per-rule outcomes, compose multiple `run()` calls — see U9
verification report for the empirical comparison plan.

`debug["wall_time_ms"]` records per-call wall time for downstream timing
aggregation.

CLI surface
-----------

    xlator evaluate-catala <domain> <module> --inputs <path> [--scope <scope>]

JSON-only stdout (no header sentinel — library-style output for
downstream consumers, not a user-facing summary). Exit 0 on success, 1
on evaluation error, 2 on pre-flight failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Strip ANSI color escape sequences from any embedded prose so cross-run
# canonicalization is stable (catala 1.1.0 may color "ERROR" tags).
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ---------------------------------------------------------------------------
# Errors + result records
# ---------------------------------------------------------------------------


class EvaluationError(Exception):
    """Raised when Catala evaluation fails. Carries structured `{context, message}`."""

    def __init__(self, context: str, message: str):
        self.context = context
        self.message = message
        super().__init__(f"{context}: {message}" if context else message)


@dataclass
class EvaluationResult:
    """Result of one `catala interpret` call, rendered as the JSON contract documented in the module docstring."""

    outputs: dict[str, Any] = field(default_factory=dict)
    computed: dict[str, Any] = field(default_factory=dict)
    reasons: list[dict] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "outputs": _canonicalize(self.outputs),
            "computed": _canonicalize(self.computed),
            "reasons": _canonicalize(self.reasons),
            "debug": _canonicalize(self.debug),
        }


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def _strip_ansi(value: Any) -> Any:
    if isinstance(value, str):
        return _ANSI_ESCAPE_RE.sub("", value)
    return value


def _canonicalize(value: Any) -> Any:
    """Recursively normalize a JSON-shaped value so repeated invocations
    produce byte-identical output: dict keys are sorted, list ordering is
    preserved (trace ordering is deterministic for valid programs), ANSI
    escapes are stripped from any embedded string."""
    if isinstance(value, dict):
        return {k: _canonicalize(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return [_canonicalize(v) for v in value]
    return _strip_ansi(value)


# ---------------------------------------------------------------------------
# Catala invocation
# ---------------------------------------------------------------------------


def _require_catala() -> str:
    """Return the absolute path to `catala`, or raise EvaluationError with
    install hint."""
    path = shutil.which("catala")
    if path is None:
        raise EvaluationError(
            "catala",
            "catala not found in PATH. Install the Catala toolchain "
            "(see https://catala-lang.org/) or activate the OPAM switch "
            "that provides it (e.g. `opam install catala`).",
        )
    return path


def _build_command(catala_path: Path, scope: str, inputs_str: str) -> list[str]:
    """Construct the `catala interpret` command. Inputs are passed as a
    JSON string via --input (catala 1.1.0 accepts both file and inline
    JSON for that flag — we use inline so we don't litter the working
    tree with tmp files)."""
    return [
        "catala",
        "interpret",
        "--scope",
        scope,
        "--input",
        inputs_str,
        "--output-format=json",
        catala_path.name,
    ]


def _parse_scope_result(stdout: str) -> dict[str, Any]:
    """Extract the trailing JSON object emitted by `catala interpret
    --output-format=json`. The interpreter prints a single line like
    `{ "is_eligible": true }`; when --trace is enabled, a JSON trace
    array precedes it. v1 doesn't enable --trace, so the entire stdout
    should be parseable as a single JSON object."""
    text = stdout.strip()
    if not text:
        return {}
    # Find the final balanced JSON object — `catala interpret` always
    # emits the scope result last.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall back to scanning from the last `{` — handles trace
        # prefixes if they ever surface unexpectedly.
        last_brace = text.rfind("{")
        if last_brace < 0:
            raise EvaluationError(
                "catala interpret",
                f"could not parse JSON from output:\n{stdout[:500]}",
            )
        try:
            return json.loads(text[last_brace:])
        except json.JSONDecodeError as exc:
            raise EvaluationError(
                "catala interpret",
                f"could not parse JSON tail from output: {exc}\n{stdout[:500]}",
            ) from exc


# ---------------------------------------------------------------------------
# Library entry point
# ---------------------------------------------------------------------------


def run(
    catala_path: Path | str,
    scope: str,
    inputs: dict,
    *,
    include_dirs: list[Path] | None = None,
) -> EvaluationResult:
    """Evaluate a Catala scope against an inputs dict.

    Parameters
    ----------
    catala_path : path to the .catala_en module containing the scope.
    scope       : scope name (CamelCase), e.g. "Eligibility".
    inputs      : JSON-shaped input dict; the top-level keys must match
                  the scope's `input` variables. Catala will validate the
                  shape and raise on mismatch (surfaced as EvaluationError).
    include_dirs : extra `--include` directories for sub-module
                   resolution. Defaults to None.

    Returns
    -------
    EvaluationResult preserving the {outputs, computed, reasons, debug}
    contract.

    Raises
    ------
    EvaluationError on:
      - missing `catala` on PATH,
      - missing/unreadable .catala_en file,
      - scope not found in the program,
      - input validation failure (missing field, wrong type),
      - any non-zero `catala interpret` exit.
    """
    _require_catala()

    catala_path = Path(catala_path)
    if not catala_path.is_file():
        raise EvaluationError(
            "preflight", f"Catala source file not found: {catala_path}"
        )

    if not isinstance(inputs, dict):
        raise EvaluationError(
            "inputs",
            f"inputs must be a dict, got {type(inputs).__name__}",
        )

    inputs_json = json.dumps(inputs)
    cmd = _build_command(catala_path, scope, inputs_json)
    if include_dirs:
        for inc in include_dirs:
            cmd.extend(["--include", str(inc)])

    try:
        from clerk_loop import ensure_catala_bootstrap
        ensure_catala_bootstrap(catala_path.parent)
    except ImportError:
        pass

    start = time.perf_counter()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(catala_path.parent),
    )
    wall_time_ms = int((time.perf_counter() - start) * 1000)

    debug: dict[str, Any] = {
        "command": cmd,
        "scope": scope,
        "wall_time_ms": wall_time_ms,
        "returncode": proc.returncode,
    }

    if proc.returncode != 0:
        stderr_clean = _ANSI_ESCAPE_RE.sub("", proc.stderr or "")
        stdout_clean = _ANSI_ESCAPE_RE.sub("", proc.stdout or "")
        combined = stderr_clean.strip() or stdout_clean.strip()
        # Surface scope-not-found and missing-input errors with their
        # diagnostic context so consumer skills can dispatch on
        # exception.message without re-parsing the catala output.
        raise EvaluationError("catala interpret", combined or "non-zero exit with no output")

    outputs = _parse_scope_result(proc.stdout)

    return EvaluationResult(
        outputs=outputs,
        # v1 leaves `computed` empty when --trace is off; U9 enables
        # trace-backed population once the per-rule semantics
        # verification settles (see deferred sub-deliverable A in the
        # module docstring).
        computed={},
        reasons=[],
        debug=debug,
    )


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _derive_scope_from_module(module: str) -> str:
    """Convert module name (e.g. `eligibility`, `apa_adltc`) to CamelCase
    scope name (`Eligibility`, `ApaAdltc`)."""
    parts = re.split(r"[_\-]+", module)
    return "".join(p.capitalize() for p in parts if p)


class _PreflightError(Exception):
    """Raised by `_preflight` when required paths are missing. Mapped to
    exit-code 2 by `main()`; library callers don't see this — they get
    `EvaluationError` from `run()` instead."""


def _preflight(domain_dir: Path, catala_path: Path, inputs_path: Path) -> None:
    """Verify all required paths exist. Raises _PreflightError on miss
    so main() can return exit-code 2 cleanly."""
    if not domain_dir.is_dir():
        raise _PreflightError(f"domain directory not found: {domain_dir}")
    if not catala_path.is_file():
        raise _PreflightError(f"Catala source not found: {catala_path}")
    if not inputs_path.is_file():
        raise _PreflightError(f"inputs file not found: {inputs_path}")


def _resolve_catala_path(domain_dir: Path, module: str) -> Path:
    """Resolve to a `.catala_en` source under the domain. Prefer
    `output/<module>.catala_en` (post-U9 the generated source lives
    there); fall back to `specs/<module>.catala_en` for pre-U9 hand-
    authored sources. Also accepts the Catala-CamelCase filename
    variant (SPIKE_RESULT.md lesson #1)."""
    camel = _derive_scope_from_module(module)
    candidates = [
        domain_dir / "output" / f"{module}.catala_en",
        domain_dir / "output" / f"{camel}.catala_en",
        domain_dir / "specs" / f"{module}.catala_en",
        domain_dir / "specs" / f"{camel}.catala_en",
    ]
    for c in candidates:
        if c.is_file():
            return c
    # Return the first candidate so _preflight emits an actionable error.
    return candidates[0]


def cmd_evaluate_catala(
    domain_dir: Path,
    module: str,
    inputs_path: Path,
    scope: str | None,
) -> dict:
    """Evaluate one Catala scope against the supplied inputs and return
    the result as a JSON-serializable dict. Raises _PreflightError when
    paths are missing (mapped to exit-code 2 by main()) or
    EvaluationError on evaluation failure (mapped to exit-code 1)."""
    catala_path = _resolve_catala_path(domain_dir, module)
    _preflight(domain_dir, catala_path, inputs_path)

    with inputs_path.open(encoding="utf-8") as f:
        inputs = json.load(f)
    if not isinstance(inputs, dict):
        raise EvaluationError(
            "inputs", f"inputs file must be a JSON object: {inputs_path}"
        )

    effective_scope = scope or _derive_scope_from_module(module)
    result = run(catala_path, effective_scope, inputs)
    return result.as_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xlator evaluate-catala",
        description=(
            "Evaluate a Catala scope against an inputs JSON file. "
            "Preserves the JSON contract historically served by "
            "the legacy `evaluate` command so /expand-tests, /detect-stale-cases, and "
            "/create-tests consume the same shape."
        ),
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument(
        "module",
        help="Module name (matches output/<module>.catala_en or specs/<module>.catala_en)",
    )
    parser.add_argument(
        "--inputs",
        required=True,
        help="Path to JSON file containing the inputs dict.",
    )
    parser.add_argument(
        "--scope",
        default=None,
        help=(
            "Scope name (CamelCase). Default: derived from module name "
            "(e.g. `eligibility` -> `Eligibility`, `apa_adltc` -> `ApaAdltc`)."
        ),
    )
    args = parser.parse_args(argv)

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        print("Error: DOMAINS_FULLPATH not set in environment.", file=sys.stderr)
        return 2

    domain_dir = Path(domains_root) / args.domain
    inputs_path = Path(args.inputs)

    try:
        result = cmd_evaluate_catala(domain_dir, args.module, inputs_path, args.scope)
    except _PreflightError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except EvaluationError as exc:
        print(f"Evaluation error: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"JSON parse error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
