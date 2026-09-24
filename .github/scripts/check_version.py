#!/usr/bin/env python3
"""Fail when the plugin manifests and CHANGELOG.md disagree on the version.

The version lives in three places: .claude-plugin/plugin.json,
.codex-plugin/plugin.json, and the top entry of CHANGELOG.md. A release that
bumps one and forgets another ships a plugin whose installed version does not
match its notes.

Usage:  python3 .github/scripts/check_version.py
Exit:   0 = consistent, 1 = mismatch, 2 = could not run
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
CHANGELOG_ENTRY = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.M)


def main(argv):
    found = {}
    for rel in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        try:
            found[rel] = json.loads((REPO / rel).read_text()).get("version")
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read {rel}: {exc}", file=sys.stderr)
            return 2
    try:
        m = CHANGELOG_ENTRY.search((REPO / "CHANGELOG.md").read_text())
    except OSError as exc:
        print(f"error: cannot read CHANGELOG.md: {exc}", file=sys.stderr)
        return 2
    found["CHANGELOG.md"] = m.group(1) if m else None

    failed = False
    for where, v in found.items():
        if not isinstance(v, str) or not SEMVER.match(v):
            print(f"FAIL  {where}: version {v!r} is not X.Y.Z")
            failed = True
    if len(set(found.values())) != 1:
        print("FAIL  versions disagree:")
        for where, v in found.items():
            print(f"        {where}: {v}")
        failed = True
    if not failed:
        print(f"version {next(iter(found.values()))} consistent across {len(found)} sources")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
