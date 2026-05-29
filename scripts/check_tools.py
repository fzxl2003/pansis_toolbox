#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"


def main() -> int:
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_prefixes: set[str] = set()

    for tool_dir in sorted(path for path in TOOLS_DIR.iterdir() if path.is_dir()):
        manifest_path = tool_dir / "manifest.json"
        if not manifest_path.exists():
            continue

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{tool_dir.name}: invalid JSON: {exc}")
            continue

        tool_id = manifest.get("id")
        api_prefix = manifest.get("api", {}).get("prefix")
        frontend_entry = manifest.get("entry", {}).get("frontend")
        backend_entry = manifest.get("entry", {}).get("backend")

        if not tool_id:
            errors.append(f"{tool_dir.name}: missing id")
        elif tool_id in seen_ids:
            errors.append(f"{tool_dir.name}: duplicate id {tool_id}")
        else:
            seen_ids.add(tool_id)

        if not api_prefix:
            errors.append(f"{tool_dir.name}: missing api.prefix")
        elif api_prefix in seen_prefixes:
            errors.append(f"{tool_dir.name}: duplicate api.prefix {api_prefix}")
        else:
            seen_prefixes.add(api_prefix)

        for field_name, entry in (("entry.frontend", frontend_entry), ("entry.backend", backend_entry)):
            if not entry:
                errors.append(f"{tool_dir.name}: missing {field_name}")
            elif not (tool_dir / entry).exists():
                errors.append(f"{tool_dir.name}: {field_name} not found at {entry}")

    if errors:
        print("Tool check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Tool check passed. {len(seen_ids)} tool(s) discovered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
