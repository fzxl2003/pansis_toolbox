#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/create_tool.py tool_id")
        return 1

    tool_id = sys.argv[1].strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", tool_id):
        print("tool_id must use lowercase letters, numbers, and underscores")
        return 1

    tool_dir = TOOLS_DIR / tool_id
    if tool_dir.exists():
        print(f"Tool already exists: {tool_id}")
        return 1

    (tool_dir / "backend").mkdir(parents=True)
    (tool_dir / "frontend").mkdir(parents=True)
    (tool_dir / "tests").mkdir(parents=True)

    manifest = {
        "id": tool_id,
        "name": tool_id.replace("_", " ").title(),
        "description": "New toolbox tool.",
        "version": "0.1.0",
        "enabled": True,
        "category": "other",
        "icon": "wrench",
        "entry": {"frontend": "frontend/index.tsx", "backend": "backend/router.py"},
        "api": {"prefix": f"/api/tools/{tool_id.replace('_', '-')}"},
        "widgets": [],
        "dependencies": {},
        "permissions": {"filesystem": False, "network": False, "longRunningTask": False},
    }
    (tool_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (tool_dir / "README.md").write_text(f"# {manifest['name']}\n", encoding="utf-8")
    (tool_dir / "backend/router.py").write_text(
        "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n@router.get('/health')\ndef health() -> dict[str, str]:\n    return {'status': 'ok'}\n",
        encoding="utf-8",
    )
    (tool_dir / "frontend/index.tsx").write_text(
        "export default function ToolView() {\n  return <div className=\"tool-surface\"><h1>New Tool</h1></div>;\n}\n",
        encoding="utf-8",
    )
    print(f"Created {tool_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
