#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
OUT_PATH = ROOT / "frontend/src/registry/generatedToolViews.ts"


def main() -> None:
    lines = [
        "import { lazy } from 'react';",
        "",
        "export const generatedToolViews = {",
    ]

    for tool_dir in sorted(path for path in TOOLS_DIR.iterdir() if path.is_dir()):
        manifest_path = tool_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not manifest.get("enabled", True):
            continue
        tool_id = manifest["id"]
        frontend_entry = manifest["entry"]["frontend"]
        if not (tool_dir / frontend_entry).exists():
            continue
        import_path = f"../../../tools/{tool_dir.name}/{frontend_entry.removesuffix('.tsx').removesuffix('.ts')}"
        lines.append(f"  {json.dumps(tool_id)}: lazy(() => import({json.dumps(import_path)})),")

    lines.extend([
        "} as const;",
        "",
        "export type GeneratedToolId = keyof typeof generatedToolViews;",
        "",
    ])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Generated {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
