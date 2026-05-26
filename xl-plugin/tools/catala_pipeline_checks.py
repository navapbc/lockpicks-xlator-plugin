"""Pre-build staleness checks and post-build failure attribution for the Catala pipeline."""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class StaleReport:
    catala_file: Path
    reason: str  # "civil-newer" | "transpiler-newer"
    program: str


def stale_catala_files(
    output_dir: Path,
    specs_dir: Path,
    transpiler_path: Path,
) -> list[StaleReport]:
    """Return one StaleReport per stale .catala_en found in output_dir.

    Staleness vectors checked per file:
      civil-newer: the .civil.yaml source is newer than the .catala_en
      transpiler-newer: the transpiler script is newer than the .catala_en

    Pure calculation — takes paths, returns descriptions. No I/O beyond stat().
    """
    transpiler_mtime = transpiler_path.stat().st_mtime
    stale = []
    for catala_file in sorted(output_dir.glob("*.catala_en")):
        program = catala_file.stem
        spec_file = specs_dir / f"{program}.civil.yaml"
        catala_mtime = catala_file.stat().st_mtime
        if spec_file.exists() and spec_file.stat().st_mtime > catala_mtime:
            stale.append(StaleReport(catala_file, "civil-newer", program))
            continue
        if transpiler_mtime > catala_mtime:
            stale.append(StaleReport(catala_file, "transpiler-newer", program))
    return stale


# ---------------------------------------------------------------------------
# Post-build failure attribution
# ---------------------------------------------------------------------------

_ERROR_BLOCK_START = "┌─[ERROR]"
_ERROR_POINTER_RE = re.compile(r"├─➤ ([^:]+\.catala_en):")
_ERROR_BLOCK_END = "└─"


def _first_error_line(block: str) -> str:
    """Extract the first non-header content line from an error block."""
    for line in block.splitlines():
        stripped = line.lstrip("│ ").strip()
        if stripped and not stripped.startswith("[ERROR]") and not stripped.startswith("➤"):
            return stripped
    return block.splitlines()[0] if block else ""


def attribute_errors(ninja_stderr: str) -> dict[str, list[str]]:
    """Parse catala/ninja OCaml error output and group blocks by source module name.

    Returns a dict mapping bare module name (e.g. 'my_module') to a list of
    complete error block strings for that module. Blocks with no ├─➤ source
    pointer are silently dropped.

    Pure calculation — no I/O, no side effects.
    """
    by_module: dict[str, list[str]] = {}
    current_block: list[str] = []
    current_module: str | None = None

    for line in ninja_stderr.splitlines():
        if line.startswith(_ERROR_BLOCK_START):
            current_block = [line]
            current_module = None
        elif current_block and line.startswith(_ERROR_BLOCK_END):
            current_block.append(line)
            if current_module is not None:
                by_module.setdefault(current_module, []).append("\n".join(current_block))
            current_block = []
            current_module = None
        elif current_block:
            current_block.append(line)
            if current_module is None:
                pointer_match = _ERROR_POINTER_RE.search(line)
                if pointer_match:
                    raw_path = pointer_match.group(1)
                    current_module = raw_path.rsplit("/", 1)[-1].removesuffix(".catala_en")

    return by_module


def format_attribution_summary(
    requested_module: str,
    errors_by_module: dict[str, list[str]],
    output_artifacts: list[str],
) -> str:
    """Format a :::important attribution summary for a failed OCaml build.

    Distinguishes three cases:
      - errors_by_module is empty → returns '' (no summary needed)
      - requested_module not in errors_by_module → module compiled cleanly; siblings failed
      - requested_module in errors_by_module → module itself has errors (siblings may too)

    Pure calculation — no I/O, no side effects.
    """
    if not errors_by_module:
        return ""

    sibling_errors = {
        name: blocks
        for name, blocks in errors_by_module.items()
        if name != requested_module
    }
    requested_errors = errors_by_module.get(requested_module)

    lines: list[str] = [":::important"]

    if requested_errors is None:
        lines.append(
            f"Build failed — but your requested module ({requested_module}) compiled cleanly."
        )
        lines.append("")
        lines.append("Failure is in OTHER modules sharing the same Catala build target:")
        lines.append("")
        for module_name, blocks in sibling_errors.items():
            error_count = len(blocks)
            first_content = _first_error_line(blocks[0])
            lines.append(f"  {module_name} — {error_count} error(s):")
            lines.append(f"    {first_content}")
        if output_artifacts:
            lines.append("")
            lines.append("Your module's transpile artifacts are valid:")
            for artifact in output_artifacts:
                lines.append(f"  ✓ {artifact}")
    else:
        req_count = len(requested_errors)
        lines.append(
            f"Build failed. Your requested module ({requested_module}) has {req_count} error(s):"
        )
        lines.append("")
        first_req = _first_error_line(requested_errors[0])
        lines.append(f"  {first_req}")
        if sibling_errors:
            lines.append("")
            lines.append("Also failing (sibling modules):")
            lines.append("")
            for module_name, blocks in sibling_errors.items():
                error_count = len(blocks)
                first_content = _first_error_line(blocks[0])
                lines.append(f"  {module_name} — {error_count} error(s):")
                lines.append(f"    {first_content}")

    lines.append(":::")
    return "\n" + "\n".join(lines)
