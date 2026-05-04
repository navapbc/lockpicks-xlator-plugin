#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pyyaml>=6.0"]
# ///
"""
Move low-quality markdown files from input/policy_docs/ to input/rejected/.

Reads specs/input-index.yaml, finds files whose md_quality.score is below the
given threshold, and moves each one to input/rejected/ preserving its subdirectory
structure relative to input/policy_docs/.

Usage:
    xlator reject-low-quality-inputs <domain-dir> <threshold>

Arguments:
    domain-dir   Absolute path to the domain directory (contains specs/, input/)
    threshold    Integer score; files strictly below this value are moved

Output (JSON):
    {"moved": N, "files": ["input/policy_docs/...", ...]}

Exit codes:
    0 — success (even if no files were moved)
    1 — error (missing index, unreadable file, etc.)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

_POLICY_DOCS_PREFIX = 'input/policy_docs/'
_REJECTED_PREFIX = 'input/rejected/'


def reject_low_quality(domain_dir: Path, threshold: int) -> dict[str, object]:
    index_path = domain_dir / 'specs' / 'input-index.yaml'
    if not index_path.exists():
        raise FileNotFoundError(f'Index not found: {index_path}')

    with index_path.open(encoding='utf-8') as f:
        data = yaml.safe_load(f)

    files: dict[str, object] = (data or {}).get('files') or {}
    moved: list[str] = []

    for rel_path, info in files.items():
        if not isinstance(info, dict):
            continue
        quality = info.get('md_quality')
        if not isinstance(quality, dict):
            continue
        score = quality.get('score')
        if not isinstance(score, int) or score >= threshold:
            continue

        src = domain_dir / rel_path
        if not src.exists():
            continue

        # Map input/policy_docs/<sub> → input/rejected/<sub>
        try:
            sub = Path(rel_path).relative_to(_POLICY_DOCS_PREFIX.rstrip('/'))
        except ValueError:
            continue  # not under input/policy_docs/ — skip

        dst = domain_dir / _REJECTED_PREFIX / sub
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        moved.append(rel_path)

    return {'moved': len(moved), 'files': moved}


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Move low-quality markdown files to input/rejected/.'
    )
    parser.add_argument('domain_dir', help='Absolute path to the domain directory')
    parser.add_argument('threshold', type=int, help='Reject files with score strictly below this')
    args = parser.parse_args()

    domain_dir = Path(args.domain_dir)
    if not domain_dir.is_dir():
        print(f'Error: not a directory: {domain_dir}', file=sys.stderr)
        sys.exit(1)

    try:
        result = reject_low_quality(domain_dir, args.threshold)
    except (OSError, yaml.YAMLError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result))


if __name__ == '__main__':
    main()
