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
CAMERAS = ("2701", "2702", "4703", "4713")
OUT = pathlib.Path(__file__).parent / "cameras"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with urllib.request.urlopen(FEED, timeout=20) as resp:
        cameras = json.load(resp)["items"][0]["cameras"]
    for cam in cameras:
        if cam["camera_id"] not in CAMERAS:
            continue
        with urllib.request.urlopen(cam["image"], timeout=20) as resp:
            image = Image.open(io.BytesIO(resp.read()))
        image.thumbnail((960, 540))
        image.convert("RGB").save(OUT / f"{cam['camera_id']}.jpg", quality=85)
        print(cam["camera_id"], cam["timestamp"])


if __name__ == "__main__":
    main()
