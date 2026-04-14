"""Print the install path of the xl plugin from `claude plugin list --json` output (stdin)."""
import json, sys

plugins = json.load(sys.stdin)
xl = next((p for p in plugins if p["id"].startswith("xl@")), None)
print(xl["installPath"] if xl else "", end="")
