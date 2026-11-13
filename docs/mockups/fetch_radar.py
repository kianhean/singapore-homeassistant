# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Download the current NEA rain radar images used by the README radar image.

uv run docs/mockups/fetch_radar.py
"""

import json
import pathlib
import urllib.request

FEED = "https://api-open.data.gov.sg/v2/real-time/api/weather-radar-images/{range}"
RANGES = ("70km", "240km", "480km")
OUT = pathlib.Path(__file__).parent / "radar"
# data.gov.sg answers 403 to urllib's default User-Agent.
HEADERS = {"User-Agent": "singapore-homeassistant-mockups"}


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=20) as resp:
        return resp.read()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for radar_range in RANGES:
        payload = json.loads(_get(FEED.format(range=radar_range)))
        record = payload["data"]["records"][0]
        (OUT / f"{radar_range}.png").write_bytes(_get(record["image"]["url"]))
        print(radar_range, record["timestamp"])


if __name__ == "__main__":
    main()
