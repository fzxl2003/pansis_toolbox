#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    commands = [
        ["uvicorn", "backend.app.main:app", "--reload", "--port", "8000"],
        ["npm", "--prefix", "frontend", "run", "dev"],
    ]
    processes = [subprocess.Popen(command, cwd=ROOT) for command in commands]
    try:
        return next(process.wait() for process in processes)
    except KeyboardInterrupt:
        return 130
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()


if __name__ == "__main__":
    sys.exit(main())
