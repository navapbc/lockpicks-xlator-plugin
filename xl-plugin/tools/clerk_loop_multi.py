#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
xlator clerk-loop-multi: orchestrate per-module clerk loops + aggregated
naming-manifest divergence check across the multi-module work-list.

Step 6 of /extract-ruleset and /update-ruleset becomes a shell-out to
this script: it loads the work-list, drives per-module clerk loops with
`skip_naming_divergence_check=True`, then runs the single aggregated
divergence check across every module's identifiers. Sequential
stop-on-first-failure orchestration mirrors the per-module
`xlator clerk-loop` primitive's contract.

CLI surface
-----------

    xlator clerk-loop-multi <domain> [<program>] [--max-iterations N]
                            [--no-reset-log] [--check-only]

`--check-only` skips the per-module clerk-loop pass entirely and runs
only the aggregated naming-manifest divergence check across the
work-list. Used by /update-ruleset Step 0 as a pre-edit gate; the full
pass is the default and is what /extract-ruleset Step 6 and
/update-ruleset Step 6 invoke.

Emits a JSON header line, the sentinel
`--- CLERK-LOOP-MULTI-HEADER-END ---`, then a human-readable summary.
Mirrors the per-tool JSON-header convention from `clerk_loop.py` and
`merge_naming_manifest.py`.

Exit codes:
    0 — status == "ok"   (all per-module loops + aggregated check passed)
    1 — status == "unresolved" (per-module typecheck/test failed, or
        aggregated divergence detected)
    2 — pre-flight failure (missing domain, missing naming-manifest,
        missing clerk, or load-context failure)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

# Sibling tools live next to this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import clerk_loop  # noqa: E402
import load_extraction_context  # noqa: E402


_HEADER_SENTINEL = "--- CLERK-LOOP-MULTI-HEADER-END ---"
_NAMING_MANIFEST_REL = "specs/naming-manifest.yaml"


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _emit_header(
    header: dict[str, Any],
    summary_lines: list[str],
    diagnostics: list[clerk_loop.Diagnostic] | None = None,
) -> None:
    """Single-line JSON header, sentinel, then human summary block.

    Matches the layout of `clerk_loop.main()` and
    `merge_naming_manifest.run()`."""
    print(json.dumps(header))
    print(_HEADER_SENTINEL)
    for line in summary_lines:
        print(line)
    if diagnostics:
        print()
        print(f"Diagnostics ({len(diagnostics)}):")
        for d in diagnostics:
            loc = f"{d.file}:{d.line}.{d.col}" if d.line else d.file
            print(f"  [{d.severity}/{d.category}] {loc}: {d.message}")


def _emit_error(message: str, exit_code: int) -> dict[str, Any]:
    header = {
        "status": "error",
        "error": message,
        "warnings": [],
    }
    _emit_header(header, [f"ERROR: {message}"])
    return header


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(
    domain_dir: Path,
    program_arg: str | None,
    *,
    max_iterations: int = clerk_loop._DEFAULT_MAX_ITERATIONS,
    reset_log: bool = True,
    check_only: bool = False,
) -> int:
    """Sequential per-module clerk loops + post-pass aggregated divergence
    check. When `check_only=True`, skips the per-module pass and runs
    only the aggregated divergence check (used by /update-ruleset Step 0
    as a pre-edit gate). Returns the same exit codes documented in the
    module docstring."""
    warnings: list[str] = []
    mode = "check_only" if check_only else "full"

    # 1. Load the work-list via the library API exposed by
    # load_extraction_context's refactor.
    try:
        payload = load_extraction_context.load_context(
            domain_dir, program_arg, mode="extract",
        )
    except load_extraction_context.LoadContextError as exc:
        _emit_error(str(exc), exc.exit_code)
        return exc.exit_code
    work_list: list[dict[str, Any]] = payload.get("work_list", [])
    warnings.extend(payload.get("warnings", []))

    # 2. Resolve manifest path directly (Step 6 cannot run without it).
    manifest_path = domain_dir / _NAMING_MANIFEST_REL
    if not manifest_path.is_file():
        _emit_error(
            f"missing required naming-manifest: {manifest_path}",
            exit_code=2,
        )
        return 2

    module_paths: list[Path] = []
    for entry in work_list:
        rel = entry.get("catala_file")
        if isinstance(rel, str):
            module_paths.append(domain_dir / rel)

    # 3. Per-module clerk loops (only `generate` entries; skipped in
    # check-only mode).
    verified_modules: list[str] = []
    iterations_per_module: list[dict[str, Any]] = []
    failed_module: str | None = None
    per_module_diagnostics: list[clerk_loop.Diagnostic] = []
    per_module_summary: str = ""

    if not check_only:
        for entry in work_list:
            if entry.get("action") != "generate":
                continue
            name = entry.get("name", "<unnamed>")
            if not isinstance(name, str):
                name = "<unnamed>"
            module_path = domain_dir / entry["catala_file"]
            try:
                result = clerk_loop.run(
                    module_path,
                    max_iterations=max_iterations,
                    reset_log=reset_log,
                    skip_naming_divergence_check=True,
                )
            except clerk_loop.ClerkLoopError as exc:
                failed_module = name
                per_module_summary = (
                    f"per-module clerk loop on {name} raised "
                    f"ClerkLoopError: {exc}"
                )
                break
            iterations_per_module.append(
                {"module": name, "iterations": result.iterations}
            )
            if result.status != "ok":
                failed_module = name
                per_module_diagnostics = list(result.last_diagnostics)
                per_module_summary = result.summary
                break
            verified_modules.append(name)

    if failed_module is not None:
        header = {
            "status": "unresolved",
            "mode": mode,
            "modules_checked": len(module_paths),
            "modules_generated": len(verified_modules),
            "iterations_per_module": iterations_per_module,
            "failed_module": failed_module,
            "verified_modules": verified_modules,
            "diagnostic_count": len(per_module_diagnostics),
            "warnings": warnings,
        }
        _emit_warnings_stderr(warnings)
        summary_lines = [
            f"halted on per-module clerk loop for {failed_module}.",
            per_module_summary or "(no further detail)",
        ]
        if verified_modules:
            summary_lines.append(
                f"Verified before halt: {', '.join(verified_modules)}."
            )
        _emit_header(header, summary_lines, per_module_diagnostics)
        return 1

    # 4. Aggregated divergence check across ALL work-list modules.
    aggregated_diagnostics = clerk_loop.naming_divergence_check_aggregated(
        manifest_path, module_paths, warnings_out=warnings,
    )

    if aggregated_diagnostics:
        status = "unresolved"
        exit_code = 1
        if check_only:
            summary_lines = [
                f"--check-only: aggregated naming-manifest divergence "
                f"check surfaced {len(aggregated_diagnostics)} "
                f"diagnostic(s) across {len(module_paths)} module(s). "
                f"Resolve in the Catala source(s) or the manifest before "
                f"continuing.",
            ]
        else:
            summary_lines = [
                f"per-module clerk loops passed on "
                f"{len(verified_modules)}/{len(module_paths)} module(s); "
                f"aggregated naming-manifest divergence check surfaced "
                f"{len(aggregated_diagnostics)} diagnostic(s). Resolve in the "
                f"Catala source(s) or the manifest before retrying.",
            ]
    else:
        status = "ok"
        exit_code = 0
        if check_only:
            summary_lines = [
                f"--check-only: aggregated naming-manifest check passed "
                f"across {len(module_paths)} module(s)."
            ]
        else:
            summary_lines = [
                f"All {len(module_paths)} module(s) verified; "
                f"aggregated naming-manifest check passed."
            ]

    header = {
        "status": status,
        "mode": mode,
        "modules_checked": len(module_paths),
        "modules_generated": len(verified_modules),
        "iterations_per_module": iterations_per_module,
        "failed_module": None,
        "verified_modules": verified_modules,
        "diagnostic_count": len(aggregated_diagnostics),
        "warnings": warnings,
    }
    _emit_warnings_stderr(warnings)
    _emit_header(header, summary_lines, aggregated_diagnostics)
    return exit_code


def _emit_warnings_stderr(warnings: list[str]) -> None:
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xlator clerk-loop-multi",
        description=(
            "Drive per-module clerk loops and run an aggregated "
            "naming-manifest divergence check across the multi-module "
            "work-list. Replaces /extract-ruleset and /update-ruleset's "
            "Step 6 per-file divergence pass (which cannot converge "
            "against a manifest shared across sibling modules)."
        ),
    )
    parser.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    parser.add_argument(
        "program", nargs="?", default=None,
        help="Optional program name (auto-detected from "
             "ruleset-modules.yaml's role: main entry, or from a single "
             "specs/*.catala_en file when unambiguous).",
    )
    parser.add_argument(
        "--max-iterations", type=int,
        default=clerk_loop._DEFAULT_MAX_ITERATIONS,
        help=f"Iteration cap for each per-module clerk loop "
             f"(default {clerk_loop._DEFAULT_MAX_ITERATIONS}).",
    )
    parser.add_argument(
        "--no-reset-log", action="store_true",
        help="Skip the inter-iteration catala_runtime.reset_log() call.",
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="Skip the per-module clerk-loop pass; run only the aggregated "
             "naming-manifest divergence check across the work-list. Used "
             "by /update-ruleset Step 0 as a pre-edit gate.",
    )
    args = parser.parse_args(argv)
    if args.check_only and args.max_iterations != clerk_loop._DEFAULT_MAX_ITERATIONS:
        print(
            "WARN: --max-iterations is ignored with --check-only (no per-module "
            "loops fire).",
            file=sys.stderr,
        )

    domains_root = os.environ.get("DOMAINS_FULLPATH")
    if not domains_root:
        _emit_error(
            "DOMAINS_FULLPATH not set in environment.",
            exit_code=2,
        )
        return 2

    domain_dir = Path(domains_root) / args.domain
    if not domain_dir.is_dir():
        _emit_error(
            f"domain directory not found: {domain_dir}",
            exit_code=2,
        )
        return 2

    return run(
        domain_dir,
        args.program,
        max_iterations=args.max_iterations,
        reset_log=not args.no_reset_log,
        check_only=args.check_only,
    )


if __name__ == "__main__":
    sys.exit(main())
