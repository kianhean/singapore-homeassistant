"""Data coordinator for SP Services household electricity and water usage."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from sp_services import (
    SessionExpiredError,
    SessionState,
    SpServicesClient,
    SpServicesError,
    UsageData,
    UsagePoint,
)

from .const import (
    CONF_ACCOUNT_NO,
    CONF_IMPORT_STATISTICS,
    CONF_SESSION,
    DOMAIN,
)
from .sp_statistics import (
    SP_TIMEZONE,
    async_import_usage_statistics,
    parse_period_date,
)

_LOGGER = logging.getLogger(__name__)

# One fetch makes up to six requests to SP's private API, and SP updates the
# data slowly. Polling every 30 minutes also renews the Auth0 session (3 days
# from last use) long before it ends.
UPDATE_INTERVAL = timedelta(minutes=30)

ISSUE_SESSION_EXPIRED: Final = "sp_session_expired"


@dataclass
class SpUsageData:
    """Usage derived for one SP Services account.

    ``usage`` keeps the raw history the values were derived from, for the
    statistics import.
    """

    account_no: str
    electricity_today: float | None
    electricity_this_month: float | None
    electricity_last_month: float | None
    water_this_month: float | None
    water_last_month: float | None
    usage: UsageData


def _now() -> datetime:
    return datetime.now(SP_TIMEZONE)


def _value_for_day(points: list[UsagePoint] | None, day: date) -> float | None:
    for point in points or []:
        if point.value is not None and parse_period_date(point.period) == day:
            return point.value
    return None


def _value_for_month(
    points: list[UsagePoint] | None, year: int, month: int
) -> float | None:
    for point in points or []:
        parsed = parse_period_date(point.period)
        if (
            point.value is not None
            and parsed is not None
            and (parsed.year, parsed.month) == (year, month)
        ):
            return point.value
    return None


def _sum_for_day(points: list[UsagePoint] | None, day: date) -> float | None:
    values = [
        point.value
        for point in points or []
        if point.value is not None and parse_period_date(point.period) == day
    ]
    return round(sum(values), 3) if values else None


def _sum_daily_for_month(
    points: list[UsagePoint] | None, year: int, month: int, last_day: date
) -> float | None:
    """Sum daily points for a month, only when every day up to ``last_day`` is there."""
    by_day: dict[date, float] = {}
    for point in points or []:
        parsed = parse_period_date(point.period)
        if (
            point.value is not None
            and parsed
            and (parsed.year, parsed.month)
            == (
                year,
                month,
            )
        ):
            by_day[parsed] = point.value
    expected = {date(year, month, d) for d in range(1, last_day.day + 1)}
    if not expected or not expected.issubset(by_day):
        return None
    return round(sum(by_day[d] for d in expected), 3)


def derive_usage(usage: UsageData, now: datetime) -> SpUsageData:
    """Work out today / this month / last month from SP's history series.

    SP's own summary fields are often null even when the history is complete,
    so they are only a fallback here.
    """
    today = now.astimezone(SP_TIMEZONE).date()
    first_of_month = today.replace(day=1)
    last_month_end = first_of_month - timedelta(days=1)

    electricity_today = _value_for_day(usage.electricity_daily_history, today)
    if electricity_today is None:
        electricity_today = _sum_for_day(usage.electricity_hourly_history, today)
    if electricity_today is None:
        electricity_today = usage.electricity_today_kwh

    electricity_this_month = _value_for_month(
        usage.electricity_monthly_history, today.year, today.month
    )
    if electricity_this_month is None:
        electricity_this_month = _sum_daily_for_month(
            usage.electricity_daily_history, today.year, today.month, today
        )
    if electricity_this_month is None:
        electricity_this_month = usage.electricity_month_kwh

    electricity_last_month = _value_for_month(
        usage.electricity_monthly_history, last_month_end.year, last_month_end.month
    )
    if electricity_last_month is None:
        electricity_last_month = _sum_daily_for_month(
            usage.electricity_daily_history,
            last_month_end.year,
            last_month_end.month,
            last_month_end,
        )
    if electricity_last_month is None:
        electricity_last_month = usage.electricity_last_month_kwh

    water_this_month = _value_for_month(
        usage.water_monthly_history, today.year, today.month
    )
    if water_this_month is None:
        water_this_month = usage.water_month_m3

    water_last_month = _value_for_month(
        usage.water_monthly_history, last_month_end.year, last_month_end.month
    )
    if water_last_month is None:
        water_last_month = usage.water_last_month_m3

    return SpUsageData(
        account_no=usage.account_no or "",
        electricity_today=electricity_today,
        electricity_this_month=electricity_this_month,
        electricity_last_month=electricity_last_month,
        water_this_month=water_this_month,
        water_last_month=water_last_month,
        usage=usage,
    )


class SpServicesCoordinator(DataUpdateCoordinator[SpUsageData]):
    """Fetches usage for one SP Services account (one config subentry).

    SP's Auth0 tenant issues no refresh token. The subentry stores the Auth0
    session cookies instead, and each expired access token (1 hour) is
    replaced with ``renew_silently()``. Every renewal moves the session's
    3-day expiry forward, so the refreshed session is written back.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        subentry: ConfigSubentry,
    ) -> None:
        self.subentry_id = subentry.subentry_id
        self.account_no: str = subentry.data[CONF_ACCOUNT_NO]
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"SP Services {self.account_no}",
            update_interval=UPDATE_INTERVAL,
        )
        self._session: str = subentry.data[CONF_SESSION]
        self.import_statistics: bool = subentry.data.get(CONF_IMPORT_STATISTICS, True)
        self._client = _client_for(self._session)

    @property
    def token_expires_at(self) -> datetime | None:
        tokens = self._client.token_set if self._client else None
        return tokens.expires_at if tokens else None

    async def async_subentry_updated(self, subentry: ConfigSubentry) -> None:
        """Pick up a reconfigured subentry (new login or changed options)."""
        self.import_statistics = subentry.data.get(CONF_IMPORT_STATISTICS, True)
        session = subentry.data[CONF_SESSION]
        if session == self._session:
            return
        # A new login: drop the old client and its access token.
        await self.async_close()
        self._session = session
        self._client = _client_for(session)
        await self.async_request_refresh()

    async def async_close(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def _async_update_data(self) -> SpUsageData:
        try:
            usage = await self._async_fetch_usage()
        except SessionExpiredError as err:
            self._async_create_session_issue()
            raise UpdateFailed(
                f"SP Services session for account {self.account_no} has ended; "
                "log in again by reconfiguring the account"
            ) from err
        except SpServicesError as err:
            raise UpdateFailed(f"Error fetching SP Services usage: {err}") from err

        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        data = derive_usage(usage, _now())
        if self.import_statistics:
            await async_import_usage_statistics(self.hass, self.account_no, usage)
        return data

    async def _async_fetch_usage(self) -> UsageData:
        if self._client is None:
            raise SessionExpiredError("stored SP Services session is not valid")
        token = await self._async_access_token()
        try:
            return await self._client.fetch_usage(token, account_no=self.account_no)
        except SessionExpiredError:
            # SP rejected a token we thought was still valid; renew once.
            token = await self._async_access_token(force=True)
            return await self._client.fetch_usage(token, account_no=self.account_no)

    async def _async_access_token(self, *, force: bool = False) -> str:
        tokens = self._client.token_set
        if tokens is not None and not force and not tokens.is_expired():
            return tokens.access_token

        tokens = await self._client.renew_silently()
        _LOGGER.debug("Renewed SP Services access token for %s", self.account_no)
        self._async_persist_session(self._client.export_session().to_string())
        return tokens.access_token

    def _async_persist_session(self, session: str) -> None:
        if session == self._session:
            return
        self._session = session
        entry = self.config_entry
        subentry = entry.subentries.get(self.subentry_id)
        if subentry is None:
            return
        self.hass.config_entries.async_update_subentry(
            entry, subentry, data={**subentry.data, CONF_SESSION: session}
        )

    @property
    def _issue_id(self) -> str:
        return f"{ISSUE_SESSION_EXPIRED}_{self.subentry_id}"

    def _async_create_session_issue(self) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_SESSION_EXPIRED,
            translation_placeholders={"account_no": self.account_no},
        )


def _client_for(session: str) -> SpServicesClient | None:
    try:
        state = SessionState.from_string(session)
    except ValueError:
        _LOGGER.error("Stored SP Services session is not valid; log in again")
        return None
    return SpServicesClient(session_state=state)
