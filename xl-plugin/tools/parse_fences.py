"""
Reference harness for parsing xlator output-fencing blocks.

Parse `:::type` / `:::` delimited blocks from command output and return a list
of {"type": str, "content": str} dicts. Unfenced text becomes type "detail".

Unclosed fence behavior: if the input ends while a fence is still open, the
accumulated content is yielded as a partial block with the declared type. No
error is raised — callers that need strict validation should check for this
by confirming that every block's content ends before an explicit close.
"""

import re
import sys
from typing import Union

_OPEN_RE = re.compile(r"^:::([a-z_]+)$")
_CLOSE_RE = re.compile(r"^:::$")


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

    for block in parse_fences(text):
        print(f"[{block['type']}]")
        print(block["content"])
        print()


if __name__ == "__main__":
    main()
