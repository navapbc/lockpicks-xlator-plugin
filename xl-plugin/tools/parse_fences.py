"""
Reference harness for parsing xlator output-fencing blocks.

Accepts three input forms (auto-detected):
  - Plain text containing :::type / ::: fences
  - claude --output-format json  (single JSON object; text in .result)
  - claude --output-format stream-json  (NDJSON; text in the final .result)

Usage:
  claude --dangerously-skip-permissions -p --output-format json '/xl:new-domain my_domain' \
    | python xl-plugin/tools/parse_fences.py

Parse `:::type` / `:::` delimited blocks from command output and return a list
of {"type": str, "content": str} dicts. Unfenced text becomes type "detail".

Unclosed fence behavior: if the input ends while a fence is still open, the
accumulated content is yielded as a partial block with the declared type. No
error is raised — callers that need strict validation should check for this
by confirming that every block's content ends before an explicit close.
"""

import json
import re
import sys
from typing import Union

_OPEN_RE = re.compile(r"^:::([a-z_]+)$")
_CLOSE_RE = re.compile(r"^:::$")


def extract_text(raw: str) -> str:
    """Extract plain text from claude --output-format json or stream-json input.

    Falls back to returning raw as-is if it is not JSON.
    """
    raw = raw.strip()
    if not raw.startswith("{"):
        return raw
    # stream-json: multiple newline-delimited objects — find the last "result" object.
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) > 1:
        for line in reversed(lines):
            try:
                obj = json.loads(line)
                if "result" in obj:
                    return obj["result"]
            except json.JSONDecodeError:
                continue
        return raw
    # Single JSON object (--output-format json).
    try:
        return json.loads(raw).get("result", raw)
    except json.JSONDecodeError:
        return raw


def parse_fences(text: str) -> list[dict[str, str]]:
    """Return a list of {"type": str, "content": str} blocks.

    Unfenced lines accumulate as "detail". Empty content blocks are dropped.
    """
    blocks: list[dict[str, str]] = []
    current_type: Union[str, None] = None
    buf: list[str] = []

    def _flush(t: str) -> None:
        content = "\n".join(buf).strip("\n")
        if content:
            blocks.append({"type": t, "content": content})
        buf.clear()

    for line in text.splitlines():
        open_m = _OPEN_RE.match(line)
        close_m = _CLOSE_RE.match(line)

        if open_m and current_type is None:
            _flush("detail")
            current_type = open_m.group(1)
        elif close_m and current_type is not None:
            _flush(current_type)
            current_type = None
        else:
            buf.append(line)

    # Flush any remaining content (unfenced tail or unclosed fence).
    _flush(current_type if current_type is not None else "detail")

    return blocks


def main() -> None:
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    for block in parse_fences(extract_text(text)):
        print(f"[{block['type']}]")
        print(block["content"])
        print()


if __name__ == "__main__":
    main()
