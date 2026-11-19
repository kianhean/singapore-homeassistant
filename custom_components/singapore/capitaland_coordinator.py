"""Data coordinator for CapitaLand mall carpark availability (JustPark).

https://justpark.capitaland.com/Lot-Availability is a Vue page that loads the
lots from its own JSON endpoint: a form POST to ``AjaxNoAuth/OnHttpPost`` with
``JData`` = ``{"DisplayType": "LotAvail", "LotType": null}``, the
``X-APIAction: SelectSiteNoAuth/OnSelectSite`` header, and the ASP.NET
anti-forgery pair the page hands out (the hidden ``__RequestVerificationToken``
input, sent back as a header, plus the cookie of the same name). The answer is
``{"HasError": false, "Result": "<JSON string>"}``; ``Result`` is a list of
sites with ``SiteCode``, ``SiteDesc``, ``BusinessUnit`` (``Retail`` = malls,
``Commercial`` = offices, business parks), ``LotBalance`` and ``LotTotal``
(strings, ``"NA"`` / null when a site reports nothing).

The token and cookies are kept between polls; the page is only loaded again
when they are missing or the endpoint stops answering with JSON.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import SOURCE_CAPITALAND, scaled_interval
from .errors import ERROR_FETCH, ERROR_HTTP, ERROR_PAYLOAD, source_error

_LOGGER = logging.getLogger(__name__)

CAPITALAND_PAGE_URL = "https://justpark.capitaland.com/Lot-Availability"
CAPITALAND_API_URL = "https://justpark.capitaland.com/AjaxNoAuth/OnHttpPost"
_API_ACTION = "SelectSiteNoAuth/OnSelectSite"
_MODULE_ID = "FELotAvail"
_REQUEST = {"DisplayType": "LotAvail", "LotType": None}
_TOKEN_NAME = "__RequestVerificationToken"
_TOKEN_RE = re.compile(
    r'name="__RequestVerificationToken"[^>]*?value="([^"]+)"'
    r'|value="([^"]+)"[^>]*?name="__RequestVerificationToken"'
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-SG,en;q=0.9",
}

# The malls' sites; offices and business parks are other business units.
BUSINESS_UNIT_RETAIL = "Retail"

UPDATE_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class CapitaLandSite:
    """One CapitaLand carpark."""

    code: str
    name: str
    business_unit: str | None
    available: int | None
    total: int | None
    full: bool


@dataclass
class CapitaLandData:
    """Every site the page lists, keyed by site code."""

    sites: dict[str, CapitaLandSite] = field(default_factory=dict)

    def malls(self) -> list[CapitaLandSite]:
        """The malls (Retail sites) that report a lot count."""
        return [
            site
            for site in self.sites.values()
            if site.business_unit == BUSINESS_UNIT_RETAIL and site.available is not None
        ]


def _to_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def extract_token(html: str) -> str | None:
    """The anti-forgery token in the page's hidden form input."""
    if match := _TOKEN_RE.search(html):
        return match.group(1) or match.group(2)
    return None


def _parse_sites(payload: object) -> CapitaLandData:
    """Parse the endpoint's answer (the outer object, ``Result`` still a string)."""
    if not isinstance(payload, dict) or payload.get("HasError"):
        detail = payload.get("Message") if isinstance(payload, dict) else None
        raise source_error(
            ERROR_PAYLOAD, SOURCE_CAPITALAND, detail=detail or "no result"
        )
    result = payload.get("Result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError as err:
            raise source_error(
                ERROR_PAYLOAD, SOURCE_CAPITALAND, detail="Result is not JSON"
            ) from err
    if not isinstance(result, list) or not result:
        raise source_error(ERROR_PAYLOAD, SOURCE_CAPITALAND, detail="no sites")
    sites: dict[str, CapitaLandSite] = {}
    for row in result:
        if not isinstance(row, dict):
            continue
        code = str(row.get("SiteCode") or "").strip()
        if not code:
            continue
        sites[code] = CapitaLandSite(
            code=code,
            name=str(row.get("SiteDesc") or code).strip(),
            business_unit=row.get("BusinessUnit"),
            available=_to_int(row.get("LotBalance")),
            total=_to_int(row.get("LotTotal")),
            full=bool(row.get("IsFull")),
        )
    if not sites:
        raise source_error(ERROR_PAYLOAD, SOURCE_CAPITALAND, detail="no site codes")
    return CapitaLandData(sites)


class _SessionExpired(Exception):
    """The endpoint answered with something other than JSON (stale token)."""


class _HttpError(Exception):
    """The site answered with an error status (transient)."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class CapitaLandCarparkCoordinator(DataUpdateCoordinator[CapitaLandData]):
    """Polls CapitaLand JustPark lot availability."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Singapore CapitaLand Carpark Availability",
            update_interval=scaled_interval(config_entry, UPDATE_INTERVAL),
            always_update=False,
        )
        self._token: str | None = None
        self._cookies: dict[str, str] = {}

    async def _async_new_session(self, session: aiohttp.ClientSession) -> None:
        """Load the page for a fresh anti-forgery token and its cookies."""
        async with session.get(
            CAPITALAND_PAGE_URL,
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status != 200:
                raise _HttpError(response.status)
            html = await response.text()
            cookies = {name: morsel.value for name, morsel in response.cookies.items()}
        if (token := extract_token(html)) is None:
            raise source_error(
                ERROR_PAYLOAD, SOURCE_CAPITALAND, detail="no verification token"
            )
        self._token = token
        self._cookies = cookies

    async def _async_post(self, session: aiohttp.ClientSession) -> object:
        cookie = "; ".join(f"{name}={value}" for name, value in self._cookies.items())
        headers = {
            **_HEADERS,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": CAPITALAND_PAGE_URL,
            "X-APIAction": _API_ACTION,
            "x-ModID": _MODULE_ID,
            _TOKEN_NAME: self._token or "",
        }
        if cookie:
            headers["Cookie"] = cookie
        async with session.post(
            CAPITALAND_API_URL,
            data={"JData": json.dumps(_REQUEST)},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status != 200:
                raise _HttpError(response.status)
            text = await response.text()
        try:
            return json.loads(text)
        except ValueError as err:
            # A rejected token gets the site's HTML error page.
            raise _SessionExpired from err

    async def _async_fetch(self) -> object:
        session = async_get_clientsession(self.hass)
        if self._token is None:
            await self._async_new_session(session)
            fresh = True
        else:
            fresh = False
        try:
            return await self._async_post(session)
        except _SessionExpired:
            if fresh:
                raise source_error(
                    ERROR_PAYLOAD, SOURCE_CAPITALAND, detail="not JSON"
                ) from None
        # The kept token went stale: load the page once more and retry.
        await self._async_new_session(session)
        try:
            return await self._async_post(session)
        except _SessionExpired:
            raise source_error(
                ERROR_PAYLOAD, SOURCE_CAPITALAND, detail="not JSON"
            ) from None

    async def _async_update_data(self) -> CapitaLandData:
        try:
            payload = await self._async_fetch()
        except (aiohttp.ClientError, TimeoutError) as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Error fetching CapitaLand carparks (%s); using last known values",
                    err,
                )
                return self.data
            raise source_error(ERROR_FETCH, SOURCE_CAPITALAND, error=err) from err
        except _HttpError as err:
            if self.data is not None:
                _LOGGER.warning(
                    "CapitaLand carparks returned HTTP %s; using last known values",
                    err.status,
                )
                return self.data
            raise source_error(
                ERROR_HTTP, SOURCE_CAPITALAND, status=err.status
            ) from err
        except UpdateFailed:
            # The page or its answer changed: the next poll starts afresh.
            self._token = None
            raise
        return _parse_sites(payload)
