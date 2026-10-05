#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    cmd = [
        sys.executable, "-X", "utf8",
        str(ROOT / "apps" / "console" / "main.py"),
        "--host", args.host,
        "--port", str(args.port),
    ]
    proc = subprocess.Popen(cmd, cwd=ROOT)
    url = f"http://127.0.0.1:{args.port}"
    for _ in range(40):
        try:
            urllib.request.urlopen(url + "/api/health", timeout=0.3).read()
            if not args.no_browser:
                webbrowser.open(url)
            break
        except Exception:
            time.sleep(0.15)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
