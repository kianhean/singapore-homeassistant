# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow"]
# ///
"""Download the current Woodlands/Tuas checkpoint stills used by the README images.

uv run docs/mockups/fetch_cameras.py
"""

import io
import json
import pathlib
import urllib.request

from PIL import Image

FEED = "https://api.data.gov.sg/v1/transport/traffic-images"
CAMERAS = ("2701", "2702", "2704", "4703", "4712", "4713", "4798", "4799")
OUT = pathlib.Path(__file__).parent / "cameras"
HEADERS = {"User-Agent": "singapore-homeassistant"}


def _open(url: str):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=HEADERS), timeout=20
    )


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with _open(FEED) as resp:
        cameras = json.load(resp)["items"][0]["cameras"]
    for cam in cameras:
        if cam["camera_id"] not in CAMERAS:
            continue
        with _open(cam["image"]) as resp:
            image = Image.open(io.BytesIO(resp.read()))
        image.thumbnail((960, 540))
        image.convert("RGB").save(OUT / f"{cam['camera_id']}.jpg", quality=85)
        print(cam["camera_id"], cam["timestamp"])


if __name__ == "__main__":
    main()
