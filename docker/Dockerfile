FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm ci --prefix frontend

COPY scripts ./scripts
COPY frontend ./frontend
COPY tools ./tools
RUN python3 scripts/generate_tool_views.py && npm --prefix frontend run build

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    TOOLS_DIR=/app/tools \
    FRONTEND_DIST_DIR=/app/frontend/dist \
    STORAGE_DIR=/app/storage \
    PLATFORM_DB_PATH=/app/storage/data/platform.db \
    WIDGET_LAYOUT_PATH=/app/storage/data/widget_layout.json \
    FRONTEND_ORIGIN=http://localhost:8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates nodejs npm openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY backend ./backend
COPY tools ./tools
COPY scripts ./scripts
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

RUN python - <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

root = Path("/app")
tools_dir = root / "tools"
python_deps: dict[str, str] = {}

for manifest_path in sorted(tools_dir.glob("*/manifest.json")):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("enabled", True):
        continue

    tool_dir = manifest_path.parent
    for name, spec in manifest.get("dependencies", {}).items():
        python_deps[name] = spec

    for package_json in sorted(tool_dir.glob("**/package.json")):
        if "node_modules" in package_json.parts:
            continue
        command = ["npm", "ci"] if (package_json.parent / "package-lock.json").exists() else ["npm", "install"]
        subprocess.run(command + ["--ignore-scripts"], cwd=package_json.parent, check=True)
        if "build" in json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {}):
            subprocess.run(["npm", "run", "build"], cwd=package_json.parent, check=True)

if python_deps:
    specs = [f"{name}{version}" for name, version in sorted(python_deps.items())]
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir", *specs], check=True)
PY

RUN mkdir -p /app/storage/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
