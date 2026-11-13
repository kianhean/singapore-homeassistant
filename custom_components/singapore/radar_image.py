"""Composite NEA rain radar overlays onto NEA's own base maps.

Pure Pillow (no Home Assistant imports), so ``docs/mockups/fetch_radar.py`` can use the
exact same rendering as the camera entities. Everything here is blocking: call it from
an executor job.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Final

from PIL import Image

NEA_MAP_URL = "https://www.nea.gov.sg/assets/images/map/{name}"


@dataclass(frozen=True)
class Basemap:
    """An NEA rain-areas base map and the part of the radar image it shows.

    ``crop`` is the (left, top, right, bottom) box, in pixels of data.gov.sg's
    480 x 480 radar PNG, that NEA's own overlay for this map shows; ``None`` means
    the whole image.
    """

    name: str
    crop: tuple[int, int, int, int] | None = None

    @property
    def url(self) -> str:
        return NEA_MAP_URL.format(name=self.name)


# Base maps used on https://www.nea.gov.sg/weather/rain-areas. The crop boxes were
# found by matching NEA's own overlays against data.gov.sg's image of the same
# capture: NEA's 70 km overlay (217 x 120) is exactly this crop, and its 240 km
# overlay is the full image. NEA has no map for the 480 km range.
BASEMAPS: Final[dict[str, Basemap]] = {
    "70km": Basemap("base-853.png", crop=(83, 193, 300, 313)),
    "240km": Basemap("240km-v2.jpg"),
}


def composite_radar(basemap: bytes, overlay: bytes, spec: Basemap) -> bytes:
    """Lay a radar overlay on its base map, the way NEA's rain-areas page does.

    The page draws both images at the same width, top-left aligned, so the overlay
    is scaled uniformly to the base map's width. Returns a JPEG.
    """
    with Image.open(io.BytesIO(basemap)) as base_image:
        base = base_image.convert("RGBA")
    with Image.open(io.BytesIO(overlay)) as overlay_image:
        rain = overlay_image.convert("RGBA")
    if spec.crop is not None:
        rain = rain.crop(spec.crop)
    height = round(rain.height * base.width / rain.width)
    rain = rain.resize((base.width, height), Image.Resampling.BILINEAR)
    base.alpha_composite(rain.crop((0, 0, base.width, min(height, base.height))))

    out = io.BytesIO()
    base.convert("RGB").save(out, format="JPEG", quality=90)
    return out.getvalue()
