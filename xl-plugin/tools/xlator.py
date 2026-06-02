#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0", "rich>=10.0", "pydantic>=2.0"]
# ///
"""
xlator CLI - replaces the Makefile.

Usage:
  xlator <action> [domain] [module]

Typical user actions (no domain/module):
  list                                 Show all domain/module pairs
  new-domain      <domain>             Scaffold standard domain directory structure
  ensure-guidance <domain>             Create specs/guidance/ and seed CLAUDE.md (idempotent)

  catala-test           <domain> <module>   Run Catala tests via Catala's clerk CLI
        Clerk runs the test fixtures under output/tests/ (mirrored from specs/tests/).
  catala-pipeline       <domain> <module>   copy-source-to-output -> clerk typecheck -> clerk test
  catala-demo           <domain> <module>   Start Catala-Python demo (foreground)

Slash command support actions:
  manifest-update <domain>             Refresh git SHAs in extraction-manifest.yaml
  detect-changes  <domain>             Exit 0 = no changes; exit 1 = changes detected
  convert-doc     <domain> <source-file> [--force-cleanup] [--no-cleanup]
        Convert a .docx or .pdf into a clean .md under input/policy_docs/ and
        archive the original under input/_originals/ with a diagnostics JSON.
  graph           <domain> <module>    Generate computation graph (via catala_depgraph.py)
  clerk-loop      <domain> <module>    Drive clerk typecheck + clerk test, parse diagnostics
  evaluate-catala <domain> <module> --inputs <path> [--scope <scope>]
        Evaluate a Catala scope against an inputs JSON file (preserves the JSON contract).
  preflight       <domain> <module> [--backend catala]   Validate Catala source exists and tool is in PATH

CSV test-case authoring:
  export-test-template  <domain> <module>   Generate CSV template from naming-manifest
  export-test-cases     <domain> <module>   Export existing _tests.yaml to CSV
  import-tests          <domain> <module> <file>  Import CSV/YAML test cases into _tests.yaml

"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import yaml
from pathlib import Path

from rich.console import Console
from rich.table import Table

CWD = Path.cwd()

DOMAINS_FULLPATH = Path(os.environ["DOMAINS_FULLPATH"])
SCRIPT_DIR_TOOLS = Path(__file__).parent

print(f"Using CWD={CWD}, DOMAINS_FULLPATH={DOMAINS_FULLPATH}, SCRIPT_DIR_TOOLS={SCRIPT_DIR_TOOLS}")

_console = Console()
_err_console = Console(stderr=True)


def _print_ok(msg):
    _console.print(f"[green]OK[/green] {msg}")


def _print_err(msg):
    _err_console.print(f"[red]ERR[/red] {msg}")


def _print_info(msg):
    _console.print(msg)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def resolve_paths(domain, module):
    base = DOMAINS_FULLPATH / domain
    return {
        "catala_source": base / "specs" / f"{module}.catala_en",
        "catala_output": base / "output" / f"{module}.catala_en",
        "tests":         base / "specs" / "tests" / f"{module}_tests.yaml",
        "package":       f"{domain}.{module}",
        "demo_catala_sh": base / "output" / f"demo-catala-{module}" / "start.sh",
    }


def require_file(path, label):
    if not path.exists():
        _print_err(f"{label} not found: {path}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Manifest helpers (shared by manifest-update and detect-changes)
# ---------------------------------------------------------------------------

def _manifest_path(domain):
    return DOMAINS_FULLPATH / domain / "specs" / "extraction-manifest.yaml"


def _get_file_sha(repo_relative_path):
    """Return git blob SHA of the file's current working-tree content, or None
    if the file is missing / unreadable.

    Uses `git hash-object` rather than `git log -1` so that uncommitted edits to
    tracked files produce a new SHA (the commit-based form would return the SHA
    of the file's last commit even after a working-tree edit, missing the change).
    """
    if not Path(repo_relative_path).exists():
        return None
    result = subprocess.run(
        ["git", "hash-object", repo_relative_path],
        capture_output=True, text=True, cwd=str(DOMAINS_FULLPATH),
    )
    return result.stdout.strip() or None


def _parse_source_doc(entry):
    """Return (domain_relative_path, stored_sha) from a manifest entry.

    Format: {path: "input/...", git_sha: "abc"}
    Paths are relative to DOMAINS_FULLPATH/<domain>/.
    """
    domain_rel = entry.get("path", "")
    stored_sha = entry.get("git_sha", "")
    return domain_rel, stored_sha


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------

def run(cmd, **kwargs):
    """Run a command. Exit 1 on non-zero return code."""
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def cmd_copy_source_to_output(domain, module):
    """Mirror specs/<module>.catala_en into output/<module>.catala_en.

    The AI-authored source lives under specs/;
    output/<module>.catala_en is a copy maintained by the build
    step so downstream consumers (catala_depgraph.py, FastAPI demo, clerk
    test runner) continue to read from output/ without learning the new
    source location.

    Any *.catala_en files under specs/ are mirrored (sibling sub-modules
    are copied alongside the requested module) so cross-module `> Using`
    directives resolve at clerk-test time. `clerk.toml` is also mirrored
    when present — clerk's ninja build reads it from CWD to resolve
    module names; without it, `clerk test` errors with `ninja: error:
    '<Module>@src' missing and no known rule to make it`.

    When `specs/tests/` contains `*.catala_en` files (emitted by
    `/catala-emit-tests` as of v14.0.0), they are mirrored to
    `output/tests/` so `clerk test` discovers them. YAML test files in
    `specs/tests/` are the SME-facing source and stay in `specs/`.
    """
    paths = resolve_paths(domain, module)
    require_file(paths["catala_source"], "Catala source")
    out_dir = paths["catala_output"].parent
    out_dir.mkdir(parents=True, exist_ok=True)
    specs_dir = paths["catala_source"].parent
    for src in specs_dir.glob("*.catala_en"):
        shutil.copy2(src, out_dir / src.name)
    clerk_toml = specs_dir / "clerk.toml"
    if clerk_toml.is_file():
        shutil.copy2(clerk_toml, out_dir / "clerk.toml")
    specs_tests_dir = specs_dir / "tests"
    if specs_tests_dir.is_dir():
        tests_out_dir = out_dir / "tests"
        tests_out_dir.mkdir(parents=True, exist_ok=True)
        for src in specs_tests_dir.glob("*.catala_en"):
            shutil.copy2(src, tests_out_dir / src.name)


def cmd_catala_typecheck(domain, module):
    """Run `clerk typecheck` on the copied Catala source under output/.

    The pre-step (cmd_copy_source_to_output) has placed the source plus
    any sibling sub-modules in output/, so the importer can resolve
    `> Using <Sub>` against output/<Sub>.catala_en.
    """
    paths = resolve_paths(domain, module)
    if not paths["catala_output"].exists():
        cmd_copy_source_to_output(domain, module)
    out_dir = paths["catala_output"].parent
    run(["clerk", "typecheck", paths["catala_output"].name], cwd=str(out_dir))


def cmd_demo(domain, module, backend):
    paths = resolve_paths(domain, module)
    sh = paths[f"demo_{backend}_sh"]
    if not sh.exists():
        _print_err(
            f"No demo script found at {sh}. "
            f"Create domains/{domain}/output/demo-{backend}-{module}/start.sh to enable the demo."
        )
        sys.exit(1)
    _print_info(f"Starting {backend} demo for {domain}/{module}...")
    run(["bash", str(sh)])


def cmd_graph(domain, module):
    """Generate the computation-graph artifacts from the Catala source.

    Reads the authored source at `specs/<module>.catala_en`. Falls back
    to the build copy at `output/<module>.catala_en` if the source isn't
    present (e.g. a consumer working from build artifacts only).
    """
    paths = resolve_paths(domain, module)
    catala_source = paths["catala_source"]
    if not catala_source.exists():
        catala_source = paths["catala_output"]
        if not catala_source.exists():
            _print_err(
                f"Catala source not found for {domain}/{module}. "
                f"Looked at: {paths['catala_source']} and {paths['catala_output']}. "
                f"Run `xlator catala-pipeline {domain} {module}` first."
            )
            sys.exit(1)
    run([sys.executable, str(SCRIPT_DIR_TOOLS / "catala_depgraph.py"), str(catala_source)])
    graph_json = catala_source.with_suffix(".graph.json")
    run([sys.executable, str(SCRIPT_DIR_TOOLS / "catala_depgraph.py"), str(graph_json), "--format", "mmd"])


def cmd_evaluate_catala(domain, module, inputs_path, scope):
    """Thin wrapper over catala_eval.main() — U3 deliverable.

    Forwards to the library's CLI entry point so the argparse + JSON
    contract live in one place (catala_eval.py). Mirrors the
    cmd_clerk_loop dispatch pattern.
    """
    sys.path.insert(0, str(SCRIPT_DIR_TOOLS))
    import catala_eval  # noqa: E402
    argv = [domain, module, "--inputs", inputs_path]
    if scope:
        argv.extend(["--scope", scope])
    sys.exit(catala_eval.main(argv))


def cmd_clerk_loop(domain, module, max_iterations, no_reset_log):
    """Thin wrapper over clerk_loop.main() — U2 deliverable.

    Forwards to the library's CLI entry point; clerk_loop owns argument
    parsing, output fencing, and exit-code semantics."""
    sys.path.insert(0, str(SCRIPT_DIR_TOOLS))
    import clerk_loop  # noqa: E402
    argv = [domain, module, "--max-iterations", str(max_iterations)]
    if no_reset_log:
        argv.append("--no-reset-log")
    sys.exit(clerk_loop.main(argv))


_SCOPE_DECL_RE = re.compile(r"^\s*declaration\s+scope\s+([A-Za-z_][A-Za-z0-9_]*)\s*:", re.MULTILINE)


def _derive_scope_name(catala_source_path: Path) -> str:
    """Return the name of the first `declaration scope <Name>:` in the
    Catala source."""
    text = catala_source_path.read_text(encoding="utf-8")
    m = _SCOPE_DECL_RE.search(text)
    if not m:
        _print_err(
            f"No `declaration scope <Name>:` found in {catala_source_path}. "
            f"Add a scope declaration in the catala-metadata fence."
        )
        sys.exit(1)
    return m.group(1)


def cmd_catala_test(domain, module):
    """Run clerk test in domains/<domain>/output/."""
    out_dir = DOMAINS_FULLPATH / domain / "output"
    if not out_dir.exists():
        _print_err(f"Output dir not found: {out_dir}")
        sys.exit(1)
    # cwd= is forwarded to subprocess.run via run()'s **kwargs
    run(["clerk", "test"], cwd=str(out_dir))


def cmd_catala_pipeline(domain, module):
    """copy-source-to-output → clerk typecheck → clerk test.

    The Catala source under `specs/` is the authored truth, including
    `specs/tests/*.catala_en` test fixtures emitted by `/catala-emit-tests`
    at authoring time. The copy step mirrors both the source modules and
    the test fixtures into `output/` so clerk discovers them; no transpile
    step runs at pipeline time.
    """
    _print_info(f"Catala pipeline: {domain}/{module}")
    cmd_copy_source_to_output(domain, module)
    cmd_catala_typecheck(domain, module)
    cmd_catala_test(domain, module)


def cmd_new_domain(domain):
    base = DOMAINS_FULLPATH / domain
    for d in [
        base / "input" / "policy_docs",
        base / "policy_facets",
        base / "specs",
        base / "output",
    ]:
        d.mkdir(parents=True, exist_ok=True)
    clerk_toml_default = (
        '[project]\n'
        'target_dir = "_targets"\n'
        'include_dirs = ["."]\n'
    )
    for clerk_dir in [base / "specs", base / "output"]:
        clerk_toml = clerk_dir / "clerk.toml"
        if not clerk_toml.exists():
            clerk_toml.write_text(clerk_toml_default)
    _print_ok(f"{base}/")
    _print_info(f"  input/policy_docs/    ← add .md policy documents here")
    _print_info(f"  policy_facets/        ← derived views of the policy docs (compressed/, etc.)")
    _print_info(f"  specs/                ← ruleset specs and guidance (guidance/ created on demand)")
    _print_info(f"  output/               ← generated Catala files and demo folder(s)")
    _print_info(f"  specs/clerk.toml, output/clerk.toml  ← clerk project markers (all clerk/catala commands cd here)")
    _print_info(
        f"\nDomain '{domain}' created. "
        f"Next: add policy docs to {base}/input/policy_docs/, then run /index-inputs "
        f"(which fans out parallel per-file workers that compress and extract each file)."
    )


def cmd_ensure_guidance(domain):
    """Create specs/guidance/ and seed CLAUDE.md from core/guidance_claude.md.

    Idempotent: skips the copy if CLAUDE.md is already present. Called by
    skills (e.g., /declare-target-ruleset, /refine-guidance) just before they
    write into specs/guidance/.
    """
    base = DOMAINS_FULLPATH / domain
    if not base.exists():
        _print_err(f"Domain not found: {base}/")
        sys.exit(1)
    guidance_dir = base / "specs" / "guidance"
    guidance_dir.mkdir(parents=True, exist_ok=True)
    guidance_src = SCRIPT_DIR_TOOLS.parent / "core" / "guidance_claude.md"
    guidance_dest = guidance_dir / "CLAUDE.md"
    if not guidance_dest.exists():
        shutil.copy2(guidance_src, guidance_dest)
        _print_ok(f"{guidance_dest.relative_to(DOMAINS_FULLPATH)} (created)")
    else:
        _print_ok(f"{guidance_dir.relative_to(DOMAINS_FULLPATH)}/ (already present)")


def cmd_preflight(domain, module, backend):
    domain_dir = DOMAINS_FULLPATH / domain
    if not domain_dir.exists():
        _print_err(f"Domain not found: {domain_dir}/")
        sys.exit(1)
    paths = resolve_paths(domain, module)
    require_file(paths["catala_source"], "Catala source")
    if backend == "catala" and shutil.which("clerk") is None:
        _print_err("clerk not found in PATH. Install the Catala toolchain to run tests.")
        sys.exit(1)
    label = f"{domain}/{module}" + (f" [{backend}]" if backend else "")
    _print_ok(f"preflight passed: {label}")


def cmd_manifest_update(domain):
    mpath = _manifest_path(domain)
    if not mpath.exists():
        _print_err(f"Extraction manifest not found: {mpath.relative_to(DOMAINS_FULLPATH)}")
        sys.exit(1)
    with open(mpath) as f:
        manifest = yaml.safe_load(f)

    def refresh(source_docs):
        updated = []
        for entry in source_docs:
            domain_rel, _ = _parse_source_doc(entry)
            sha = _get_file_sha(f"{DOMAINS_FULLPATH}/{domain}/{domain_rel}")
            if sha is None:
                _print_info(f"    [dim]dropped[/dim] {domain_rel} (file missing)")
                continue
            new_entry = {"path": domain_rel, "git_sha": sha}
            if "last_extracted" in entry:
                new_entry["last_extracted"] = entry["last_extracted"]
            updated.append(new_entry)
        return updated

    for prog_name, prog in (manifest.get("programs") or {}).items():
        _print_info(f"  {prog_name}")
        if prog.get("source_docs"):
            prog["source_docs"] = refresh(prog["source_docs"])
        for sub in prog.get("sub_modules") or []:
            if sub.get("source_docs"):
                sub["source_docs"] = refresh(sub["source_docs"])

    with open(mpath, "w") as f:
        f.write("# Auto-generated by /extract-ruleset — do not edit manually\n")
        yaml.dump(manifest, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    _print_ok(f"manifest updated: {mpath.relative_to(DOMAINS_FULLPATH)}")


def cmd_detect_changes(domain):
    """Exit 0 = no changes (nothing to do). Exit 1 = changes detected.

    Compares the blob SHA stored in extraction-manifest.yaml against the
    current working-tree blob SHA (`git hash-object`) for each source document.
    Detects both committed and uncommitted edits — any byte-level change to the
    source flips the SHA.
    """
    mpath = _manifest_path(domain)
    if not mpath.exists():
        _print_err(f"Extraction manifest not found: {mpath.relative_to(DOMAINS_FULLPATH)}")
        sys.exit(1)
    with open(mpath) as f:
        manifest = yaml.safe_load(f)

    def has_changes(source_docs):
        for entry in source_docs:
            domain_rel, stored_sha = _parse_source_doc(entry)
            current = _get_file_sha(f"{DOMAINS_FULLPATH}/{domain}/{domain_rel}")
            if current is None or current != stored_sha:
                return True
        return False

    for prog in (manifest.get("programs") or {}).values():
        if has_changes(prog.get("source_docs") or []):
            _print_info("Changes detected in source documents.")
            sys.exit(1)
        for sub in prog.get("sub_modules") or []:
            if has_changes(sub.get("source_docs") or []):
                _print_info("Changes detected in source documents.")
                sys.exit(1)

    _print_info("No changes detected.")
    sys.exit(0)



def cmd_list():
    pattern = str(DOMAINS_FULLPATH / "*" / "specs" / "*.catala_en")
    module_rows = []
    domains_with_modules = set()
    for path in sorted(glob.glob(pattern)):
        parts = Path(path).parts
        domain = parts[-3]
        module = parts[-1].removesuffix(".catala_en")
        module_rows.append((domain, module))
        domains_with_modules.add(domain)

    exclude_domains = {".shared", "guidance-examples"}
    domain_dirs = sorted(p.name for p in (DOMAINS_FULLPATH).iterdir() if p.is_dir() and p.name not in exclude_domains)
    initialized_only = [d for d in domain_dirs if d not in domains_with_modules]

    if not module_rows and not initialized_only:
        _print_info("No domains found under domains/")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Domain")
    table.add_column("Module")
    for domain, module in module_rows:
        table.add_row(domain, module)
    for domain in initialized_only:
        table.add_row(domain, "[dim]—[/dim]")
    _console.print(table)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="xlator",
        description="xlator CLI - run pipeline actions for any domain/module",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  xlator list
  xlator catala-test snap eligibility
  xlator catala-pipeline snap eligibility
        """,
    )
    sub = parser.add_subparsers(dest="action", required=True, metavar="action")

    for action, help_text in [
        ("catala-test",           "Run Catala tests via clerk test"),
        ("catala-demo",           "Start Catala-Python demo (foreground)"),
        ("graph",                 "Generate computation graph"),
        ("catala-pipeline",       "copy-source-to-output -> clerk typecheck -> clerk test"),
    ]:
        p = sub.add_parser(action, help=help_text)
        p.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
        p.add_argument("module", help="Module name (e.g. eligibility, apa_adltc)")

    # evaluate-catala: U3 — Catala-backed evaluator. Thin shim over
    # xl-plugin/tools/catala_eval.py.
    p_ec = sub.add_parser(
        "evaluate-catala",
        help="Evaluate a Catala scope against an inputs JSON file",
    )
    p_ec.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    p_ec.add_argument("module", help="Module name (e.g. eligibility)")
    p_ec.add_argument(
        "--inputs", required=True,
        help="Path to JSON file containing the inputs dict.",
    )
    p_ec.add_argument(
        "--scope", default=None,
        help="Scope name (CamelCase); default derives from module name.",
    )

    # clerk-loop: U2 — drive clerk typecheck + clerk test, parse GNU-format
    # diagnostics, run naming-manifest divergence check, report structured
    # outcome. Thin shim over xl-plugin/tools/clerk_loop.py.
    p_cl = sub.add_parser(
        "clerk-loop",
        help="Drive clerk typecheck + clerk test, parse diagnostics, report outcome",
    )
    p_cl.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    p_cl.add_argument("module", help="Module name (e.g. eligibility)")
    p_cl.add_argument(
        "--max-iterations", type=int, default=5,
        help="Iteration cap (initial N=5; U9 calibrates).",
    )
    p_cl.add_argument(
        "--no-reset-log", action="store_true",
        help="Skip the inter-iteration catala_runtime.reset_log() call.",
    )

    sub.add_parser("list",            help="Show all domain/module pairs")

    # Domain-only subcommands (no module arg)
    for action, help_text in [
        ("new-domain",      "Scaffold standard domain directory structure"),
        ("ensure-guidance", "Create specs/guidance/ and seed CLAUDE.md (idempotent)"),
        ("manifest-update", "Refresh git SHAs in extraction-manifest.yaml"),
        ("detect-changes",  "Exit 0 if no source doc changes; exit 1 if changes detected"),
    ]:
        p = sub.add_parser(action, help=help_text)
        p.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")

    # convert-doc: convert .docx / .pdf -> .md and archive the original
    p_cd = sub.add_parser(
        "convert-doc",
        help="Convert a .docx or .pdf into clean markdown for indexing",
    )
    p_cd.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    p_cd.add_argument("source", help="Path to .docx or .pdf source file")
    p_cd.add_argument(
        "--force-cleanup",
        action="store_true",
        help="Run cleanup even when the doc exceeds auto-cleanup thresholds.",
    )
    p_cd.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Skip cleanup entirely (used by hermetic tests).",
    )


    # Preflight: domain + module + optional backend
    p_pre = sub.add_parser("preflight", help="Validate domain, module, and tool prerequisites")
    p_pre.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    p_pre.add_argument("module", help="Module name (e.g. eligibility)")
    p_pre.add_argument(
        "--backend", choices=["catala"], default=None,
        help="Also check that the backend tool (clerk) is in PATH",
    )

    # CSV test case authoring
    p_ett = sub.add_parser("export-test-template",
                           help="Generate CSV template from naming manifest")
    p_ett.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    p_ett.add_argument("module", help="Module name (e.g. eligibility)")
    p_ett.add_argument("--output-dir", default=None,
                       help="Output directory (default: domains/<domain>/specs/tests/)")

    p_etc = sub.add_parser("export-test-cases",
                           help="Export existing test cases to CSV for review/editing")
    p_etc.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    p_etc.add_argument("module", help="Module name (e.g. eligibility)")
    p_etc.add_argument("--output-dir", default=None,
                       help="Output directory (default: domains/<domain>/specs/tests/)")
    p_etc.add_argument("--test-file", default=None,
                       help="Source _tests.yaml (default: domains/<domain>/specs/tests/<module>_tests.yaml)")

    p_it = sub.add_parser("import-tests",
                          help="Import test cases from CSV (or YAML) into _tests.yaml")
    p_it.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    p_it.add_argument("module", help="Module name (e.g. eligibility)")
    p_it.add_argument("input", help="Path to CSV or YAML file, or '-' for stdin")
    p_it.add_argument("--format", choices=["csv", "yaml"], default="csv",
                      help="Input format (default: csv; use yaml for direct YAML test case input)")
    p_it.add_argument("--test-file", default=None,
                      help="Target _tests.yaml (default: domains/<domain>/specs/tests/<module>_tests.yaml)")
    p_it.add_argument("--dry-run", action="store_true",
                      help="Validate and report without writing")
    p_it.add_argument("--no-comment-check", action="store_true",
                      help="Skip the comment-loss warning prompt (for non-interactive use)")
    p_it.add_argument("--output-format", choices=["text", "json"], default="text",
                      help="Error/result output format (default: text; use json for machine-parseable output)")

    args = parser.parse_args()

    match args.action:
        case "catala-test":
            cmd_catala_test(args.domain, args.module)
        case "catala-demo":
            cmd_demo(args.domain, args.module, "catala")
        case "graph":
            cmd_graph(args.domain, args.module)
        case "clerk-loop":
            cmd_clerk_loop(args.domain, args.module,
                           args.max_iterations, args.no_reset_log)
        case "evaluate-catala":
            cmd_evaluate_catala(args.domain, args.module,
                                args.inputs, args.scope)
        case "catala-pipeline":
            cmd_catala_pipeline(args.domain, args.module)
        case "list":
            cmd_list()
        case "new-domain":
            cmd_new_domain(args.domain)
        case "ensure-guidance":
            cmd_ensure_guidance(args.domain)
        case "preflight":
            cmd_preflight(args.domain, args.module, args.backend)
        case "manifest-update":
            cmd_manifest_update(args.domain)
        case "detect-changes":
            cmd_detect_changes(args.domain)
        case "convert-doc":
            extra: list[str] = []
            if args.force_cleanup:
                extra.append("--force-cleanup")
            if args.no_cleanup:
                extra.append("--no-cleanup")
            # Delegate to convert_doc.py via uv run so its inline script
            # dependencies (mammoth, pymupdf, anthropic) are auto-installed.
            run(
                [
                    "uv",
                    "run",
                    "--script",
                    str(SCRIPT_DIR_TOOLS / "convert_doc.py"),
                    args.domain,
                    args.source,
                    *extra,
                ]
            )
        case "export-test-template":
            out = args.output_dir or str(DOMAINS_FULLPATH / args.domain / "specs" / "tests")
            manifest_path = DOMAINS_FULLPATH / args.domain / "specs" / "naming-manifest.yaml"
            run([sys.executable, str(SCRIPT_DIR_TOOLS / "export_test_template.py"),
                 str(manifest_path),
                 "--module", args.module,
                 "--output-dir", out])
        case "export-test-cases":
            out = args.output_dir or str(DOMAINS_FULLPATH / args.domain / "specs" / "tests")
            tf = args.test_file or str(resolve_paths(args.domain, args.module)["tests"])
            manifest_path = DOMAINS_FULLPATH / args.domain / "specs" / "naming-manifest.yaml"
            run([sys.executable, str(SCRIPT_DIR_TOOLS / "export_test_cases.py"),
                 str(manifest_path), tf,
                 "--output-dir", out])
        case "import-tests":
            if args.test_file:
                tf = args.test_file
            elif args.input != "-":
                tests_dir = DOMAINS_FULLPATH / args.domain / "specs" / "tests"
                tf = str(tests_dir / (Path(args.input).stem + ".yaml"))
            else:
                tf = str(resolve_paths(args.domain, args.module)["tests"])
            extra = []
            if args.dry_run:
                extra += ["--dry-run"]
            if args.no_comment_check:
                extra += ["--no-comment-check"]
            if args.format != "csv":
                extra += ["--format", args.format]
            if args.output_format != "text":
                extra += ["--output-format", args.output_format]
            manifest_path = DOMAINS_FULLPATH / args.domain / "specs" / "naming-manifest.yaml"
            run([sys.executable, str(SCRIPT_DIR_TOOLS / "import_tests.py"),
                 str(manifest_path),
                 "--module", args.module,
                 args.input, tf, *extra])


if __name__ == "__main__":
    main()
