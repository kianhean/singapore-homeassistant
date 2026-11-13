# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow"]
# ///
"""Download the current NEA rain radar and render it the way the camera entities do.

uv run docs/mockups/fetch_radar.py

70 km and 240 km are drawn on NEA's base maps with the integration's own
``radar_image.composite_radar``.
"""

import importlib.util
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
FEED = "https://api-open.data.gov.sg/v2/real-time/api/weather-radar-images/{range}"
# 480 km has no NEA map and its camera is disabled by default, so it isn't shown.
RANGES = ("70km", "240km")
OUT = pathlib.Path(__file__).parent / "radar"
# data.gov.sg answers 403 to urllib's default User-Agent.
HEADERS = {"User-Agent": "singapore-homeassistant-mockups"}


def _load_radar_image():
    # Load the module by path: importing the package would pull in Home Assistant.
    path = ROOT / "custom_components" / "singapore" / "radar_image.py"
    spec = importlib.util.spec_from_file_location("radar_image", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=20) as resp:
        return resp.read()


def main() -> None:
    radar_image = _load_radar_image()
    OUT.mkdir(exist_ok=True)
    for old in OUT.iterdir():
        old.unlink()
    for radar_range in RANGES:
        payload = json.loads(_get(FEED.format(range=radar_range)))
        record = payload["data"]["records"][0]
        overlay = _get(record["image"]["url"])
        spec = radar_image.BASEMAPS[radar_range]
        image = radar_image.composite_radar(_get(spec.url), overlay, spec)
        (OUT / f"{radar_range}.jpg").write_bytes(image)
        print(radar_range, record["timestamp"])


if __name__ == "__main__":
    main()
