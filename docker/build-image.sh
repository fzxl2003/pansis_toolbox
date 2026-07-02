#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

IMAGE_NAME=${IMAGE_NAME:-pansis-toolbox}
IMAGE_TAG=${IMAGE_TAG:-latest}

printf 'Building %s:%s from %s\n' "$IMAGE_NAME" "$IMAGE_TAG" "$ROOT_DIR"
python3 - "$ROOT_DIR" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
tools = []
for manifest_path in sorted((root / "tools").glob("*/manifest.json")):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("enabled", True):
        continue
    deps = manifest.get("dependencies", {})
    node_packages = [
        str(path.relative_to(root))
        for path in sorted(manifest_path.parent.glob("**/package.json"))
        if "node_modules" not in path.parts
    ]
    tools.append((manifest.get("id", manifest_path.parent.name), deps, node_packages))

if not tools:
    print("No enabled tools found under tools/.")
else:
    print("Enabled tools included in this image:")
    for tool_id, deps, node_packages in tools:
        dep_text = ", ".join(f"{name}{version}" for name, version in sorted(deps.items())) or "no Python deps"
        node_text = ", ".join(node_packages) or "no Node package"
        print(f"  - {tool_id}: {dep_text}; {node_text}")
PY

if [ -n "${PLATFORM:-}" ]; then
    set -- --platform "$PLATFORM"
else
    set --
fi

docker build "$@" -f "$ROOT_DIR/docker/Dockerfile" -t "$IMAGE_NAME:$IMAGE_TAG" "$ROOT_DIR"
