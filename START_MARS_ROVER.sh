#!/usr/bin/env bash
set -e

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source /opt/ros/humble/setup.bash

readarray -t station_config < <(python3 - "$root_dir/config/team_config.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
print(config.get("team_id", "team-a"))
print(config.get("ros_domain_id", 21))
print(config.get("listen_host", "0.0.0.0"))
print(config.get("port", 8080))
print(config.get("discovery_port", 38765))
PY
)
team_id="${station_config[0]}"
export ROS_DOMAIN_ID="${station_config[1]}"
listen_host="${station_config[2]}"
station_port="${station_config[3]}"
discovery_port="${station_config[4]}"
export PYTHONPATH="$root_dir/src:$root_dir${PYTHONPATH:+:$PYTHONPATH}"

python3 "$root_dir/apps/console/main.py" --host "$listen_host" --port "$station_port" &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT

python3 - "$station_port" <<'PY'
import sys
import time
import urllib.request

url = f"http://127.0.0.1:{sys.argv[1]}/api/health"
for _ in range(50):
    try:
        urllib.request.urlopen(url, timeout=0.2).read()
        break
    except Exception:
        time.sleep(0.1)
else:
    raise SystemExit("ground station failed to start")
PY

echo "MARS Ground Station local: http://127.0.0.1:$station_port"
python3 - "$station_port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
addresses = set()
try:
    for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        address = row[4][0]
        if address and not address.startswith("127."):
            addresses.add(address)
except OSError:
    pass
for address in sorted(addresses):
    print(f"MARS Ground Station LAN:   http://{address}:{port}")
PY
echo "ROS_DOMAIN_ID=$ROS_DOMAIN_ID, team_id=$team_id"
python3 "$root_dir/scripts/ros2_team_gateway.py" \
  --team-id "$team_id" --console "http://127.0.0.1:$station_port" \
  --discovery-port "$discovery_port"
