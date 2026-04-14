#!/usr/bin/env python3
"""
xlator CLI - replaces the Makefile.

Usage:
  ./xlator <action> [domain] [module]

Typical user actions (no domain/module):
  list                                 Show all domain/module pairs
  new-domain      <domain>             Scaffold standard domain directory structure

  catala-transpile      <domain> <module>   Generate Catala from CIVIL
  catala-test-transpile <domain> <module>   Generate Catala test file from YAML tests
  catala-test           <domain> <module>   Run Catala tests via Catala's clerk CLI
        Clerk runs the transpiled tests under output/tests/.
  catala-pipeline       <domain> <module>   validate -> catala-transpile -> catala-test-transpile -> catala-test
  catala-demo           <domain> <module>   Start Catala-Python demo (foreground)

  rego-transpile        <domain> <module>   Generate Rego from CIVIL
  rego-test             <domain> <module>   Start OPA, run tests, stop OPA
        Reads a CIVIL specs/tests/<module>_tests.yaml file and submits each test case
        to the OPA REST server, reporting pass/fail per case.
  rego-pipeline         <domain> <module>   validate -> rego-transpile -> rego-test (OPA/Rego)
  rego-demo             <domain> <module>   Start OPA + FastAPI demo (foreground)

Slash command support actions:
  manifest-update <domain>             Refresh git SHAs in extraction-manifest.yaml
  detect-changes  <domain>             Exit 0 = no changes; exit 1 = changes detected
  validate        <domain> <module>    Validate CIVIL YAML
  graph           <domain> <module>    Generate computation graph
  preflight       <domain> <module> [--backend rego|catala]   Validate CIVIL file exists and tool is in PATH

Observability actions:
  diff-report     <domain>             Show AI-vs-user diffs from session log (read-only)
  tidy-log        <domain>             Render session log as Markdown conversation

CSV test-case authoring:
  export-test-template  <domain> <module>   Generate CSV template from CIVIL spec
  export-test-cases     <domain> <module>   Export existing _tests.yaml to CSV
  import-tests          <domain> <module> <file>  Import CSV/YAML test cases into _tests.yaml

"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import yaml
from pathlib import Path

from rich.console import Console
from rich.table import Table

CWD = Path.cwd()

assert "PROJECT_ROOT" in os.environ, "PROJECT_ROOT must be set to the repository root"
ROOT = Path(os.environ.get("PROJECT_ROOT", os.getcwd()))

assert "DOMAINS_DIR" in os.environ, "DOMAINS_DIR must be set to the directory containing domain subfolders (relative to ROOT)"
DOMAINS_DIR = Path(os.environ.get("DOMAINS_DIR", "domains"))

PLUGIN_DIR = Path(__file__).parent
SCRIPT_DIR_TOOLS = PLUGIN_DIR / "tools"

print(f"Using ROOT={ROOT}, DOMAINS_DIR={DOMAINS_DIR}, PLUGIN_DIR={PLUGIN_DIR}, CWD={CWD}")

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
    base = ROOT / DOMAINS_DIR / domain
    return {
        "civil":    base / "specs" / f"{module}.civil.yaml",
        "rego":     base / "output" / f"{module}.rego",
        "catala":   base / "output" / f"{module}.catala_en",
        "tests":    base / "specs" / "tests" / f"{module}_tests.yaml",
        "package":  f"{domain}.{module}",
        "opa_path": f"/v1/data/{domain}/{module}/decision",
        "demo_rego_sh":   base / "output" / f"demo-rego-{module}" / "start.sh",
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
    return ROOT / DOMAINS_DIR / domain / "specs" / "extraction-manifest.yaml"


def _get_file_sha(repo_relative_path):
    """Return current HEAD git SHA for a file, or None if not tracked/committed."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", repo_relative_path],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    return result.stdout.strip() or None


def _parse_source_doc(entry, domain):
    """Return (domain_relative_path, stored_sha) from either manifest format.

    snap format:   {path: "input/...",                git_sha: "abc"}
    ak_doh format: {file: "domains/ak_doh/input/...", sha:     "abc"}
    """
    raw = entry.get("path") or entry.get("file", "")
    prefix = f"{DOMAINS_DIR}/{domain}/"
    domain_rel = raw[len(prefix):] if raw.startswith(prefix) else raw
    stored_sha = entry.get("git_sha") or entry.get("sha") or ""
    return domain_rel, stored_sha


# ---------------------------------------------------------------------------
# OPA lifecycle
# ---------------------------------------------------------------------------

def start_opa(rego_path, port=8181):
    """Start OPA server as a subprocess. Poll health endpoint. Return Popen."""
    proc = subprocess.Popen(
        ["opa", "run", "--server", "--addr", f":{port}", str(rego_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    health_url = f"http://localhost:{port}/health"
    for _ in range(10):
        try:
            urllib.request.urlopen(health_url, timeout=1)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    _print_err(
        f"OPA failed to start within 5 seconds. "
        f"Port {port} may already be in use, or OPA is not installed."
    )
    sys.exit(1)


def stop_opa(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


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

def cmd_validate(domain, module):
    paths = resolve_paths(domain, module)
    require_file(paths["civil"], "CIVIL spec")
    run([sys.executable, str(SCRIPT_DIR_TOOLS / "validate_civil.py"), str(paths["civil"])])


def cmd_transpile(domain, module):
    paths = resolve_paths(domain, module)
    require_file(paths["civil"], "CIVIL spec")
    paths["rego"].parent.mkdir(parents=True, exist_ok=True)
    run([
        sys.executable, str(SCRIPT_DIR_TOOLS / "transpile_to_rego.py"),
        str(paths["civil"].relative_to(CWD)),
        str(paths["rego"].relative_to(CWD)),
        "--package", paths["package"],
    ], cwd=str(CWD))


def _get_invoke_modules(civil_path: Path) -> list[str]:
    """Return list of sub-module names referenced by invoke: fields in a CIVIL file."""
    import yaml as _yaml
    try:
        with open(civil_path) as f:
            doc = _yaml.safe_load(f)
    except Exception:
        return []
    result = []
    for field_def in (doc.get("computed") or {}).values():
        if isinstance(field_def, dict) and field_def.get("module") and field_def.get("invoke"):
            name = field_def["module"]
            if name not in result:
                result.append(name)
    return result


def cmd_catala_transpile(domain, module):
    paths = resolve_paths(domain, module)
    require_file(paths["civil"], "CIVIL spec")

    # Transpile sub-module dependencies first (dependency order)
    for sub_module in _get_invoke_modules(paths["civil"]):
        _print_info(f"  → Transpiling dependency: {domain}/{sub_module}")
        cmd_catala_transpile(domain, sub_module)

    paths["catala"].parent.mkdir(parents=True, exist_ok=True)
    from tools.transpile_to_catala import derive_scope_name, load_civil
    doc = load_civil(str(paths["civil"]))
    scope_name = derive_scope_name(doc.get("module", module))
    run([
        sys.executable, str(SCRIPT_DIR_TOOLS / "transpile_to_catala.py"),
        str(paths["civil"].resolve().relative_to(CWD.resolve())),
        str(paths["catala"].resolve().relative_to(CWD.resolve())),
        "--scope", scope_name,
    ], cwd=str(CWD))


def cmd_test(domain, module):
    paths = resolve_paths(domain, module)
    require_file(paths["rego"], "Rego file (run rego-transpile first)")
    require_file(paths["tests"], "Test cases")
    _print_info(f"Starting OPA server with {paths['rego'].name}...")
    opa = start_opa(paths["rego"])
    _print_ok("OPA ready")
    sys.stdout.flush()
    try:
        result = subprocess.run([
            sys.executable, str(SCRIPT_DIR_TOOLS / "rego-run_tests.py"),
            str(paths["tests"]),
            "--opa-path", paths["opa_path"],
        ])
        sys.exit(result.returncode)
    finally:
        stop_opa(opa)
        _print_info("OPA stopped")


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
    paths = resolve_paths(domain, module)
    require_file(paths["civil"], "CIVIL spec")
    run([sys.executable, str(SCRIPT_DIR_TOOLS / "computation_graph.py"), str(paths["civil"])])


def cmd_catala_test_transpile(domain, module):
    paths = resolve_paths(domain, module)
    require_file(paths["civil"], "CIVIL spec")
    from tools.transpile_to_catala import derive_scope_name, load_civil
    doc = load_civil(str(paths["civil"]))
    scope_name = derive_scope_name(doc.get("module", module))
    domain_base = ROOT / DOMAINS_DIR / domain
    tests_dir = domain_base / "specs" / "tests"
    out_dir = domain_base / "output" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    import glob as _glob
    pattern = str(tests_dir / f"{module}*_tests.yaml")
    test_files = sorted(_glob.glob(pattern))
    if not test_files:
        _print_err(f"No test files matching {pattern}")
        sys.exit(1)
    for tests_yaml in test_files:
        stem = Path(tests_yaml).stem  # e.g. eligibility_tests
        out_catala = out_dir / f"{stem}.catala_en"
        run([
            sys.executable, str(SCRIPT_DIR_TOOLS / "transpile_to_catala_tests.py"),
            str(Path(tests_yaml).resolve().relative_to(CWD.resolve())),
            str(out_catala.resolve().relative_to(CWD.resolve())),
            "--scope", scope_name,
            "--civil-spec", str(paths["civil"].resolve().relative_to(CWD.resolve())),
        ], cwd=str(CWD))


def cmd_catala_test(domain, module):
    """Run clerk test in domains/<domain>/output/."""
    out_dir = ROOT / DOMAINS_DIR / domain / "output"
    if not out_dir.exists():
        _print_err(f"Output dir not found: {out_dir}")
        sys.exit(1)
    # cwd= is forwarded to subprocess.run via run()'s **kwargs
    run(["clerk", "test"], cwd=str(out_dir))


def cmd_catala_pipeline(domain, module):
    """validate → catala-transpile → catala-test-transpile → catala-test."""
    _print_info(f"Catala pipeline: {domain}/{module}")
    cmd_validate(domain, module)
    cmd_catala_transpile(domain, module)
    cmd_catala_test_transpile(domain, module)
    cmd_catala_test(domain, module)


def cmd_pipeline(domain, module):
    """validate → rego-transpile → rego-test. Stops on first failure."""
    _print_info(f"Pipeline: {domain}/{module}")
    cmd_validate(domain, module)
    cmd_transpile(domain, module)
    cmd_test(domain, module)


def cmd_new_domain(domain):
    base = ROOT / DOMAINS_DIR / domain
    for d in [base / "input" / "policy_docs", base / "specs", base / "output"]:
        d.mkdir(parents=True, exist_ok=True)
    _print_ok(f"{base}/")
    _print_info(f"  input/policy_docs/    ← add .md policy documents here")
    _print_info(f"  specs/")
    _print_info(f"  output/               ← generated Catala or Rego files and demo folder(s)")
    _print_info(
        f"\nDomain '{domain}' created. "
        f"Next: add policy docs to {base}/input/policy_docs/, then run /index-inputs."
    )


def cmd_preflight(domain, module, backend):
    domain_dir = ROOT / DOMAINS_DIR / domain
    if not domain_dir.exists():
        _print_err(f"Domain not found: {domain_dir}/")
        sys.exit(1)
    paths = resolve_paths(domain, module)
    require_file(paths["civil"], "CIVIL spec")
    if backend == "rego" and shutil.which("opa") is None:
        _print_err("opa not found in PATH. Install OPA to run Rego tests (`./xlator setup`).")
        sys.exit(1)
    if backend == "catala" and shutil.which("clerk") is None:
        _print_err("clerk not found in PATH. Install the Catala toolchain to run tests.")
        sys.exit(1)
    label = f"{domain}/{module}" + (f" [{backend}]" if backend else "")
    _print_ok(f"preflight passed: {label}")


def cmd_manifest_update(domain):
    mpath = _manifest_path(domain)
    if not mpath.exists():
        _print_err(f"Extraction manifest not found: {mpath.relative_to(ROOT)}")
        sys.exit(1)
    with open(mpath) as f:
        manifest = yaml.safe_load(f)

    def refresh(source_docs):
        updated = []
        for entry in source_docs:
            domain_rel, _ = _parse_source_doc(entry, domain)
            sha = _get_file_sha(f"{DOMAINS_DIR}/{domain}/{domain_rel}")
            if sha is None:
                _print_info(f"    [dim]dropped[/dim] {domain_rel} (no longer in git)")
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
    _print_ok(f"manifest updated: {mpath.relative_to(ROOT)}")


def cmd_detect_changes(domain):
    """Exit 0 = no changes (nothing to do). Exit 1 = changes detected.

    Compares git SHA values stored in extraction-manifest.yaml against the current
    HEAD SHA for each source document. Only committed changes are detected — this
    intentionally matches the pipeline's behaviour of tracking committed policy versions.
    """
    mpath = _manifest_path(domain)
    if not mpath.exists():
        _print_err(f"Extraction manifest not found: {mpath.relative_to(ROOT)}")
        sys.exit(1)
    with open(mpath) as f:
        manifest = yaml.safe_load(f)

    def has_changes(source_docs):
        for entry in source_docs:
            domain_rel, stored_sha = _parse_source_doc(entry, domain)
            current = _get_file_sha(f"{DOMAINS_DIR}/{domain}/{domain_rel}")
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


def cmd_diff_report(domain):
    from tools.diff_report import run as _diff_report
    _diff_report(domain)


def cmd_tidy_log(domain):
    from tools.tidy_obs_log import run as _tidy_obs_log
    _tidy_obs_log(domain)


def cmd_list():
    pattern = str(ROOT / DOMAINS_DIR / "*" / "specs" / "*.civil.yaml")
    module_rows = []
    domains_with_modules = set()
    for path in sorted(glob.glob(pattern)):
        parts = Path(path).parts
        domain = parts[-3]
        module = parts[-1].removesuffix(".civil.yaml")
        module_rows.append((domain, module))
        domains_with_modules.add(domain)

    exclude_domains = {".venv", "guidance-templates"}
    domain_dirs = sorted(p.name for p in (ROOT / DOMAINS_DIR).iterdir() if p.is_dir() and p.name not in exclude_domains)
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
  ./xlator list
  ./xlator validate snap eligibility
  ./xlator rego-pipeline snap eligibility
  ./xlator catala-test snap eligibility
  ./xlator catala-pipeline snap eligibility
  ./xlator rego-test ak_doh apa_adltc
        """,
    )
    sub = parser.add_subparsers(dest="action", required=True, metavar="action")

    for action, help_text in [
        ("validate",              "Validate CIVIL YAML"),
        ("rego-transpile",        "Generate Rego from CIVIL"),
        ("catala-transpile",      "Generate Catala from CIVIL"),
        ("catala-test-transpile", "Generate Catala test file from YAML tests"),
        ("catala-test",           "Run Catala tests via clerk test"),
        ("rego-test",             "Start OPA, run tests, stop OPA"),
        ("rego-demo",             "Start OPA + FastAPI demo (foreground)"),
        ("catala-demo",           "Start Catala-Python demo (foreground)"),
        ("graph",                 "Generate computation graph"),
        ("catala-pipeline",       "validate -> catala-transpile -> catala-test-transpile -> catala-test"),
        ("rego-pipeline",         "validate -> rego-transpile -> rego-test (OPA/Rego)"),
    ]:
        p = sub.add_parser(action, help=help_text)
        p.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
        p.add_argument("module", help="Module name (e.g. eligibility, apa_adltc)")

    sub.add_parser("list",            help="Show all domain/module pairs")

    # Domain-only subcommands (no module arg)
    for action, help_text in [
        ("new-domain",      "Scaffold standard domain directory structure"),
        ("manifest-update", "Refresh git SHAs in extraction-manifest.yaml"),
        ("detect-changes",  "Exit 0 if no source doc changes; exit 1 if changes detected"),
        ("diff-report",     "Show AI-vs-user diffs from session log (read-only)"),
        ("tidy-log",        "Render session log as Markdown conversation"),
    ]:
        p = sub.add_parser(action, help=help_text)
        p.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")

    # Preflight: domain + module + optional backend
    p_pre = sub.add_parser("preflight", help="Validate domain, module, and tool prerequisites")
    p_pre.add_argument("domain", help="Domain name (e.g. snap, ak_doh)")
    p_pre.add_argument("module", help="Module name (e.g. eligibility)")
    p_pre.add_argument(
        "--backend", choices=["rego", "catala"], default=None,
        help="Also check that the backend tool (opa/clerk) is in PATH",
    )

    # CSV test case authoring
    p_ett = sub.add_parser("export-test-template",
                           help="Generate CSV template from CIVIL spec")
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
        case "validate":
            cmd_validate(args.domain, args.module)
        case "rego-transpile":
            cmd_transpile(args.domain, args.module)
        case "catala-transpile":
            cmd_catala_transpile(args.domain, args.module)
        case "catala-test-transpile":
            cmd_catala_test_transpile(args.domain, args.module)
        case "catala-test":
            cmd_catala_test(args.domain, args.module)
        case "rego-test":
            cmd_test(args.domain, args.module)
        case "rego-demo":
            cmd_demo(args.domain, args.module, "rego")
        case "catala-demo":
            cmd_demo(args.domain, args.module, "catala")
        case "graph":
            cmd_graph(args.domain, args.module)
        case "catala-pipeline":
            cmd_catala_pipeline(args.domain, args.module)
        case "rego-pipeline":
            cmd_pipeline(args.domain, args.module)
        case "list":
            cmd_list()
        case "new-domain":
            cmd_new_domain(args.domain)
        case "preflight":
            cmd_preflight(args.domain, args.module, args.backend)
        case "manifest-update":
            cmd_manifest_update(args.domain)
        case "detect-changes":
            cmd_detect_changes(args.domain)
        case "diff-report":
            cmd_diff_report(args.domain)
        case "tidy-log":
            cmd_tidy_log(args.domain)
        case "export-test-template":
            out = args.output_dir or str(ROOT / DOMAINS_DIR / args.domain / "specs" / "tests")
            run([sys.executable, str(SCRIPT_DIR_TOOLS / "export_test_template.py"),
                 str(resolve_paths(args.domain, args.module)["civil"]),
                 "--output-dir", out])
        case "export-test-cases":
            out = args.output_dir or str(ROOT / DOMAINS_DIR / args.domain / "specs" / "tests")
            tf = args.test_file or str(resolve_paths(args.domain, args.module)["tests"])
            run([sys.executable, str(SCRIPT_DIR_TOOLS / "export_test_cases.py"),
                 str(resolve_paths(args.domain, args.module)["civil"]), tf,
                 "--output-dir", out])
        case "import-tests":
            if args.test_file:
                tf = args.test_file
            elif args.input != "-":
                tests_dir = ROOT / DOMAINS_DIR / args.domain / "specs" / "tests"
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
            run([sys.executable, str(SCRIPT_DIR_TOOLS / "import_tests.py"),
                 str(resolve_paths(args.domain, args.module)["civil"]),
                 args.input, tf, *extra])


if __name__ == "__main__":
    main()
