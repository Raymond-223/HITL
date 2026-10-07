import json
import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, _tag, attrs):
        self.ids.extend(value for key, value in attrs if key == "id")


def test_dashboard_contains_only_connected_rover_controls():
    html = (ROOT / "apps/console/frontend/index.html").read_text(encoding="utf-8")
    css = (ROOT / "apps/console/frontend/assets/style.css").read_text(encoding="utf-8")
    for element_id in (
        "mapCanvas", "map2dButton", "map3dButton", "cameraFrame", "startSearch",
        "pauseCommand", "resumeCommand", "returnCommand", "estopCommand", "healthGrid",
    ):
        assert f'id="{element_id}"' in html
    for removed in ("实验记录", "模型对照", "附加参数 JSON", "信息边界"):
        assert removed not in html
    for page in ("devices", "overview", "map", "perception", "mission", "diagnostics"):
        assert f'data-page-panel="{page}"' in html
    assert "#0b1118" not in css
    assert "--green:#2f6d55" in css
    assert "@media" not in css
    assert "min-width:1180px" in css
    assert 'id="menuButton"' not in html
    assert 'id="sidebarBackdrop"' not in html


def test_javascript_ids_and_lan_configuration_are_consistent():
    html = (ROOT / "apps/console/frontend/index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "apps/console/frontend/assets/app.js").read_text(encoding="utf-8")
    parser = IdParser()
    parser.feed(html)
    assert len(parser.ids) == len(set(parser.ids))
    referenced = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", javascript))
    assert referenced <= set(parser.ids)
    assert "function drawMap3d" in javascript
    config = json.loads((ROOT / "config/team_config.json").read_text(encoding="utf-8"))
    assert config["listen_host"] == "0.0.0.0"
    assert config["port"] == 8080
    assert config["discovery_port"] == 38765
    assert config["agent_port"] == 38766
    assert "data-connect-rover" in javascript


def test_repository_has_one_documented_startup_entry():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    entry = ROOT / "START_MARS_ROVER.py"
    assert entry.is_file()
    assert entry.stat().st_mode & 0o111
    assert "./START_MARS_ROVER.py" in readme
    for removed in (
        "START_MARS_ROVER.sh",
        "START_MARS_ROVER.bat",
        "START_MARS_ROVER_LAN.bat",
        "scripts/windows_launcher.py",
    ):
        assert not (ROOT / removed).exists()
        assert removed not in readme
