#!/usr/bin/env python3
"""Single entry point for the MARS rover supervision platform."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config" / "team_config.json"
DIRECT_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def lan_urls(port: int) -> list[str]:
    addresses: set[str] = set()
    try:
        for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = str(row[4][0])
            if address and not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    # Hostname lookup commonly resolves only to 127.0.1.1 on Ubuntu.  A UDP
    # connect selects the active interface without transmitting a packet.
    for target in (("239.255.73.84", port), ("192.0.2.1", 9)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                route.connect(target)
                address = str(route.getsockname()[0])
                if address and not address.startswith("127."):
                    addresses.add(address)
        except OSError:
            continue
    return [f"http://{address}:{port}" for address in sorted(addresses)]


def wait_for_server(port: int, process: subprocess.Popen) -> None:
    url = f"http://127.0.0.1:{port}/api/health"
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError(f"platform server exited with code {process.returncode}")
        try:
            with DIRECT_HTTP.open(url, timeout=0.25) as response:
                if json.load(response).get("ready"):
                    return
        except (OSError, json.JSONDecodeError):
            time.sleep(0.1)
    raise RuntimeError("platform server did not become ready")


def start_gateway(config: dict, environment: dict[str, str]) -> subprocess.Popen | None:
    gateway = ROOT / "scripts" / "ros2_team_gateway.py"
    arguments = [
        str(gateway),
        "--team-id", str(config.get("team_id") or "team-a"),
        "--console", f"http://127.0.0.1:{int(config.get('port') or 8080)}",
        "--discovery-port", str(int(config.get("discovery_port") or 38765)),
        "--agent-port", str(int(config.get("agent_port") or 38766)),
    ]
    ros_setup = Path("/opt/ros/humble/setup.bash")
    if ros_setup.is_file() and os.name != "nt":
        command = "source " + shlex.quote(str(ros_setup)) + " && exec "
        command += " ".join(shlex.quote(value) for value in [sys.executable, *arguments])
        return subprocess.Popen(
            ["bash", "-lc", command],
            cwd=ROOT,
            env=environment,
            start_new_session=True,
        )
    if importlib.util.find_spec("rclpy") is not None:
        return subprocess.Popen(
            [sys.executable, *arguments],
            cwd=ROOT,
            env=environment,
            start_new_session=os.name != "nt",
        )
    return None


def terminate(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the MARS rover supervision platform")
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    config = load_config()
    host = str(config.get("listen_host") or "0.0.0.0")
    port = int(config.get("port") or 8080)
    environment = os.environ.copy()
    environment["ROS_DOMAIN_ID"] = str(config.get("ros_domain_id") or 21)
    python_path = [str(ROOT / "src"), str(ROOT)]
    if environment.get("PYTHONPATH"):
        python_path.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(python_path)

    server = subprocess.Popen([
        sys.executable,
        str(ROOT / "apps" / "console" / "main.py"),
        "--host", host,
        "--port", str(port),
    ], cwd=ROOT, env=environment, start_new_session=os.name != "nt")
    gateway: subprocess.Popen | None = None
    try:
        wait_for_server(port, server)
        gateway = start_gateway(config, environment)
        print("\n小车监督平台已启动")
        print(f"本机入口: http://127.0.0.1:{port}")
        for url in lan_urls(port):
            print(f"局域网入口: {url}")
        print("ROS2 网关: " + ("已自动启动" if gateway is not None else "由局域网小车网关提供"))
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{port}")
        while server.poll() is None:
            if gateway is not None and gateway.poll() is not None:
                print(f"ROS2 网关已停止（退出码 {gateway.returncode}），Web 平台继续运行")
                gateway = None
            time.sleep(0.5)
        return int(server.returncode or 0)
    except KeyboardInterrupt:
        return 0
    finally:
        terminate(gateway)
        terminate(server)


if __name__ == "__main__":
    raise SystemExit(main())
