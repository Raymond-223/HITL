from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.console.backend.server import GroundHTTPServer  # noqa: E402
from mars_ground_station import GroundStationService  # noqa: E402


def load_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def lan_urls(port: int) -> list[str]:
    """Return usable IPv4 URLs without sending any network traffic."""
    addresses: set[str] = set()
    try:
        for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = str(row[4][0])
            if address and not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return [f"http://{address}:{port}" for address in sorted(addresses)]


def main() -> int:
    parser = argparse.ArgumentParser(description="MARS Rover real-vehicle human-in-the-loop ground station")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=str(ROOT / "config" / "team_config.json"))
    parser.add_argument("--workspace", default=str(ROOT / "runtime"))
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    host = str(args.host or cfg.get("listen_host") or "0.0.0.0")
    port = int(args.port or cfg.get("port") or 8080)
    service = GroundStationService(
        Path(args.workspace),
        team_id=str(cfg.get("team_id") or "team-a"),
        stale_after_s=float(cfg.get("stale_after_s") or 3.0),
    )
    server = GroundHTTPServer((host, port), ROOT / "apps" / "console" / "frontend", service)
    print(f"MARS Ground Station (local): http://127.0.0.1:{port}")
    for url in lan_urls(port):
        print(f"MARS Ground Station (LAN):   {url}")
    if host not in {"0.0.0.0", "::"}:
        print(f"Listening only on {host}; set listen_host to 0.0.0.0 for LAN access")
    print(f"Team filter: {service.team_id}; real-rover data only; no simulation/global truth")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
